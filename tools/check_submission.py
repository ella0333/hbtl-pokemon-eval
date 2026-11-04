"""Check submitted run folders and rebuild the leaderboard.

    python tools/check_submission.py submissions/<folder> [...]   check folders
    python tools/check_submission.py --leaderboard                 check all, rebuild

A submission passes when its files match the hashes in summary.json, the
calls, tokens and cost recount from model_calls.jsonl, the milestones are
backed by the memory reads in events.jsonl, and the harness commit is one of
this repo's commits. Rows are "self-reported" until listed in
submissions/verified.txt.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from hbtl_pokemon.results import (  # noqa: E402
    MILESTONE_LABELS,
    MILESTONES,
    REQUIRED_FILES,
    reached,
)

SUBMISSIONS = ROOT / "submissions"
VERIFIED = SUBMISSIONS / "verified.txt"
LEADERBOARD_JSON = ROOT / "leaderboard.json"
LEADERBOARD_MD = ROOT / "LEADERBOARD.md"
README = ROOT / "README.md"
FINISHED = ("success", "call_cap", "model_error")
FORBIDDEN_SUFFIXES = (".gb", ".gbc", ".gba", ".zip", ".7z")
README_ROWS = 10


def _jsonl(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    raise ValueError(f"{path.name} line {n} is not valid JSON") from None
    return rows


def _commit_known(commit: str) -> bool:
    if not commit or commit == "unknown" or commit.endswith("-modified"):
        return False
    out = subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=ROOT,
                         capture_output=True)
    return out.returncode == 0


def check(folder: Path) -> tuple[dict | None, list[str]]:
    """Returns (summary, problems). No problems means the folder passes."""
    problems: list[str] = []
    folder = Path(folder)
    for name in REQUIRED_FILES:
        if not (folder / name).is_file():
            problems.append(f"missing {name}")
    if problems:
        return None, problems
    try:
        summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, ["summary.json is not valid JSON"]

    # Files and hashes
    listed = summary.get("files", {})
    present = {}
    for p in folder.rglob("*"):
        if p.is_file() and p.name != "summary.json":
            present[p.relative_to(folder).as_posix()] = p
    for rel, p in present.items():
        if p.suffix.lower() in FORBIDDEN_SUFFIXES:
            problems.append(f"{rel} must not be submitted (no ROMs or archives)")
        elif rel == "run_state.json":
            problems.append("run_state.json means the run was not finished")
        elif rel not in listed:
            problems.append(f"{rel} is not listed in summary.json")
        elif hashlib.sha256(p.read_bytes()).hexdigest() != listed[rel]:
            problems.append(f"{rel} does not match its hash in summary.json")
    for rel in listed:
        if rel not in present:
            problems.append(f"{rel} is listed in summary.json but missing")

    if summary.get("outcome") not in FINISHED:
        problems.append(f"outcome is '{summary.get('outcome')}', not a finished run")
    start = summary.get("start", "")
    if start != "new_game":
        problems.append("leaderboard runs must start from a new game")
    target = summary.get("target")
    if target not in MILESTONES:
        problems.append(f"unknown target '{target}'")

    # Recount calls, tokens and cost
    try:
        calls = _jsonl(folder / "model_calls.jsonl")
        events = _jsonl(folder / "events.jsonl")
    except ValueError as e:
        return summary, problems + [str(e)]
    answered = [c for c in calls if c.get("response") is not None]
    tok_in = sum((c.get("usage") or {}).get("in", 0) for c in answered)
    tok_out = sum((c.get("usage") or {}).get("out", 0) for c in answered)
    cost = sum((c.get("usage") or {}).get("cost", 0.0) or 0.0 for c in answered)
    if len(answered) != summary.get("calls"):
        problems.append(f"summary says {summary.get('calls')} calls, model_calls.jsonl has {len(answered)}")
    tokens = summary.get("tokens", {})
    if tok_in != tokens.get("in") or tok_out != tokens.get("out"):
        problems.append("token totals don't match model_calls.jsonl")
    if abs(cost - float(summary.get("cost_usd", 0))) > max(1e-4, 1e-3 * cost):
        problems.append(f"summary says ${summary.get('cost_usd')}, model_calls.jsonl adds up to ${cost:.6f}")
    cap = summary.get("call_cap") or 0
    if cap and len(answered) > cap:
        problems.append(f"{len(answered)} calls is over the call cap of {cap}")

    # Milestones must come from the memory reads
    start_reads = summary.get("start_reads") or {"hof_teams": 0}
    at_start = set(summary.get("milestones_at_start", []))
    logged = {e["milestone"]: e for e in events if e.get("event") == "milestone"}
    for name, info in (summary.get("milestones") or {}).items():
        ev = logged.get(name)
        if ev is None:
            problems.append(f"milestone {name} has no milestone event in events.jsonl")
            continue
        reads = ev.get("reads") or []
        if len(reads) < 2 or not all(name in reached(r, start_reads) for r in reads):
            problems.append(f"milestone {name} is not backed by two memory reads")
        if info.get("call", 0) > len(answered):
            problems.append(f"milestone {name} is dated after the last call")
    success = summary.get("outcome") == "success"
    if success and target not in (summary.get("milestones") or {}):
        problems.append("outcome is success but the target milestone was never read from memory")
    if not success and target in (summary.get("milestones") or {}):
        problems.append("the target milestone was reached but the outcome is not success")
    if target in at_start:
        problems.append("the starting save already had the target milestone")

    if not _commit_known(summary.get("harness_commit", "")):
        problems.append(f"harness commit '{summary.get('harness_commit')}' is not a commit of this repo")
    return summary, problems


def _row(folder: Path, summary: dict, verified: set) -> dict:
    hints = summary.get("hints") or []
    return {
        "submission": folder.name,
        "model": summary["model"],
        "game": summary["game"].title(),
        "target": MILESTONE_LABELS[summary["target"]],
        "target_rank": MILESTONES.index(summary["target"]),
        "hints": "Off" if not hints else ", ".join(h.replace("_", " ") for h in hints),
        "success": bool(summary.get("success")),
        "calls": summary["calls"],
        "cost_usd": round(float(summary["cost_usd"]), 4),
        "status": "Verified" if folder.name in verified else "Self-reported",
    }


def _money(value: float) -> str:
    return f"${value:.2f}" if value >= 0.01 or value == 0 else f"${value:.4f}"


def _table(rows: list[dict]) -> str:
    lines = ["| Model | Game | Target | Hints | Success | Calls | Cost | Status |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['model']} | {r['game']} | {r['target']} | {r['hints']} | "
                     f"{'Yes' if r['success'] else 'No'} | {r['calls']} | "
                     f"{_money(r['cost_usd'])} | {r['status']} |")
    return "\n".join(lines)


def rebuild() -> int:
    verified = set()
    if VERIFIED.exists():
        verified = {line.strip() for line in VERIFIED.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.startswith("#")}
    rows, failed = [], 0
    for folder in sorted(p for p in SUBMISSIONS.iterdir() if p.is_dir()):
        summary, problems = check(folder)
        if problems:
            failed += 1
            print(f"{folder.name}: left off the leaderboard")
            for p in problems:
                print(f"  - {p}")
            continue
        rows.append(_row(folder, summary, verified))
    # Only runs of the same game and target are compared with each other.
    rows.sort(key=lambda r: (r["game"], r["target_rank"], not r["success"], r["calls"], r["cost_usd"]))

    LEADERBOARD_JSON.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    LEADERBOARD_MD.write_text(
        "# Leaderboard\n\n"
        "Every run starts from a new game. Runs are grouped by game and target, and within "
        "each group successful runs come first, then fewer calls. Rows are "
        "self-reported until the logs have been looked at and the run is marked verified.\n\n"
        + (_table(rows) if rows else "No runs have been submitted yet.") + "\n",
        encoding="utf-8")

    if README.exists():
        text = README.read_text(encoding="utf-8")
        start, end = "<!-- leaderboard:start -->", "<!-- leaderboard:end -->"
        if start in text and end in text:
            body = _table(rows[:README_ROWS]) if rows else "No runs have been submitted yet."
            head, rest = text.split(start, 1)
            _, tail = rest.split(end, 1)
            README.write_text(f"{head}{start}\n{body}\n{end}{tail}", encoding="utf-8")
    print(f"Leaderboard rebuilt: {len(rows)} runs, {failed} left off.")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv == ["--leaderboard"]:
        return rebuild()
    status = 0
    for arg in argv:
        summary, problems = check(Path(arg))
        if problems:
            status = 1
            print(f"{arg}: FAILED")
            for p in problems:
                print(f"  - {p}")
        else:
            print(f"{arg}: passed ({summary['model']}, {summary['outcome']}, {summary['calls']} calls)")
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
