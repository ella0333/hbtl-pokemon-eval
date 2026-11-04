"""Setup wizard: configure the model and the run, then download the tools.

Creates config.yaml and a .env file for the API key (the config only holds
the env var's NAME), downloads VBA-RR and pokemon-access, builds the map
database, checks your ROM, and checks that the model returns strict JSON.

Run once before using run.py. Run it again to change settings.
"""

from __future__ import annotations

import asyncio
import os
import platform
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from hbtl_pokemon import runtime  # noqa: E402
from hbtl_pokemon.game import HINT_GROUPS  # noqa: E402
from hbtl_pokemon.provider import Provider  # noqa: E402
from hbtl_pokemon.results import MILESTONE_LABELS, MILESTONES  # noqa: E402

CONFIG_PATH = ROOT / "config.yaml"
ENV_PATH = ROOT / ".env"
API_KEY_ENV = "MODEL_API_KEY"

HINT_LABELS = {
    "navigation": "Navigation hints (the Directions line, visited and blocked marks)",
    "stuck_notes": "Stuck notes (when a move changed nothing, or a battle menu loop)",
    "battle_tips": "Battle and catch tips (catch chances, PP and Poke Ball notes)",
    "item_descriptions": "Item descriptions (what each item does)",
    "location_tips": "Location tips (where to head next in each town)",
    "story_notes": "Story notes (starter, Poke Ball permission, demonstration, resume)",
}


# ── prompt helpers (same conventions as the module family's wizards) ───────

def _say(text: str) -> None:
    try:
        print(f"[system] {text}")
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(f"[system] {text}".encode(enc, errors="replace").decode(enc))


def _text(prompt: str, default: str = "", show_default: bool = True) -> str:
    suffix = f" ({default})" if default and show_default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def _yesno(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        answer = input(f"{prompt} ({hint}): ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        _say("please answer y or n")


def _integer(prompt: str, default: int | None = None, minimum: int = 0) -> int:
    suffix = f" ({default})" if default is not None else ""
    while True:
        answer = input(f"{prompt}{suffix}: ").strip()
        if not answer and default is not None:
            return default
        if answer.isdigit() and int(answer) >= minimum:
            return int(answer)
        _say(f"please enter a whole number >= {minimum}")


def _number(prompt: str, default: float = 0.0) -> float:
    while True:
        answer = input(f"{prompt} ({default:g}): ").strip()
        if not answer:
            return default
        try:
            return float(answer.lstrip("$"))
        except ValueError:
            _say("please enter a number, like 0.15")


def _choice(prompt: str, options: list[tuple[str, str]], default: str) -> str:
    """options = [(key, label), ...]; returns the chosen key."""
    _say(prompt)
    for i, (_, label) in enumerate(options, 1):
        _say(f"  {i}. {label}")
    keys = [k for k, _ in options]
    default_num = keys.index(default) + 1
    while True:
        answer = input(f"choose 1-{len(options)} ({default_num}): ").strip()
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return keys[int(answer) - 1]
        _say(f"please enter a number from 1 to {len(options)}")


# ── .env writer (matches the base wizard's _write_env_stub) ───────────────

def _write_env_stub(env_path: Path, var_name: str) -> None:
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    if var_name in existing:
        _say(f"{env_path} already has {var_name} set.")
        return
    key = _text("Paste your API key (will be stored in .env, never in config.yaml)")
    if not key:
        _say(f"No key entered. Add it later to {env_path} as: {var_name}=your-key")
        key = "PASTE-YOUR-API-KEY-HERE"
    with env_path.open("a", encoding="utf-8") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(f"{var_name}={key}\n")
    _say(f"wrote {env_path}")


# ── questions ─────────────────────────────────────────────────────────────

def _ask_config() -> dict:
    kind = _choice(
        "Where does the model run?",
        [
            ("lmstudio", "LM Studio on this computer (local, free)"),
            ("ollama", "Ollama on this computer (local, free)"),
            ("cloud", "A paid API (OpenRouter, OpenAI, Anthropic, etc.)"),
        ],
        default="cloud",
    )
    defaults = {
        "lmstudio": "http://127.0.0.1:1234",
        "ollama": "http://127.0.0.1:11434",
        "cloud": "https://openrouter.ai/api/v1",
    }
    hints = {
        "lmstudio": "  (In LM Studio: Developer tab, look for the server URL, e.g. http://127.0.0.1:1234)",
        "ollama": "  (Ollama's default is http://127.0.0.1:11434)",
    }
    if kind in hints:
        _say(hints[kind])
    base_url = _text("Server URL", default=defaults[kind])
    model = _text(
        "Model identifier (e.g. 'qwen3-8b', 'openai/gpt-5.6-luna')",
        default="default" if kind != "cloud" else "",
    ) or "default"

    prices = {"input_per_million": 0.0, "output_per_million": 0.0}
    host = (urlparse(base_url).hostname or "").lower()
    if kind == "cloud" and not host.endswith("openrouter.ai"):
        _say("Prices are used to work out each run's cost (OpenRouter reports its own).")
        prices["input_per_million"] = _number("Input price per million tokens, in dollars")
        prices["output_per_million"] = _number("Output price per million tokens, in dollars")

    _say("")
    game = _choice("Which game?", [("red", "Pokemon Red"), ("blue", "Pokemon Blue")], "red")
    while True:
        rom = _text(f"Path to your Pokemon {game.title()} ROM (.gb file)").strip('"')
        problem = runtime.check_rom(Path(rom), game)
        if not problem:
            break
        _say(problem)

    _say("")
    hints_on: list[str] = []
    if _yesno("Turn on any hints? Benchmark runs usually leave them off", default=False):
        for group in HINT_GROUPS:
            if _yesno(HINT_LABELS[group], default=False):
                hints_on.append(group)

    _say("")
    call_cap = _integer("Call cap (most model calls a run can make, 0 for no cap)", 1000)
    target = _choice("Where should the run end?",
                     [(m, MILESTONE_LABELS[m]) for m in MILESTONES], "pokedex")

    start = "new_game"
    checkpoints = sorted((ROOT / "checkpoints" / game).glob("*.sav"))
    checkpoints = [c for c in checkpoints
                   if c.stem in MILESTONES and MILESTONES.index(c.stem) < MILESTONES.index(target)]
    if checkpoints:
        start = _choice("Where should the run start?",
                        [("new_game", "A new game")]
                        + [(c.stem, f"The {MILESTONE_LABELS[c.stem]} checkpoint") for c in checkpoints],
                        "new_game")

    save_mode = _choice(
        "How should saves carry over between runs?",
        [("new", "Start a new game every run"),
         ("continue", "Keep playing the same game every run"),
         ("slots", "Named save slots, picked when a run starts")],
        "new",
    )

    return {
        "provider": {
            "kind": "openai_compat",
            "base_url": base_url,
            "api_key_env": API_KEY_ENV if kind == "cloud" else "",
            "model": model,
            "request_timeout": 300,
            "quirks": {"no_think_suffix": False},
        },
        "model_params": {"temperature": 0.7, "top_p": 0.95, "max_tokens": 3000},
        "prices": prices,
        "game": game,
        "rom": str(Path(rom).resolve()),
        "hints": hints_on,
        "run": {"call_cap": call_cap, "target": target, "start": start, "save_mode": save_mode},
    }


def _write_config(config: dict) -> None:
    text = ("# Generated by setup.py. Safe to edit by hand.\n"
            + yaml.dump(config, default_flow_style=False, sort_keys=False))
    CONFIG_PATH.write_text(text, encoding="utf-8")


# ── connection check (modelled on the base wizard's probe) ────────────────

async def _probe(config: dict) -> str:
    """One strict-schema request. Returns "" on success or a message."""
    probe_config = {**config, "run": {"call_cap": 0}}
    provider = Provider(probe_config)
    schema = {"type": "object", "properties": {
        "thinking": {"type": "string"},
        "choice": {"type": "string", "enum": ["A", "B"]}},
        "required": ["thinking", "choice"], "additionalProperties": False}
    text = "Current Location - Pallet Town\n\nNearby:\nA. Oak's Lab (door) - 6 Down 3 Right\nB. Sign - 2 Up"
    messages = [
        {"role": "system", "content": "You are playing Pokemon. Pick one lettered choice."},
        {"role": "user", "content": text},
    ]
    async with httpx.AsyncClient() as client:
        reply, error = await provider.generate(client, messages, schema, turn=0)

    # Keep any automatic fixes the provider needed.
    if provider.model_override:
        config["provider"]["model"] = provider.model_override
    for bad in provider.blocked_params:
        config["model_params"].pop(bad, None)
    for old, new in provider.param_renames.items():
        if old in config["model_params"]:
            config["model_params"][new] = config["model_params"].pop(old)

    if reply is not None:
        return ""
    lower = error.lower()
    if "http 401" in lower or "http 403" in lower or "api key" in lower or "unauthorized" in lower:
        return f"The API key was refused ({error}). Check {API_KEY_ENV} in .env."
    if error.startswith("reply rejected") or "response_format" in lower or "json_schema" in lower \
            or "structured" in lower or "tool_choice" in lower:
        return (f"The model can't return strict JSON for the game's schema ({error}). Models "
                f"without structured output aren't compatible; see docs/models.md.")
    return f"The connection check failed: {error}"


def main():
    if platform.system() != "Windows":
        _say("This benchmark runs on Windows only (VBA-RR and its Lua loader need it).")
        return

    config = None
    if CONFIG_PATH.exists():
        if not _yesno("config.yaml already exists. Overwrite it?", default=False):
            config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    if config is None:
        config = _ask_config()
        _write_config(config)
        _say(f"wrote {CONFIG_PATH}")
        if config["provider"]["api_key_env"]:
            _write_env_stub(ENV_PATH, config["provider"]["api_key_env"])

    _say("")
    runtime.RUNTIME.mkdir(exist_ok=True)
    runtime.install_vba(_say)
    runtime.install_pokemon_access(_say)
    runtime.build_map_database(_say)

    load_dotenv(ENV_PATH)
    key_env = config["provider"].get("api_key_env")
    if key_env and os.environ.get(key_env, "") in ("", "PASTE-YOUR-API-KEY-HERE"):
        _say(f"Add your key to .env as {key_env}=..., then run setup again for the connection check.")
        return

    paid = bool(key_env)
    if not paid or _yesno("Run the connection check? It makes one small paid call", default=True):
        problem = asyncio.run(_probe(config))
        if problem:
            _say(problem)
            return
        _write_config(config)
        _say("Connection check passed: the model answered with valid JSON.")

    _say("")
    _say("Done. Start a run with:  python run.py")


if __name__ == "__main__":
    main()
