"""Run the benchmark: one model plays Pokemon Red or Blue through the menu loop.

Each turn the model gets the game state as text with lettered choices and
answers with JSON. The run ends when the target milestone is read from game
memory, when the call cap is reached, or when the model fails to answer.
Ctrl+C pauses the run with an in-game save; run this again to resume it.

Usage:
    python setup.py   (once)
    python run.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from hbtl_pokemon import game as engine  # noqa: E402
from hbtl_pokemon import prompt, runtime  # noqa: E402
from hbtl_pokemon.provider import CallCapReached, Provider, say  # noqa: E402
from hbtl_pokemon.results import (  # noqa: E402
    MILESTONE_LABELS,
    MILESTONES,
    RESULTS,
    RunFolder,
    harness_commit,
    milestones_from_memory,
    paused_runs,
    reached,
)

CONFIG_PATH = ROOT / "config.yaml"
SAVES = ROOT / "saves"
CHECKPOINTS = ROOT / "checkpoints"
RUNS_DIR = runtime.RUNTIME / "runs"

# The context window, as in the paper's constrained harness: the system
# prompt, the last 24 messages, and the current state.
HISTORY_LIMIT = 24
DIALOGUE_LIMIT = 500
# A milestone counts once it reads as reached twice in a row.
COMPLETION_CONFIRMATIONS = 2
# In-game saves, counted in turns.
SAVE_EVERY = 50

RESUME_NOTE = ("[System: You are resuming from a saved game. Your progress has been "
               "automatically restored to where you left off. You do not need to redo the "
               "intro, character naming, starter selection, or tutorial. Continue playing "
               "from where you are.]")


class Stop:
    """Ctrl+C once asks for a pause at the next save point; twice stops now."""

    def __init__(self):
        self.requested = False
        self.now = False

    def handler(self, *_):
        if not self.requested:
            self.requested = True
            say("Pausing: the run will save in-game and stop at the next chance. "
                "Press Ctrl+C again to stop right away from the last save.")
        else:
            self.now = True


def _print(text: str) -> None:
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(text.encode(enc, errors="replace").decode(enc), flush=True)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        print("config.yaml not found. Run 'python setup.py' first.")
        sys.exit(1)
    load_dotenv(ROOT / ".env")
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")[:40] or "model"


def _ask(prompt_text: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = input(f"{prompt_text} ({hint}): ").strip().lower()
    return default if not answer else answer in ("y", "yes")


def _choose_slot(game: str) -> tuple[Path, Path | None]:
    """Ask which save slot to play. Returns (slot path, existing save or None)."""
    slot_dir = SAVES / game / "slots"
    slot_dir.mkdir(parents=True, exist_ok=True)
    slots = sorted(slot_dir.glob("*.sav"))
    say("Which save slot should this run use?")
    for i, s in enumerate(slots, 1):
        stamp = datetime.fromtimestamp(s.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        say(f"  {i}. {s.stem} (saved {stamp})")
    say(f"  {len(slots) + 1}. New slot")
    while True:
        answer = input(f"choose 1-{len(slots) + 1} ({len(slots) + 1}): ").strip()
        if not answer or answer == str(len(slots) + 1):
            name = _slug(input("Name for the new slot: ").strip() or "slot")
            return slot_dir / f"{name}.sav", None
        if answer.isdigit() and 1 <= int(answer) <= len(slots):
            return slots[int(answer) - 1], slots[int(answer) - 1]
        say(f"please enter a number from 1 to {len(slots) + 1}")


def plan_start(config: dict) -> dict:
    """Work out which save the run starts from and where progress is kept."""
    game = config["game"]
    run_cfg = config["run"]
    mode = run_cfg.get("save_mode", "new")
    start = run_cfg.get("start", "new_game")
    save_back, existing = None, None
    if mode == "continue":
        save_back = SAVES / game / "continue.sav"
        existing = save_back if save_back.exists() else None
    elif mode == "slots":
        save_back, existing = _choose_slot(game)

    if start != "new_game":
        checkpoint = CHECKPOINTS / game / f"{start}.sav"
        if not checkpoint.exists():
            say(f"There is no {game.title()} checkpoint named '{start}' in checkpoints/{game}/.")
            sys.exit(1)
        return {"label": f"checkpoint:{start}", "save_file": checkpoint, "save_back": save_back}
    if existing is not None:
        return {"label": "save", "save_file": existing, "save_back": save_back}
    return {"label": "new_game", "save_file": None, "save_back": save_back}


class Run:
    def __init__(self, config: dict, folder: RunFolder, state: dict):
        self.config = config
        self.folder = folder
        self.state = state
        self.stop = Stop()
        self.provider = Provider(config, call_log=lambda r: folder.append("model_calls.jsonl", r))
        if state.get("totals"):
            self.provider.restore(state["totals"])
        self.game = None
        self.client = None

    # ── persistence ──────────────────────────────────────────────────────

    def persist(self) -> None:
        self.state["totals"] = self.provider.totals()
        self.state["updated"] = datetime.now().isoformat(timespec="seconds")
        self.state["updated_epoch"] = time.time()
        if self.game is not None:
            self.state["player_name"] = self.game.player_name
            self.state["rival_name"] = self.game.rival_name
        self.folder.write_json("run_state.json", self.state)

    def active_seconds(self) -> float:
        total = 0.0
        for s in self.state["sessions"]:
            end = s.get("end") or time.time()
            total += max(0.0, end - s["start"])
        return total

    def write_summary(self, outcome: str, detail: str = "") -> dict:
        totals = self.provider.totals()
        calls = totals["calls"]
        cap = int(self.config["run"].get("call_cap", 0) or 0)
        per_call = totals["cost_usd"] / calls if calls else 0.0
        cfg = self.config
        summary = {
            "benchmark": "hbtl-pokemon-eval",
            "outcome": outcome,
            "detail": detail,
            "success": outcome == "success",
            "model": cfg["provider"]["model"],
            "endpoint": cfg["provider"]["base_url"],
            "game": cfg["game"],
            "start": self.state["start"],
            "target": cfg["run"]["target"],
            "hints": sorted(cfg.get("hints") or []),
            "call_cap": cap,
            "start_reads": self.state.get("start_reads"),
            "milestones_at_start": self.state.get("milestones_at_start", []),
            "milestones": self.state["milestones"],
            "turns": self.state["turn"],
            "calls": calls,
            "tokens": totals["tokens"],
            "cost_usd": totals["cost_usd"],
            "cost_source": totals["cost_source"],
            "average_cost_per_call_usd": round(per_call, 6),
            "projected_cost_at_cap_usd": round(per_call * cap, 4) if cap else None,
            "wall_seconds": round(self.active_seconds(), 1),
            "sessions": len(self.state["sessions"]),
            "model_params": cfg.get("model_params", {}),
            "harness_commit": self.state["harness_commit"],
            "started": self.state["created"],
            "finished": datetime.now().isoformat(timespec="seconds"),
            "files": self.folder.file_hashes(),
        }
        self.folder.write_json("summary.json", summary)
        return summary

    def close(self) -> None:
        """A finished run keeps only its results. The run state (which holds
        local paths) and the game folder with the ROM copy are removed."""
        try:
            (self.folder.path / "run_state.json").unlink()
        except FileNotFoundError:
            pass
        shutil.rmtree(self.state["game_dir"], ignore_errors=True)

    # ── saves ────────────────────────────────────────────────────────────

    async def in_game_save(self, keep_as: list[str] = ()) -> bool:
        ok = await self.game.save_state()
        sav = Path(self.state["game_dir"]) / f"pokemon_{self.config['game']}.sav"
        if not ok or not sav.exists():
            return False
        self.state["last_save_turn"] = self.state["turn"]
        shutil.copy2(sav, self.folder.path / "saves" / "latest.sav")
        for name in keep_as:
            shutil.copy2(sav, self.folder.path / "saves" / f"{name}.sav")
        back = self.state.get("save_back")
        if back:
            Path(back).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sav, back)
        self.folder.event({"event": "saved", "turn": self.state["turn"],
                           "checkpoints": list(keep_as)})
        return True

    async def save_when_possible(self, keep_as: list[str] = ()) -> bool:
        """Get to a plain overworld screen without asking the model, then save."""
        for _ in range(15):
            result = await self.game.get_game_state()
            if result and result[2].get("mode") == "overworld":
                return await self.in_game_save(keep_as)
            await asyncio.sleep(0.3)
        return False

    # ── milestones ───────────────────────────────────────────────────────

    async def check_milestones(self) -> None:
        raw = engine.read_state() or {}
        reads = milestones_from_memory(raw)
        start = self.state["start_reads"]
        new = reached(reads, start) - set(self.state["milestones"]) \
            - set(self.state.get("milestones_at_start", []))
        if not new:
            return
        confirmations = [reads]
        for _ in range(COMPLETION_CONFIRMATIONS - 1):
            await asyncio.sleep(0.2)
            again = milestones_from_memory(engine.read_state() or {})
            confirmations.append(again)
            new &= reached(again, start)
        for name in [m for m in MILESTONES if m in new]:
            self.state["milestones"][name] = {
                "turn": self.state["turn"], "call": self.provider.calls,
                "reads": confirmations,
            }
            self.state["pending_checkpoints"].append(name)
            self.folder.event({"event": "milestone", "milestone": name,
                               "turn": self.state["turn"], "call": self.provider.calls,
                               "reads": confirmations})
            say(f"Milestone reached: {MILESTONE_LABELS[name]} "
                f"(turn {self.state['turn']}, call {self.provider.calls})")

    # ── the loop ─────────────────────────────────────────────────────────

    async def play(self) -> tuple[str, str]:
        st = self.state
        cfg = self.config
        target = cfg["run"]["target"]
        history = st["history"]
        dead_reads = 0

        while True:
            if self.stop.now:
                return "paused", "stopped from the last save"

            if self.game.process is not None and self.game.process.poll() is not None:
                return "paused", "the emulator closed"

            if st["start_reads"] is not None:
                await self.check_milestones()
                if target in st["milestones"]:
                    return "success", ""

            result = await self.game.get_game_state()
            if result is None:
                await asyncio.sleep(0.25)
                continue
            text, choices, meta = result
            if meta.get("mode") == "error":
                dead_reads += 1
                if dead_reads >= 60:
                    return "paused", "lost the connection to the Lua bridge"
                await asyncio.sleep(0.5)
                continue
            dead_reads = 0

            needs_name = bool(meta.get("needs_name"))
            if not choices and not needs_name:
                if text.strip():
                    history.append({"role": "user", "content": text[:DIALOGUE_LIMIT]})
                    del history[:-HISTORY_LIMIT]
                continue

            # The first real decision fixes where the run started from.
            if st["start_reads"] is None and meta.get("mode") == "overworld":
                start_raw = milestones_from_memory(engine.read_state() or {})
                st["start_reads"] = start_raw
                st["milestones_at_start"] = [m for m in MILESTONES
                                             if m in reached(start_raw, {"hof_teams": 0})]
                if target in st["milestones_at_start"]:
                    return "already_reached", "the starting save already has the target milestone"
                self.persist()

            # Saves happen on a plain overworld screen, before the decision.
            if meta.get("mode") == "overworld":
                due = st["turn"] - st["last_save_turn"] >= SAVE_EVERY
                if self.stop.requested or st["pending_checkpoints"] or due:
                    keep = list(st["pending_checkpoints"])
                    if await self.in_game_save(keep):
                        st["pending_checkpoints"] = []
                        if self.stop.requested:
                            return "paused", "paused with Ctrl+C"
                    elif self.stop.requested:
                        say("Couldn't save here; trying again on the next turn.")

            st["turn"] += 1
            turn = st["turn"]
            if needs_name:
                schema = {"type": "object", "properties": {
                    "thinking": {"type": "string"},
                    "name": {"type": "string", "maxLength": 7}},
                    "required": ["thinking", "name"], "additionalProperties": False}
            else:
                schema = {"type": "object", "properties": {
                    "thinking": {"type": "string"},
                    "choice": {"type": "string", "enum": list(choices)}},
                    "required": ["thinking", "choice"], "additionalProperties": False}

            system = prompt.system_prompt(self.game.current_location, self.game.player_name,
                                          self.game.rival_name, meta.get("is_outdoor", True))
            messages = [{"role": "system", "content": system}, *history,
                        {"role": "user", "content": text}]

            try:
                reply, error = await self.provider.generate(self.client, messages, schema, turn)
            except CallCapReached:
                st["turn"] -= 1
                return "call_cap", f"reached the cap of {self.provider.max_calls} calls"
            if reply is None:
                if error.startswith("reply rejected"):
                    return "model_error", error
                st["turn"] -= 1
                return "paused", f"the model provider stopped answering ({error})"

            thinking = reply.get("thinking", "")
            if needs_name:
                name = reply["name"][:7]
                await self.game.send_name(name)
                if meta.get("nickname"):
                    pass
                elif not self.game.player_name:
                    self.game.player_name = name
                elif not self.game.rival_name:
                    self.game.rival_name = name
                answer, label, detail = f"Named: {name}", name, ""
            else:
                choice = reply["choice"]
                label = choices[choice].get("label", "")
                # Preset names picked from the naming menu
                if label and label.upper() != "NEW NAME":
                    if "your name" in text.lower() and not self.game.player_name:
                        self.game.player_name = label.strip()
                    elif "his name" in text.lower() and not self.game.rival_name:
                        self.game.rival_name = label.strip()
                detail = await self.game.send_choice(choice, choices) or ""
                answer = choice

            raw = engine.read_state() or {}
            for record in self.game.read_new_inputs():
                self.folder.append("inputs.jsonl", {"turn": turn, **record})
            self.folder.event({
                "event": "turn", "turn": turn, "call": self.provider.calls,
                "mode": meta.get("mode"), "location": self.game.current_location,
                "map_id": raw.get("map_id"), "x": raw.get("player_x"), "y": raw.get("player_y"),
                "state_text": text,
                "choices": {k: v.get("label", "") for k, v in choices.items()},
                "thinking": thinking, "answer": answer, "label": label, "result": detail,
                "memory": milestones_from_memory(raw),
            })

            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": answer})
            del history[:-HISTORY_LIMIT]

            self.persist()

            totals = self.provider.totals()
            _print(f"\n  Turn {turn} | {self.game.current_location or '...'} | "
                   f"calls {totals['calls']} | ${totals['cost_usd']:.4f}")
            if thinking:
                _print(f"  Thinking: {thinking[:400]}")
            _print(f"  > {answer}. {label}" if not needs_name else f"  > {answer}")

    async def start(self, resume: bool) -> None:
        st = self.state
        cfg = self.config
        engine.configure(
            vba_exe=runtime.vba_exe(), lua_script=Path(st["game_dir"]) / "pokemon_bridge.lua",
            bridge_dir=Path(st["game_dir"]) / "_bridge", map_db=runtime.MAP_DB,
            game=cfg["game"], hints=cfg.get("hints") or [],
        )
        rom = Path(st["game_dir"]) / f"pokemon_{cfg['game']}.gb"
        self.game = engine.PokemonGame(rom)
        has_save = self.game.has_save_files()
        self.game.player_name = st.get("player_name", "")
        self.game.rival_name = st.get("rival_name", "")

        if has_save and engine.hint("story_notes"):
            st["history"].append({"role": "user", "content": RESUME_NOTE})

        st["status"] = "running"
        st["sessions"].append({"start": time.time(), "end": None})
        self.persist()
        self.folder.event({"event": "resumed" if resume else "started",
                           "from_save": has_save})

        signal.signal(signal.SIGINT, self.stop.handler)
        say("Starting VBA-RR...")
        if not await self.game.launch(fresh_start=not has_save):
            await self.game.quit()
            st["sessions"][-1]["end"] = time.time()
            st["status"] = "paused"
            self.persist()
            say("The game didn't start. The run is saved as paused; fix the problem and "
                "run this again to resume.")
            return
        print("\n  Press Ctrl+C at any time to save and pause.\n")

        async with httpx.AsyncClient() as client:
            self.client = client
            try:
                outcome, detail = await self.play()
            except Exception as exc:  # keep the run resumable on any harness error
                outcome, detail = "paused", f"harness error: {type(exc).__name__}: {exc}"

        if outcome in ("success", "call_cap", "model_error", "already_reached"):
            keep = list(st["pending_checkpoints"])
            if outcome != "already_reached":
                await self.save_when_possible(keep)
            st["status"] = "finished"
        else:
            st["status"] = "paused"
        latest = Path(st["game_dir"]) / f"pokemon_{cfg['game']}.sav"
        if latest.exists():
            shutil.copy2(latest, self.folder.path / "saves" / "latest.sav")
        await self.game.quit()
        st["sessions"][-1]["end"] = time.time()
        self.persist()
        summary = self.write_summary(outcome if st["status"] == "finished" else "paused", detail)
        if st["status"] == "finished":
            self.close()

        print()
        if st["status"] == "paused":
            say(f"Run paused ({detail}). Run 'python run.py' again to resume it.")
        else:
            say(f"Run finished: {outcome}{' (' + detail + ')' if detail else ''}.")
        say(f"{summary['calls']} calls, {summary['tokens']['in']} input and "
            f"{summary['tokens']['out']} output tokens, ${summary['cost_usd']:.4f}.")
        if summary["projected_cost_at_cap_usd"] is not None and summary["calls"]:
            say(f"At this rate the full {summary['call_cap']}-call cap would cost about "
                f"${summary['projected_cost_at_cap_usd']:.2f}.")
        say(f"Results: {self.folder.path}")


def new_run(config: dict) -> Run:
    for name, path in (("VBA-RR", runtime.vba_exe()), ("pokemon-access", runtime.ACCESS_DIR / "pokemon.lua"),
                       ("the map database", runtime.MAP_DB)):
        if not path.exists():
            say(f"{name} is missing. Run 'python setup.py' first.")
            sys.exit(1)
    problem = runtime.check_rom(Path(config["rom"]), config["game"])
    if problem:
        say(problem)
        sys.exit(1)
    start = plan_start(config)
    run_id = f"run_{datetime.now():%Y%m%d_%H%M%S}_{_slug(config['provider']['model'])}"
    game_dir = RUNS_DIR / run_id
    runtime.stage_game(game_dir, Path(config["rom"]), config["game"], start["save_file"])
    folder = RunFolder(RESULTS / run_id)
    state = {
        "run_id": run_id,
        "status": "starting",
        "created": datetime.now().isoformat(timespec="seconds"),
        "config": config,
        "game_dir": str(game_dir),
        "start": start["label"],
        "save_back": str(start["save_back"]) if start["save_back"] else None,
        "harness_commit": harness_commit(),
        "turn": 0,
        "last_save_turn": 0,
        "history": [],
        "start_reads": None,
        "milestones_at_start": [],
        "milestones": {},
        "pending_checkpoints": [],
        "sessions": [],
        "totals": None,
    }
    return Run(config, folder, state)


def main():
    config = load_config()
    resumable = paused_runs()
    run = None
    if resumable:
        latest = resumable[-1]
        st = json.loads((latest / "run_state.json").read_text(encoding="utf-8"))
        calls = (st.get("totals") or {}).get("calls", 0)
        say(f"There is a paused run from {st['created'][:16].replace('T', ' ')} "
            f"({st['config']['provider']['model']}, {st['config']['game'].title()}, "
            f"{st['turn']} turns, {calls} calls).")
        if st["sessions"] and st["sessions"][-1].get("end") is None:
            # The process died mid-session; count time up to its last turn.
            st["sessions"][-1]["end"] = st.get("updated_epoch") or st["sessions"][-1]["start"]
        if _ask("Resume it?"):
            run = Run(st["config"], RunFolder(latest), st)
            asyncio.run(run.start(resume=True))
            return
        if _ask("Close it as stopped so it isn't offered again?", default=False):
            st["status"] = "finished"
            closed = Run(st["config"], RunFolder(latest), st)
            closed.write_summary("stopped", "closed without finishing")
            closed.close()
    run = new_run(config)
    model = config["provider"]["model"]
    say(f"{model} plays {engine.GAMES[config['game']]['title']} until: "
        f"{MILESTONE_LABELS[config['run']['target']]} "
        f"(call cap {config['run'].get('call_cap') or 'none'}).")
    asyncio.run(run.start(resume=False))


if __name__ == "__main__":
    main()
