"""The run folder: logs, screenshots, saves, run_state.json and summary.json."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "results"

# Files a finished run must contain. Screenshots and saves are optional.
REQUIRED_FILES = ("summary.json", "model_calls.jsonl", "events.jsonl", "inputs.jsonl")

MILESTONES = ["pokedex"] + [f"badge_{i}" for i in range(1, 9)] + ["hall_of_fame"]
MILESTONE_LABELS = {"pokedex": "Pokedex from Oak", "hall_of_fame": "Hall of Fame",
                    **{f"badge_{i}": f"Badge {i}" for i in range(1, 9)}}


def harness_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                             text=True, timeout=10)
        commit = out.stdout.strip()
        if out.returncode != 0 or not commit:
            return "unknown"
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "src", "lua", "prompts",
                                "run.py", "tools/prepare_maps.py"],
                               cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        return commit + ("-modified" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def milestones_from_memory(state: dict) -> dict:
    """The three milestone reads the bridge exports, as plain values."""
    return {
        "has_pokedex": bool(state.get("has_pokedex")),
        "badges": int(state.get("badges", 0) or 0),
        "hof_teams": int(state.get("hof_teams", 0) or 0),
    }


def reached(reads: dict, start: dict) -> set:
    """Which milestones these reads show as reached.

    The Hall of Fame counts when its team count rises above where the run
    started, so a save that has already been through it doesn't count again.
    """
    out = set()
    if reads["has_pokedex"]:
        out.add("pokedex")
    for i in range(1, bin(reads["badges"] & 0xFF).count("1") + 1):
        out.add(f"badge_{i}")
    if reads["hof_teams"] > start.get("hof_teams", 0):
        out.add("hall_of_fame")
    return out


class RunFolder:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / "screenshots").mkdir(exist_ok=True)
        (self.path / "saves").mkdir(exist_ok=True)
        for name in ("model_calls.jsonl", "events.jsonl", "inputs.jsonl"):
            (self.path / name).touch()

    def append(self, name: str, record: dict) -> None:
        with open(self.path / name, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def event(self, record: dict) -> None:
        record = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), **record}
        self.append("events.jsonl", record)

    def write_json(self, name: str, data: dict) -> None:
        tmp = self.path / (name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path / name)

    def read_json(self, name: str) -> dict:
        try:
            return json.loads((self.path / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def file_hashes(self) -> dict:
        hashes = {}
        for p in sorted(self.path.rglob("*")):
            if not p.is_file() or p.name in ("summary.json", "run_state.json") or p.suffix == ".tmp":
                continue
            rel = p.relative_to(self.path).as_posix()
            hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        return hashes


def paused_runs() -> list[Path]:
    """Runs that can be resumed: paused ones, and ones whose process died
    while still marked running (a crash or a closed window)."""
    runs = []
    for state in sorted(RESULTS.glob("*/run_state.json")):
        try:
            data = json.loads(state.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("status") in ("paused", "running"):
            runs.append(state.parent)
    return runs
