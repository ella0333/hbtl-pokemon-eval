"""Builds the system prompt from the files in prompts/.

The prompt changes with the turn: indoors and outdoors get different movement
rules, and the location tips (when switched on) follow the current area.
Everything the model is told is in prompts/, so it can be read as plain text.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import game as engine

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def _read(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _sections(name: str) -> dict:
    """Split a file into its [section] blocks."""
    out, current = {}, None
    for line in _read(name).splitlines():
        m = re.match(r"^\[(\w+)\]$", line.strip())
        if m:
            current = m.group(1)
            out[current] = []
        elif current:
            out[current].append(line)
    return {k: "\n".join(v).strip("\n") for k, v in out.items()}


_SYSTEM = _read("system.txt")
_GAME = _read("game.txt")
_OUTDOOR = _sections("outdoor.txt")
_INDOOR = _sections("indoor.txt")
_NAV_HINTS = _read("navigation_hints.txt").strip("\n")
_LOCATION_TIPS = json.loads(_read("location_tips.json"))


def location_tip(current_location: str) -> str:
    """The tip for this area, matching "Mt Moon B1F" to "Mt Moon" and so on."""
    if not current_location:
        return ""
    tip = _LOCATION_TIPS.get(current_location, "")
    if tip:
        return tip
    # The key must not be cut off mid-number: "Route 1" must not match
    # "Route 12". Longest key first for the same reason.
    for key in sorted(_LOCATION_TIPS, key=len, reverse=True):
        if re.search(re.escape(key) + r"(?!\d)", current_location) or current_location in key:
            return _LOCATION_TIPS[key]
    return ""


def system_prompt(current_location: str = "", player_name: str = "",
                  rival_name: str = "", is_outdoor: bool = True) -> str:
    title = engine.GAMES[engine.GAME]["title"]
    side = _OUTDOOR if is_outdoor else _INDOOR

    player_line = ""
    if player_name:
        player_line += f" You are playing as {player_name}."
    if rival_name:
        player_line += f" Your rival is {rival_name}."

    nav_hints = ""
    if engine.hint("navigation"):
        extra = [side.get("navigation_hints", ""), _NAV_HINTS]
        nav_hints = "".join("\n" + x for x in extra if x)

    area_info = ""
    if engine.hint("location_tips"):
        tip = location_tip(current_location)
        if tip:
            area_info = f"\n\nArea info ({current_location}): {tip}"

    return _SYSTEM.format(
        game_title=title,
        player_line=player_line,
        game_description=_GAME.format(game_title=title).strip(),
        movement_rule=side["movement_rule"],
        navigation_tips=side["navigation_tips"],
        navigation_hints=nav_hints,
        area_info=area_info,
    ).strip() + "\n"
