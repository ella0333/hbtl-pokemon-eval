"""The Pokemon Red and Blue game layer.

Drives VBA-RR through the Lua bridge and turns each screen into a short text
description plus a lettered list of choices. Walking, talking, menus, battle
text and dialogue are all handled here, so the model only ever picks a letter.

Communication goes through files in the run's bridge folder:
  - state.json  : the Lua bridge writes game state, we read
  - cmd.txt     : we write commands, the Lua bridge reads and deletes
  - text.txt    : the Lua bridge appends screen reader text, we read and truncate

Call configure() once before creating a PokemonGame.
"""

import asyncio
import ctypes
import ctypes.wintypes
import json
import re
import subprocess
import time
from collections import deque
from pathlib import Path

# =========================================================================
# Constants and Tables (from pokemon_text.py)
# =========================================================================

# ── Gen 1 Screen Text Decoder ────────────────────────────────────────────
GEN1_CHARS = {
    0x6E: " LV ", 0x75: "...", 0x7F: " ",
    0x80: "A", 0x81: "B", 0x82: "C", 0x83: "D", 0x84: "E",
    0x85: "F", 0x86: "G", 0x87: "H", 0x88: "I", 0x89: "J",
    0x8A: "K", 0x8B: "L", 0x8C: "M", 0x8D: "N", 0x8E: "O",
    0x8F: "P", 0x90: "Q", 0x91: "R", 0x92: "S", 0x93: "T",
    0x94: "U", 0x95: "V", 0x96: "W", 0x97: "X", 0x98: "Y",
    0x99: "Z", 0x9A: "(", 0x9B: ")", 0x9C: ":", 0x9D: ";",
    0x9E: "[", 0x9F: "]",
    0xA0: "a", 0xA1: "b", 0xA2: "c", 0xA3: "d", 0xA4: "e",
    0xA5: "f", 0xA6: "g", 0xA7: "h", 0xA8: "i", 0xA9: "j",
    0xAA: "k", 0xAB: "l", 0xAC: "m", 0xAD: "n", 0xAE: "o",
    0xAF: "p", 0xB0: "q", 0xB1: "r", 0xB2: "s", 0xB3: "t",
    0xB4: "u", 0xB5: "v", 0xB6: "w", 0xB7: "x", 0xB8: "y",
    0xB9: "z", 0xBA: "e", 0xBB: "'d", 0xBC: "'l", 0xBD: "'s",
    0xBE: "'t", 0xBF: "'v",
    0xE0: "'", 0xE1: "PK", 0xE2: "MN", 0xE3: "-", 0xE4: "'r",
    0xE5: "'m", 0xE6: "?", 0xE7: "!", 0xE8: ".", 0xED: ">",
    0xEF: "(M)", 0xF0: "$", 0xF1: "x", 0xF2: ".", 0xF3: "/",
    0xF4: ",", 0xF5: "(F)",
    0xF6: "0", 0xF7: "1", 0xF8: "2", 0xF9: "3", 0xFA: "4",
    0xFB: "5", 0xFC: "6", 0xFD: "7", 0xFE: "8", 0xFF: "9",
}

# ── Gen 1 Move Names (indexed by move ID 0x01-0xA5) ────────────────────
GEN1_MOVES = [
    None, "Pound", "Karate Chop", "Double Slap", "Comet Punch",
    "Mega Punch", "Pay Day", "Fire Punch", "Ice Punch", "Thunder Punch",
    "Scratch", "Vice Grip", "Guillotine", "Razor Wind", "Swords Dance",
    "Cut", "Gust", "Wing Attack", "Whirlwind", "Fly",
    "Bind", "Slam", "Vine Whip", "Stomp", "Double Kick",
    "Mega Kick", "Jump Kick", "Rolling Kick", "Sand Attack", "Headbutt",
    "Horn Attack", "Fury Attack", "Horn Drill", "Tackle", "Body Slam",
    "Wrap", "Take Down", "Thrash", "Double-Edge", "Tail Whip",
    "Poison Sting", "Twineedle", "Pin Missile", "Leer", "Bite",
    "Growl", "Roar", "Sing", "Supersonic", "Sonic Boom",
    "Disable", "Acid", "Ember", "Flamethrower", "Mist",
    "Water Gun", "Hydro Pump", "Surf", "Ice Beam", "Blizzard",
    "Psybeam", "Bubble Beam", "Aurora Beam", "Hyper Beam", "Peck",
    "Drill Peck", "Submission", "Low Kick", "Counter", "Seismic Toss",
    "Strength", "Absorb", "Mega Drain", "Leech Seed", "Growth",
    "Razor Leaf", "Solar Beam", "Poison Powder", "Stun Spore", "Sleep Powder",
    "Petal Dance", "String Shot", "Dragon Rage", "Fire Spin", "Thunder Shock",
    "Thunderbolt", "Thunder Wave", "Thunder", "Rock Throw", "Earthquake",
    "Fissure", "Dig", "Toxic", "Confusion", "Psychic",
    "Hypnosis", "Meditate", "Agility", "Quick Attack", "Rage",
    "Teleport", "Night Shade", "Mimic", "Screech", "Double Team",
    "Recover", "Harden", "Minimize", "Smokescreen", "Confuse Ray",
    "Withdraw", "Defense Curl", "Barrier", "Light Screen", "Haze",
    "Reflect", "Focus Energy", "Bide", "Metronome", "Mirror Move",
    "Self-Destruct", "Egg Bomb", "Lick", "Smog", "Sludge",
    "Bone Club", "Fire Blast", "Waterfall", "Clamp", "Swift",
    "Skull Bash", "Spike Cannon", "Constrict", "Amnesia", "Kinesis",
    "Soft-Boiled", "High Jump Kick", "Glare", "Dream Eater", "Poison Gas",
    "Barrage", "Leech Life", "Lovely Kiss", "Sky Attack", "Transform",
    "Bubble", "Dizzy Punch", "Spore", "Flash", "Psywave",
    "Splash", "Acid Armor", "Crabhammer", "Explosion", "Fury Swipes",
    "Bonemerang", "Rest", "Rock Slide", "Hyper Fang", "Sharpen",
    "Conversion", "Tri Attack", "Super Fang", "Slash", "Substitute",
    "Struggle",
]

# Status condition names
GEN1_STATUS = {
    0: None, 1: "SLP", 2: "SLP", 3: "SLP", 4: "PSN", 8: "BRN", 16: "FRZ", 32: "PAR",
}

# Paths. All of these are set by configure() for each run.
VBA_EXE = Path("vba.exe")
LUA_SCRIPT = Path("pokemon_bridge.lua")
BRIDGE_DIR = Path("_bridge")
STATE_FILE = BRIDGE_DIR / "state.json"
CMD_FILE = BRIDGE_DIR / "cmd.txt"
TEXT_FILE = BRIDGE_DIR / "text.txt"
INPUT_LOG_FILE = BRIDGE_DIR / "input_log.jsonl"
MAP_DB_PATH = Path("map_database.json")

# Hint groups. Each one is off unless configure() turns it on.
HINT_GROUPS = (
    "navigation", "stuck_notes", "battle_tips",
    "item_descriptions", "location_tips", "story_notes",
)
HINTS = set()

# Red and Blue use the same memory layout and maps. The game only changes the
# preset names offered for the player and the rival.
GAMES = {
    "red": {"title": "Pokémon Red", "player_presets": ("RED", "ASH", "JACK"),
            "rival_presets": ("BLUE", "GARY", "JOHN")},
    "blue": {"title": "Pokémon Blue", "player_presets": ("BLUE", "GARY", "JOHN"),
             "rival_presets": ("RED", "ASH", "JACK")},
}
GAME = "red"


def configure(vba_exe, lua_script, bridge_dir, map_db, game="red", hints=()):
    """Point the engine at one run's files and set the game and hint groups."""
    global VBA_EXE, LUA_SCRIPT, BRIDGE_DIR, STATE_FILE, CMD_FILE, TEXT_FILE
    global INPUT_LOG_FILE, MAP_DB_PATH, GAME
    VBA_EXE = Path(vba_exe)
    LUA_SCRIPT = Path(lua_script)
    BRIDGE_DIR = Path(bridge_dir)
    STATE_FILE = BRIDGE_DIR / "state.json"
    CMD_FILE = BRIDGE_DIR / "cmd.txt"
    TEXT_FILE = BRIDGE_DIR / "text.txt"
    INPUT_LOG_FILE = BRIDGE_DIR / "input_log.jsonl"
    MAP_DB_PATH = Path(map_db)
    GAME = game
    HINTS.clear()
    HINTS.update(h for h in hints if h in HINT_GROUPS)


def hint(group):
    """True when the hint group is switched on."""
    return group in HINTS


# ROM extensions
ROM_EXTENSIONS = (".gb", ".gbc", ".gba")

# Text history buffer
TEXT_HISTORY = []
MAX_HISTORY = 50

# The cave tileset. Mt Moon, Rock Tunnel, Seafoam Islands, Victory Road,
# Cerulean Cave and Diglett's Cave all use it, and all of them are crossed by
# compass rather than from a Nearby list.
CAVE_TILESET = 17

# Gen 1/2 Keyboard grid
KB_GRID = [
    ["A", "B", "C", "D", "E", "F", "G", "H", "I"],
    ["J", "K", "L", "M", "N", "O", "P", "Q", "R"],
    ["S", "T", "U", "V", "W", "X", "Y", "Z", " "],
]

# Win32 constants
WM_COMMAND = 0x0111
WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
BM_CLICK = 0x00F5
VBA_LUA_NEW_WINDOW_CMD_ID = 40467  # Menu ID for "New Lua Script Window"

user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None

# Item quantity regex
_QUANTITY_RE = re.compile(r'^[×x\u00d7]\s*\d+$', re.IGNORECASE)

# Mart price line regex - a price sitting on its own line, e.g. "¥200"
_PRICE_RE = re.compile(r'^[¥￥$]\s*[\d,]+$')

# Item descriptions - helps the AI understand what each item does
_ITEM_HINTS = {
    "POTION": "restores 20 HP",
    "SUPER POTION": "restores 50 HP",
    "HYPER POTION": "restores 200 HP",
    "MAX POTION": "restores all HP",
    "FULL RESTORE": "restores all HP + cures status",
    "ANTIDOTE": "cures poison ONLY",
    "PARLYZ HEAL": "cures paralysis ONLY",
    "BURN HEAL": "cures burn ONLY",
    "ICE HEAL": "cures freeze ONLY",
    "AWAKENING": "cures sleep ONLY",
    "FULL HEAL": "cures any status condition",
    "REVIVE": "revives fainted Pokémon to half HP",
    "MAX REVIVE": "revives fainted Pokémon to full HP",
    "REPEL": "prevents weak wild encounters",
    "SUPER REPEL": "prevents weak wild encounters (longer)",
    "MAX REPEL": "prevents weak wild encounters (longest)",
    "ESCAPE ROPE": "teleports out of caves",
    "POKE BALL": "catches wild Pokémon",
    "GREAT BALL": "catches wild Pokémon (better)",
    "ULTRA BALL": "catches wild Pokémon (best)",
    "MASTER BALL": "catches any Pokémon guaranteed",
    "RARE CANDY": "levels up a Pokémon by 1",
    "ETHER": "restores 10 PP to one move",
    "ELIXIR": "restores 10 PP to all moves",
    "TOWN MAP": "shows the region map",
    "OLD ROD": "fish for Pokémon in water",
    "GOOD ROD": "fish for better Pokémon",
    "SUPER ROD": "fish for rare Pokémon",
    "BICYCLE": "ride fast",
    "X ATTACK": "battle: raises Attack",
    "X DEFEND": "battle: raises Defense",
    "X SPEED": "battle: raises Speed",
    "X SPECIAL": "battle: raises Special",
    "GUARD SPEC.": "battle: prevents stat drops",
    "DIRE HIT": "battle: increases crit rate",
}

# Gen 1 item ids, as read straight out of the bag at 0xD31D by the bridge.
# Only the items that can actually turn up in Blue are named; TMs/HMs and the
# unused ids fall through to "Item #n".  Same shape as GEN1_MOVES above.
GEN1_ITEMS = {
    0x01: "MASTER BALL", 0x02: "ULTRA BALL", 0x03: "GREAT BALL", 0x04: "POKE BALL",
    0x05: "TOWN MAP", 0x06: "BICYCLE", 0x08: "SAFARI BALL", 0x09: "POKEDEX",
    0x0A: "MOON STONE", 0x0B: "ANTIDOTE", 0x0C: "BURN HEAL", 0x0D: "ICE HEAL",
    0x0E: "AWAKENING", 0x0F: "PARLYZ HEAL", 0x10: "FULL RESTORE", 0x11: "MAX POTION",
    0x12: "HYPER POTION", 0x13: "SUPER POTION", 0x14: "POTION", 0x15: "BOULDERBADGE",
    0x16: "CASCADEBADGE", 0x17: "THUNDERBADGE", 0x18: "RAINBOWBADGE",
    0x19: "SOULBADGE", 0x1A: "MARSHBADGE", 0x1B: "VOLCANOBADGE", 0x1C: "EARTHBADGE",
    0x1D: "ESCAPE ROPE", 0x1E: "REPEL", 0x1F: "OLD AMBER", 0x20: "FIRE STONE",
    0x21: "THUNDER STONE", 0x22: "WATER STONE", 0x23: "HP UP", 0x24: "PROTEIN",
    0x25: "IRON", 0x26: "CARBOS", 0x27: "CALCIUM", 0x28: "RARE CANDY",
    0x29: "DOME FOSSIL", 0x2A: "HELIX FOSSIL", 0x2B: "SECRET KEY",
    0x2D: "BIKE VOUCHER", 0x2E: "X ACCURACY", 0x2F: "LEAF STONE",
    0x30: "CARD KEY", 0x31: "NUGGET", 0x33: "POKE DOLL", 0x34: "FULL HEAL",
    0x35: "REVIVE", 0x36: "MAX REVIVE", 0x37: "GUARD SPEC.", 0x38: "SUPER REPEL",
    0x39: "MAX REPEL", 0x3A: "DIRE HIT", 0x3B: "COIN", 0x3C: "FRESH WATER",
    0x3D: "SODA POP", 0x3E: "LEMONADE", 0x3F: "S.S.TICKET", 0x40: "GOLD TEETH",
    0x41: "X ATTACK", 0x42: "X DEFEND", 0x43: "X SPEED", 0x44: "X SPECIAL",
    0x45: "COIN CASE", 0x46: "OAK'S PARCEL", 0x47: "ITEMFINDER",
    0x48: "SILPH SCOPE", 0x49: "POKE FLUTE", 0x4A: "LIFT KEY", 0x4B: "EXP.ALL",
    0x4C: "OLD ROD", 0x4D: "GOOD ROD", 0x4E: "SUPER ROD", 0x4F: "PP UP",
    0x50: "ETHER", 0x51: "MAX ETHER", 0x52: "ELIXER", 0x53: "MAX ELIXER",
}

# Gen 1 species, keyed by the *internal* index stored in the party struct
# (0xD16B + i*0x2C, first byte) - not the Pokédex number.  Only used to recover
# a Pokémon's real name when its nickname is unreadable; unknown ids fall
# through to the nickname, same as GEN1_ITEMS falls through to "Item #n".
GEN1_SPECIES = {
    0x01: "RHYDON", 0x02: "KANGASKHAN", 0x03: "NIDORAN(M)", 0x04: "CLEFAIRY",
    0x05: "SPEAROW", 0x06: "VOLTORB", 0x07: "NIDOKING", 0x08: "SLOWBRO",
    0x09: "IVYSAUR", 0x0A: "EXEGGUTOR", 0x0B: "LICKITUNG", 0x0C: "EXEGGCUTE",
    0x0D: "GRIMER", 0x0E: "GENGAR", 0x0F: "NIDORAN(F)", 0x10: "NIDOQUEEN",
    0x11: "CUBONE", 0x12: "RHYHORN", 0x13: "LAPRAS", 0x14: "ARCANINE",
    0x15: "MEW", 0x16: "GYARADOS", 0x17: "SHELLDER", 0x18: "TENTACOOL",
    0x19: "GASTLY", 0x1A: "SCYTHER", 0x1B: "STARYU", 0x1C: "BLASTOISE",
    0x1D: "PINSIR", 0x1E: "TANGELA",
    0x21: "GROWLITHE", 0x22: "ONIX", 0x23: "FEAROW", 0x24: "PIDGEY",
    0x25: "SLOWPOKE", 0x26: "KADABRA", 0x27: "GRAVELER", 0x28: "CHANSEY",
    0x29: "MACHOKE", 0x2A: "MR.MIME", 0x2B: "HITMONLEE", 0x2C: "HITMONCHAN",
    0x2D: "ARBOK", 0x2E: "PARASECT", 0x2F: "PSYDUCK", 0x30: "DROWZEE",
    0x31: "GOLEM", 0x33: "MAGMAR", 0x35: "ELECTABUZZ", 0x36: "MAGNETON",
    0x37: "KOFFING", 0x39: "MANKEY", 0x3A: "SEEL", 0x3B: "DIGLETT",
    0x3C: "TAUROS", 0x40: "FARFETCH'D", 0x41: "VENONAT", 0x42: "DRAGONITE",
    0x46: "DODUO", 0x47: "POLIWAG", 0x48: "JYNX", 0x49: "MOLTRES",
    0x4A: "ARTICUNO", 0x4B: "ZAPDOS", 0x4C: "DITTO", 0x4D: "MEOWTH",
    0x4E: "KRABBY", 0x52: "VULPIX", 0x53: "NINETALES", 0x54: "PIKACHU",
    0x55: "RAICHU", 0x58: "DRATINI", 0x59: "DRAGONAIR", 0x5A: "KABUTO",
    0x5B: "KABUTOPS", 0x5C: "HORSEA", 0x5D: "SEADRA",
    0x60: "SANDSHREW", 0x61: "SANDSLASH", 0x62: "OMANYTE", 0x63: "OMASTAR",
    0x64: "JIGGLYPUFF", 0x65: "WIGGLYTUFF", 0x66: "EEVEE", 0x67: "FLAREON",
    0x68: "JOLTEON", 0x69: "VAPOREON", 0x6A: "MACHOP", 0x6B: "ZUBAT",
    0x6C: "EKANS", 0x6D: "PARAS", 0x6E: "POLIWHIRL", 0x6F: "POLIWRATH",
    0x70: "WEEDLE", 0x71: "KAKUNA", 0x72: "BEEDRILL", 0x74: "DODRIO",
    0x75: "PRIMEAPE", 0x76: "DUGTRIO", 0x77: "VENOMOTH", 0x78: "DEWGONG",
    0x7B: "CATERPIE", 0x7C: "METAPOD", 0x7D: "BUTTERFREE", 0x7E: "MACHAMP",
    0x80: "GOLDUCK", 0x81: "HYPNO", 0x82: "GOLBAT", 0x83: "MEWTWO",
    0x84: "SNORLAX", 0x85: "MAGIKARP", 0x88: "MUK", 0x8A: "KINGLER",
    0x8B: "CLOYSTER", 0x8D: "ELECTRODE", 0x8E: "CLEFABLE", 0x8F: "WEEZING",
    0x90: "PERSIAN", 0x91: "MAROWAK", 0x93: "HAUNTER", 0x94: "ABRA",
    0x95: "ALAKAZAM", 0x96: "PIDGEOTTO", 0x97: "PIDGEOT", 0x98: "STARMIE",
    0x99: "BULBASAUR", 0x9A: "VENUSAUR", 0x9B: "TENTACRUEL", 0x9D: "GOLDEEN",
    0x9E: "SEAKING", 0xA3: "PONYTA", 0xA4: "RAPIDASH", 0xA5: "RATTATA",
    0xA6: "RATICATE", 0xA7: "NIDORINO", 0xA8: "NIDORINA", 0xA9: "GEODUDE",
    0xAA: "PORYGON", 0xAB: "AERODACTYL", 0xAD: "MAGNEMITE",
    0xB0: "CHARMANDER", 0xB1: "SQUIRTLE", 0xB2: "CHARMELEON",
    0xB3: "WARTORTLE", 0xB4: "CHARIZARD", 0xB9: "ODDISH", 0xBA: "GLOOM",
    0xBB: "VILEPLUME", 0xBC: "BELLSPROUT", 0xBD: "WEEPINBELL",
    0xBE: "VICTREEBEL",
}

# Items that restore HP, in the order the game lists them.  Used to decide
# whether a "heal" action is worth offering at all.
_HEALING_ITEMS = ("FULL RESTORE", "MAX POTION", "HYPER POTION", "SUPER POTION", "POTION")

# Field moves - the only reason the in-game menu still needs to be reachable
# once START is gone.  Move ids match GEN1_MOVES.
_FIELD_MOVES = {15: "CUT", 19: "FLY", 57: "SURF", 70: "STRENGTH", 91: "DIG", 148: "FLASH"}


def read_bag(state):
    """Return the bag as [(name, qty)], newest bridge only.

    Older bridges don't report the bag at all; they return an empty list, and
    every caller treats that as "unknown" rather than "empty".
    """
    bag = []
    for entry in state.get("bag", []) or []:
        if not isinstance(entry, dict):
            continue
        item_id = entry.get("id", 0)
        name = GEN1_ITEMS.get(item_id, f"Item #{item_id}")
        bag.append((name, entry.get("qty", 1)))
    return bag


_BALL_NAMES = {"POKE BALL", "GREAT BALL", "ULTRA BALL", "MASTER BALL", "SAFARI BALL"}


def has_balls(bag):
    """True if the bag holds anything it can throw at a wild Pokemon.

    An empty bag means "the bridge didn't tell us", not "it has nothing", so
    every caller has to check the bag is non-empty before trusting a False.
    """
    return any(name.strip().upper() in _BALL_NAMES for name, _ in bag)


def format_bag_line(bag):
    """One-line bag summary for the overworld header, or "" if the bag is empty.

    The agent used to open START just to find out whether it had a Potion, see that
    it didn't, close it, and do it again next turn.  Showing the bag up front
    is what makes removing the menu safe.
    """
    if not bag:
        return ""
    parts = []
    for name, qty in bag:
        parts.append(f"{name} x{qty}" if qty > 1 else name)
    return f"[Bag: {', '.join(parts)}]"


def party_field_moves(state):
    """Return [(party_index, mon_name, move_name)] for every field move known."""
    found = []
    for idx, mon in enumerate(state.get("party", []) or []):
        name = party_mon_name(mon)
        for move_id in mon.get("moves", []) or []:
            move = _FIELD_MOVES.get(move_id)
            if move:
                found.append((idx, name, move))
    return found

# The TOWN MAP is the one bag item that opens a full-screen graphic instead of
# text - the bridge reads it as mode "overworld" with no menu, so every action
# after that silently does nothing and the agent gets stuck on a dead screen.  It is
# intercepted in the USE/TOSS handler and answered with this text instead.
# Contents are the Kanto geography printed on the map itself, nothing more - # no objectives, no "go here next".  Keys are display_name values from the map
# database so _TOWN_MAP_REGION can be looked up directly by current location.
_TOWN_MAP_REGION = {
    "Pallet Town": "Route 1 north to Viridian City. Route 21 south is open water to Cinnabar Island (needs SURF).",
    "Viridian City": "Route 1 south to Pallet Town. Route 2 north to Viridian Forest, then Pewter City. Route 22 west to Victory Road and Indigo Plateau.",
    "Pewter City": "Route 2 south to Viridian Forest and Viridian City. Route 3 east to Mt. Moon, then Route 4 to Cerulean City.",
    "Cerulean City": "Route 4 west to Mt. Moon and Pewter City. Route 5 south to Saffron City. Route 9 east to Rock Tunnel, then Route 10 to Lavender Town. Routes 24 and 25 north to Nugget Bridge and Bill's house.",
    "Vermilion City": "Route 6 north to Saffron City. Route 11 east to Diglett's Cave. The S.S. Anne docks here.",
    "Lavender Town": "Route 10 north to Rock Tunnel and Cerulean City. Route 8 west to Saffron City. Route 12 south towards Routes 13, 14, 15 and Fuchsia City.",
    "Saffron City": "The centre of Kanto. Route 5 north to Cerulean City, Route 6 south to Vermilion City, Route 7 west to Celadon City, Route 8 east to Lavender Town.",
    "Celadon City": "Route 7 east to Saffron City. Route 16 west to Cycling Road (Routes 17 and 18), which runs south to Fuchsia City.",
    "Fuchsia City": "Route 15 north-east towards Lavender Town. Route 18 north-west up Cycling Road to Celadon City. Route 19 south is open water past the Seafoam Islands to Route 20 and Cinnabar Island (needs SURF).",
    "Cinnabar Island": "Route 20 east across the water to the Seafoam Islands and Fuchsia City. Route 21 north across the water to Pallet Town (both need SURF).",
    "Indigo Plateau": "Reached from Viridian City by Route 22 west, then Victory Road.",
    # Not a town, but it sits on the map between two of them and shares a name
    # prefix with Viridian City - without its own entry the fallback below would
    # place the agent in the wrong spot.
    "Viridian Forest": "Inside Route 2, between Viridian City to the south and Pewter City to the north.",
}

# Ordered so the printed map reads roughly the way the region is laid out.
_TOWN_MAP_ORDER = [
    "Pallet Town", "Viridian City", "Pewter City", "Cerulean City",
    "Vermilion City", "Lavender Town", "Saffron City", "Celadon City",
    "Fuchsia City", "Cinnabar Island", "Indigo Plateau",
]


def _town_map_text(current_location):
    """Text rendering of the TOWN MAP screen.

    current_location is the friendly display name (e.g. "Viridian School
    House"); indoor maps are matched to their town by prefix so standing in a
    Pokemon Center still marks the right spot on the map.
    """
    here = None
    if current_location:
        clean = current_location.replace("’", "'").strip()
        if clean in _TOWN_MAP_REGION:
            here = clean
        else:
            # Indoor maps are named after their town ("Viridian Pokecenter",
            # "Viridian School House") - match on the town's first word.
            upper = clean.upper()
            for town in _TOWN_MAP_ORDER:
                if upper.startswith(town.split()[0].upper()):
                    here = town
                    break

    lines = ["You unfold the TOWN MAP and look at the KANTO region."]
    if here:
        lines.append(f"You are here: {here}.")
        lines.append(f"  {here} - {_TOWN_MAP_REGION[here]}")
        lines.append("")
    lines.append("The rest of the map:")
    for town in _TOWN_MAP_ORDER:
        if town == here:
            continue
        lines.append(f"  {town} - {_TOWN_MAP_REGION[town]}")
    lines.append("You fold the map back up.")
    return lines


# Grass tile IDs per tileset (tileset_id → set of grass tile IDs)
# Used to detect when the player is standing in tall grass
GRASS_TILES = {0: {0x52}, 3: {0x20}, 23: {0x45}}


# =========================================================================
# BFS Pathfinding (from pokemon_vision.py)
# =========================================================================

def bfs_pathfind(passability, start, goal):
    """BFS pathfinding on a passability grid.

    Args:
        passability: 2D list [y][x] of bool
        start: (mx, my) starting position
        goal: (mx, my) target position

    Returns list of direction strings: ["up", "right", "right", "down"]
    Returns [] if already at goal.
    Returns None if no path found.
    """
    sx, sy = start
    gx, gy = goal

    if sx == gx and sy == gy:
        return []

    rows = len(passability)
    cols = len(passability[0]) if rows > 0 else 0

    if not (0 <= sx < cols and 0 <= sy < rows):
        return None
    if not (0 <= gx < cols and 0 <= gy < rows):
        return None

    visited = set()
    visited.add((sx, sy))
    queue = deque([(sx, sy, [])])

    directions = [
        (0, -1, "up"),
        (0, 1, "down"),
        (-1, 0, "left"),
        (1, 0, "right"),
    ]

    while queue:
        cx, cy, path = queue.popleft()

        for dx, dy, name in directions:
            nx, ny = cx + dx, cy + dy

            if not (0 <= nx < cols and 0 <= ny < rows):
                continue
            if (nx, ny) in visited:
                continue

            # Goal can be impassable (e.g., a wall/counter we want to face)
            if (nx, ny) == (gx, gy):
                return path + [name]

            if not passability[ny][nx]:
                continue

            visited.add((nx, ny))
            queue.append((nx, ny, path + [name]))

    return None


def bfs_pathfind_adjacent(passability, start, goal):
    """Find path to a cell adjacent to the goal (for interacting with
    impassable objects like bookshelves, counters, NPCs).

    Returns (path, facing_direction) where facing_direction is the
    direction to face the goal from the final position.
    Returns ([], None) if no path found.
    """
    gx, gy = goal
    rows = len(passability)
    cols = len(passability[0]) if rows > 0 else 0

    adjacents = [
        (gx, gy + 1, "up"),     # below goal, face up (preferred)
        (gx, gy - 1, "down"),   # above goal, face down
        (gx - 1, gy, "right"),  # left of goal, face right
        (gx + 1, gy, "left"),   # right of goal, face left
    ]

    best_path = None
    best_face = None

    for ax, ay, face_dir in adjacents:
        if not (0 <= ax < cols and 0 <= ay < rows):
            continue
        if not passability[ay][ax]:
            continue

        path = bfs_pathfind(passability, start, (ax, ay))
        if path is not None:
            if best_path is None or len(path) < len(best_path):
                best_path = path
                best_face = face_dir

    if best_path is not None:
        return best_path, best_face
    return [], None


HAS_BFS = True  # Always True since BFS is now inline


# =========================================================================
# Module-level Functions (from pokemon_text.py)
# All kept as module-level functions since they operate on state dicts.
# =========================================================================

def decode_gen1_name(byte_list):
    """Decode a Gen 1 encoded name (list of byte ints) into a string."""
    if not byte_list:
        return "???"
    return "".join(GEN1_CHARS.get(b, "") for b in byte_list).strip() or "???"


def party_mon_name(mon):
    """Display name for a party member, falling back to its species.

    Earlier sessions answered the "give a nickname?" prompt by mashing A, so
    several Pokémon are stored as "AAAAAAAAAA".  The species byte is still
    correct, so a junk nickname is shown as the real name instead.  Real
    nicknames are kept and annotated with the species.
    """
    nick = decode_gen1_name(mon.get("name_bytes", []))
    species = GEN1_SPECIES.get(mon.get("species", 0), "")
    if not species:
        return nick
    if nick == "???" or (len(nick) >= 4 and len(set(nick)) == 1):
        return species
    if nick.upper() != species:
        return f"{nick} ({species})"
    return nick


def get_status_name(status_byte):
    """Decode Gen 1 status byte into display string."""
    if status_byte == 0:
        return None
    if status_byte & 0x07:  # bits 0-2 = sleep counter
        return "SLP"
    if status_byte & 0x08:
        return "PSN"
    if status_byte & 0x10:
        return "BRN"
    if status_byte & 0x20:
        return "FRZ"
    if status_byte & 0x40:
        return "PAR"
    return None


def _hp_descriptor(hp, max_hp):
    """Return HP percentage and status label for unambiguous LLM display.

    Examples: '(97% FULL)', '(45% MID)', '(12% LOW)', '(0% FAINTED)'
    """
    if not isinstance(hp, (int, float)) or not isinstance(max_hp, (int, float)):
        return ""
    if max_hp <= 0:
        return ""
    pct = int(100 * hp / max_hp)
    if hp <= 0:
        label = "FAINTED"
    elif pct >= 75:
        label = "FULL"
    elif pct >= 40:
        label = "MID"
    elif pct >= 15:
        label = "LOW"
    else:
        label = "CRITICAL"
    return f" ({pct}% {label})"


def decode_screen_text(tile_map):
    """Decode wTileMap (360 tile IDs) into lines of text.
    Returns non-empty lines found on screen, stripped of blanks.
    """
    if not tile_map or len(tile_map) < 360:
        return []
    lines = []
    for row in range(18):
        chars = []
        for col in range(20):
            tid = tile_map[row * 20 + col]
            ch = GEN1_CHARS.get(tid, "")
            chars.append(ch)
        line = "".join(chars).strip()
        if line:
            lines.append(line)
    return lines


def send_command(cmd):
    """Write a command to cmd.txt for the Lua bridge to read."""
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CMD_FILE, "w") as f:
        f.write(cmd + "\n")


def send_commands(cmds):
    """Write multiple commands to cmd.txt -- Lua queues them all at once."""
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CMD_FILE, "w") as f:
        for cmd in cmds:
            f.write(cmd + "\n")


# ── Gen 1/2 Keyboard Input ─────────────────────────────────────────────

def kb_find(ch):
    """Find (row, col) of a letter in the keyboard grid, or None."""
    for r, row in enumerate(KB_GRID):
        for c, cell in enumerate(row):
            if cell == ch:
                return (r, c)
    return None


def kb_read_cursor():
    """Read current keyboard letter from bridge state."""
    s = read_state()
    return (s.get("keyboard_letter", "") if s else "").strip()


def kb_nav_wait(cmd):
    """Send a nav command and wait for keyboard_letter to change."""
    old = kb_read_cursor()
    send_command(cmd)
    for _ in range(12):  # poll up to 1.2s
        time.sleep(0.1)
        cur = kb_read_cursor()
        if cur != old:
            return cur
    return kb_read_cursor()


def type_on_keyboard(name):
    """Navigate the Pokemon keyboard to type a name, then confirm.
    Uses bridge keyboard_letter feedback so it works regardless of
    the exact grid layout. First letter uppercase, rest lowercase.
    """
    name = name.strip()[:7]  # Gen 1 max name length is 7
    if not name:
        send_command("START")  # confirm default name
        time.sleep(0.5)
        return

    # Capitalize first letter only: "ash" -> "Ash", "ASH" -> "Ash"
    name = name[0].upper() + name[1:].lower()
    need_lowercase = len(name) > 1

    # Read starting position
    cur_letter = kb_read_cursor().upper()
    cur_pos = kb_find(cur_letter)
    if cur_pos is None:
        cur_pos = (0, 0)

    # --- Type first letter (uppercase, batch nav) ---
    first = name[0].upper()
    tgt = kb_find(first)
    if tgt:
        cmds = []
        dr = tgt[0] - cur_pos[0]
        dc = tgt[1] - cur_pos[1]
        if dr > 0:
            cmds.extend(["NAV:DOWN"] * dr)
        elif dr < 0:
            cmds.extend(["NAV:UP"] * (-dr))
        if dc > 0:
            cmds.extend(["NAV:RIGHT"] * dc)
        elif dc < 0:
            cmds.extend(["NAV:LEFT"] * (-dc))
        cmds.append("INTERACT")
        send_commands(cmds)
        time.sleep(len(cmds) * 0.16 + 0.3)
        cur_pos = tgt

    # --- Toggle to lowercase if needed (step-by-step with verification) ---
    if need_lowercase:
        # Navigate LEFT to col 0 first
        for _ in range(cur_pos[1]):
            kb_nav_wait("NAV:LEFT")
        # Navigate DOWN until bridge reports "CASE"
        for _ in range(8):
            kl = kb_nav_wait("NAV:DOWN")
            if kl == "CASE":
                send_command("INTERACT")
                time.sleep(0.3)
                break
        # Navigate back UP until on a known letter
        cur_pos = None
        for _ in range(8):
            kl = kb_nav_wait("NAV:UP")
            pos = kb_find(kl.upper())
            if pos is not None:
                cur_pos = pos
                break
        if cur_pos is None:
            cur_pos = (0, 0)

    # --- Type remaining letters (batch nav from known position) ---
    if len(name) > 1:
        cmds = []
        cur_r, cur_c = cur_pos
        for ch in name[1:]:
            ch_upper = ch.upper()
            if not ("A" <= ch_upper <= "Z"):
                continue
            tgt = kb_find(ch_upper)
            if tgt is None:
                continue
            dr = tgt[0] - cur_r
            dc = tgt[1] - cur_c
            if dr > 0:
                cmds.extend(["NAV:DOWN"] * dr)
            elif dr < 0:
                cmds.extend(["NAV:UP"] * (-dr))
            if dc > 0:
                cmds.extend(["NAV:RIGHT"] * dc)
            elif dc < 0:
                cmds.extend(["NAV:LEFT"] * (-dc))
            cmds.append("INTERACT")
            cur_r, cur_c = tgt

        cmds.append("START")
        send_commands(cmds)
        time.sleep(len(cmds) * 0.16 + 0.5)
    else:
        send_command("START")
        time.sleep(0.5)


def read_state():
    """Read the current state.json from the bridge. Returns dict or None."""
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        # Filter keyboard grid artifacts from tolk output globally
        if "tolk_output" in state:
            state["tolk_output"] = _filter_keyboard_junk(state["tolk_output"])
        return state
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _filter_keyboard_junk(lines):
    """Filter out single-character lines (keyboard grid artifacts from pokemon-access)."""
    return [l for l in lines if len(l.strip()) > 1]


def read_text_file():
    """Read and clear the text.txt intercepted output."""
    lines = []
    try:
        with open(TEXT_FILE, "r") as f:
            content = f.read()
        if content.strip():
            lines = [l for l in content.strip().split("\n") if l.strip()]
        # Truncate the file
        with open(TEXT_FILE, "w") as f:
            f.write("")
    except (FileNotFoundError, OSError):
        pass
    return _filter_keyboard_junk(lines)


def add_to_history(lines):
    """Add lines to the text history buffer."""
    for line in lines:
        TEXT_HISTORY.append(line)
    while len(TEXT_HISTORY) > MAX_HISTORY:
        TEXT_HISTORY.pop(0)


def wait_for_state_change(old_state, timeout=10.0, poll_interval=0.1):
    """Poll state.json until it changes or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        state = read_state()
        if state and state != old_state:
            return state
        time.sleep(poll_interval)
    return read_state()


def wait_for_walk_complete(timeout=60.0):
    """Poll state.json until auto_walk becomes false or steps reach 0."""
    start = time.time()
    while time.time() - start < timeout:
        state = read_state()
        if state is None:
            time.sleep(0.2)
            continue
        if not state.get("auto_walk", False):
            return state
        steps = state.get("walk_steps_left", 0)
        if steps == 0:
            return state
        time.sleep(0.15)
    return read_state()


# ── VBA-RR Window Automation ─────────────────────────────────────────────

def find_vba_window(timeout=15.0, pid=None):
    """Find the VBA-RR main window handle, waiting up to timeout seconds.
    If pid is provided, matches by process ID (most reliable).
    Otherwise falls back to title matching.
    """
    start = time.time()
    get_window_pid = ctypes.windll.user32.GetWindowThreadProcessId if hasattr(ctypes, "windll") else None

    while time.time() - start < timeout:
        result = []

        def callback(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value

                    if pid:
                        lpdw = ctypes.wintypes.DWORD()
                        get_window_pid(hwnd, ctypes.byref(lpdw))
                        if lpdw.value == pid:
                            result.append(hwnd)
                            return True
                    elif "VisualBoyAdvance" in title or "VBA" in title:
                        result.append(hwnd)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(callback), 0)
        if result:
            return result[0]
        time.sleep(0.5)
    return None


def find_lua_dialog(timeout=10.0):
    """Find the 'Lua Script' dialog window that opens after menu command."""
    start = time.time()
    while time.time() - start < timeout:
        result = []

        def callback(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value
                    if "Lua Script" in title:
                        result.append(hwnd)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(callback), 0)
        if result:
            return result[0]
        time.sleep(0.3)
    return None


def find_child_by_class(parent_hwnd, class_name, nth=0):
    """Find the nth child window with the given class name."""
    found = []

    def callback(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        if buf.value == class_name:
            found.append(hwnd)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    user32.EnumChildWindows(parent_hwnd, WNDENUMPROC(callback), 0)
    if nth < len(found):
        return found[nth]
    return None


def find_child_by_text(parent_hwnd, text_contains):
    """Find a child window whose text contains the given string."""
    found = []

    def callback(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if text_contains.lower() in buf.value.lower():
                found.append(hwnd)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    user32.EnumChildWindows(parent_hwnd, WNDENUMPROC(callback), 0)
    return found[0] if found else None


def dump_dialog_children(hwnd):
    """Debug: list all children of a dialog."""
    children = []

    def callback(child_hwnd, _):
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(child_hwnd, cls_buf, 256)
        length = user32.GetWindowTextLengthW(child_hwnd)
        text = ""
        if length > 0:
            txt_buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(child_hwnd, txt_buf, length + 1)
            text = txt_buf.value
        ctrl_id = user32.GetDlgCtrlID(child_hwnd)
        children.append((child_hwnd, cls_buf.value, text, ctrl_id))
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    user32.EnumChildWindows(hwnd, WNDENUMPROC(callback), 0)
    return children


def auto_load_lua_script(pid=None):
    """Automatically load pokemon_bridge.lua in VBA-RR via Win32 automation.

    Returns True if successful, False if failed (falls back to manual instructions).
    """
    script_path = str(LUA_SCRIPT)

    vba_hwnd = find_vba_window(timeout=15, pid=pid)
    if not vba_hwnd:
        return False

    # Send the menu command to open "New Lua Script Window"
    user32.PostMessageW(vba_hwnd, WM_COMMAND, VBA_LUA_NEW_WINDOW_CMD_ID, 0)
    time.sleep(1.0)

    # Find the Lua Script dialog
    lua_hwnd = find_lua_dialog(timeout=10)
    if not lua_hwnd:
        return False

    # Debug: enumerate children to understand the dialog layout
    children = dump_dialog_children(lua_hwnd)

    # Find the Edit control (script file path input)
    edit_hwnd = find_child_by_class(lua_hwnd, "Edit", 0)
    if not edit_hwnd:
        # Try RichEdit variants
        for cls in ["RichEdit20W", "RichEdit20A", "EDIT"]:
            edit_hwnd = find_child_by_class(lua_hwnd, cls, 0)
            if edit_hwnd:
                break

    if not edit_hwnd:
        return False

    # Set the script path in the edit control
    user32.SendMessageW.argtypes = [ctypes.wintypes.HWND, ctypes.c_uint, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
    user32.SendMessageW.restype = ctypes.wintypes.LPARAM
    path_buf = ctypes.create_unicode_buffer(script_path)
    user32.SendMessageW(edit_hwnd, WM_SETTEXT, 0, ctypes.cast(path_buf, ctypes.c_void_p).value)
    time.sleep(0.3)

    # Find and click the Run/OK button
    run_hwnd = None
    for btn_text in ["Run", "OK", "Open", "Load"]:
        run_hwnd = find_child_by_text(lua_hwnd, btn_text)
        if run_hwnd:
            break

    if not run_hwnd:
        for i in range(5):
            btn = find_child_by_class(lua_hwnd, "Button", i)
            if btn:
                length = user32.GetWindowTextLengthW(btn)
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(btn, buf, length + 1)
                if buf.value.lower() not in ("browse", "browse..."):
                    run_hwnd = btn
                    break

    if run_hwnd:
        user32.SendMessageW(run_hwnd, BM_CLICK, 0, 0)
        time.sleep(0.5)
        return True
    else:
        return False


# ── Object preparation ──────────────────────────────────────────────────

def prepare_objects(objects):
    """Clean object list for display: remove signposts, deduplicate same-name warps."""
    filtered = [obj for obj in objects if obj.get("type") != "signpost"]

    seen_warps = {}
    result = []
    for obj in filtered:
        if obj.get("type") == "warp":
            name = obj.get("name", "???")
            nums = [int(n) for n in obj.get("dist", "").split() if n.isdigit()]
            total_dist = sum(nums) if nums else 0
            if name in seen_warps:
                prev_obj, prev_dist = seen_warps[name]
                if total_dist < prev_dist:
                    result = [r for r in result if r is not prev_obj]
                    seen_warps[name] = (obj, total_dist)
                    result.append(obj)
            else:
                seen_warps[name] = (obj, total_dist)
                result.append(obj)
        else:
            result.append(obj)
    return result


def auto_explore():
    """When no objects nearby, walk in each direction until objects appear.
    If a step changes map (warp), stop and let the caller handle the new map.
    Returns True if objects were found or map changed, False if stuck.
    """
    directions = ["down", "left", "right", "up"]

    state = read_state()
    if not state:
        return False
    start_map = state.get("map_name", "")

    for d in directions:
        for _ in range(5):
            state = read_state()
            if not state:
                time.sleep(0.2)
                continue
            if state.get("objects", []):
                return True

            old_x = state.get("player_x", -1)
            old_y = state.get("player_y", -1)

            send_command(f"PRESS:{d}")
            time.sleep(0.3)

            state = read_state()
            if not state:
                time.sleep(0.2)
                continue

            if state.get("map_name", "") != start_map:
                return True

            if (state.get("player_x", -1), state.get("player_y", -1)) == (old_x, old_y):
                break

    return False


# ── Map-database overworld navigation ────────────────────────────────────

def _dist_str(dx, dy):
    """Build human-readable direction string."""
    parts = []
    if abs(dy) > 0:
        parts.append(f"{abs(dy)} {'Down' if dy > 0 else 'Up'}")
    if abs(dx) > 0:
        parts.append(f"{abs(dx)} {'Right' if dx > 0 else 'Left'}")
    return " ".join(parts) if parts else "Here"


def _is_same_building(current_name, dest_name):
    """Check if dest is a different floor of the same building."""
    pattern = r'\s*B?\d+F$'
    base_cur = re.sub(pattern, '', current_name).strip()
    base_dest = re.sub(pattern, '', dest_name).strip()
    return base_cur and base_dest and base_cur == base_dest


def get_map_objects(state, map_db_data):
    """Get objects from map database + passability for BFS pathfinding.

    Args:
        state: Bridge state dict.
        map_db_data: Dict with 'id_lookup', 'name_lookup', 'available' keys,
                     plus 'pokeball_names' and 'interacted_objects' from the
                     PokemonGame instance.

    Returns list of object dicts.
    """
    if not map_db_data.get("available"):
        return prepare_objects(state.get("objects", []))

    px = state.get("player_x", -1)
    py = state.get("player_y", -1)
    if px < 0 or py < 0:
        return prepare_objects(state.get("objects", []))

    map_data = _get_map_data(state, map_db_data)
    if not map_data:
        return prepare_objects(state.get("objects", []))

    # Deep-copy passability grid so we can mark NPC cells
    raw_pass = map_data.get("passability", [])
    passability = [row[:] for row in raw_pass]

    pokeball_names = map_db_data.get("pokeball_names", {})

    objects = []
    MAX_DIST = 12
    # Warps get a much longer leash than signs and NPCs.  A map exit is a fixed
    # feature the player can see and remember, and BFS already pathfinds across
    # the whole grid - but at 12 tiles the Viridian Forest gates (34x48 map,
    # north gate at 1,0) never appeared in Nearby, so the agent could only walk five
    # tiles at a time through a maze and went in circles.
    MAX_WARP_DIST = 60

    # Warps
    current_name = map_data.get("display_name", "")
    seen_warp_names = {}
    for w in map_data.get("warps", []):
        wx, wy = w["x"], w["y"]
        dist = abs(wx - px) + abs(wy - py)
        if dist > MAX_WARP_DIST:
            continue
        dest = w["dest"]
        display = dest.replace("Blue's", "Rival's").replace("Red's House", "Your House").replace("Red's", "Your")
        if display != "Outside" and _is_same_building(current_name, dest):
            display = "Stairs"
        # Gate doorways register two warp tiles side by side but only one half
        # is walkable (the Viridian Forest gates have x=4 as wall, x=5 as door).
        # Rank walkable tiles ahead of distance, otherwise the nearest warp is
        # the wall half and the agent ends up one tile over pressing into it.
        walkable = (0 <= wy < len(passability) and 0 <= wx < len(passability[0])
                    and passability[wy][wx])
        rank = (0 if walkable else 1, dist)
        if display in seen_warp_names:
            if rank >= seen_warp_names[display]["_warp_rank"]:
                continue
            objects = [o for o in objects if o is not seen_warp_names[display]]
        obj = {
            "name": display, "type": "warp",
            "x": wx, "y": wy,
            "dist": _dist_str(wx - px, wy - py),
            "_map_obj": True, "_tile_type": "warp",
            "_dist_val": dist,
            "_warp_rank": rank,
        }
        seen_warp_names[display] = obj
        objects.append(obj)

    # Signs / interactables
    for s in map_data.get("signs", []):
        sx, sy = s["x"], s["y"]
        dist = abs(sx - px) + abs(sy - py)
        if dist > MAX_DIST or dist == 0:
            continue
        objects.append({
            "name": s["label"], "type": "object",
            "x": sx, "y": sy,
            "dist": _dist_str(sx - px, sy - py),
            "_map_obj": True, "_tile_type": "bookshelf",
            "_dist_val": dist,
        })

    # Live objects: preserve specific bridge names (PC, named NPCs, items).
    # Only use the map database to supply a label when the bridge has none.
    db_npcs = map_data.get("npcs", [])
    SPRITE_RENAMES = {"Blue": "Rival", "Red": "Player", "Item": "Poke Ball"}
    GENERIC_OBJECT_NAMES = {"", "???", "NPC", "Object", "Unknown"}
    for obj in state.get("objects", []):
        obj_type = obj.get("type", "")
        if obj_type in ("warp", "signpost", "connection"):
            continue
        nx = obj.get("x", -1)
        ny = obj.get("y", -1)
        if nx < 0 or ny < 0:
            continue
        dist = abs(nx - px) + abs(ny - py)
        if dist == 0 or dist > MAX_DIST:
            continue
        live_label = (obj.get("name") or "").strip()
        best_label = live_label or "NPC"
        best_dist = 999
        best_stays = True
        for db_npc in db_npcs:
            d = abs(db_npc["x"] - nx) + abs(db_npc["y"] - ny)
            if d < best_dist:
                best_dist = d
                best_stays = db_npc.get("stays", True)
                if live_label in GENERIC_OBJECT_NAMES:
                    best_label = db_npc["sprite"]
        display_name = SPRITE_RENAMES.get(best_label, best_label)
        # If this is a Poke Ball we've already checked, show the Pokemon name
        if display_name == "Poke Ball" and (nx, ny) in pokeball_names:
            display_name = f"Poke Ball ({pokeball_names[(nx, ny)]})"
        # Poke Balls (items) ALWAYS stay - force True regardless of NPC db match
        if "Poke Ball" in display_name or best_label == "Item":
            best_stays = True
        # Mark NPC cell as impassable for pathfinding
        if 0 <= ny < len(passability) and 0 <= nx < len(passability[0]):
            passability[ny][nx] = False
        # PCs are wall-mounted and only activate from the tile directly below
        # them while facing up. Treat them like bookshelves/signs so navigation
        # does not choose the equally adjacent tile to their left or right.
        interaction_tile = "bookshelf" if display_name.upper() == "PC" else "counter"
        objects.append({
            "name": display_name, "type": "object",
            "x": nx, "y": ny,
            "dist": _dist_str(nx - px, ny - py),
            "_map_obj": True, "_tile_type": interaction_tile,
            "_stays": best_stays,
            "_dist_val": dist,
        })

    # Also add bridge connections (route exits)
    for obj in state.get("objects", []):
        if obj.get("type") == "connection":
            objects.append(obj)

    # Store passability for BFS pathfinding
    state["_passability"] = passability
    state["_player_pos"] = (px, py)

    # Sort by distance, cap at 10
    objects.sort(key=lambda o: o.get("_dist_val", abs(o.get("x", 0) - px) + abs(o.get("y", 0) - py)))
    shown = objects[:10]

    # Warps now reach much further than everything else, so on a crowded map
    # they can be pushed out of the top 10 by nearby NPCs - which is exactly
    # the case (Viridian Forest) this is meant to fix.  Reserve the two
    # closest exits, dropping the furthest non-warps to make room.
    if len(objects) > len(shown):
        warps = [o for o in objects if o.get("type") == "warp"][:2]
        missing = [w for w in warps if w not in shown]
        if missing:
            keep = [o for o in shown if o.get("type") != "warp"]
            keep = keep[:max(0, 10 - len([o for o in shown if o.get("type") == "warp"]) - len(missing))]
            shown = [o for o in shown if o.get("type") == "warp"] + keep + missing
            shown.sort(key=lambda o: o.get("_dist_val", 0))

    return shown


def _get_map_data(state, map_db_data):
    """Look up map data from bridge state (by map_id, then display_name)."""
    id_lookup = map_db_data.get("id_lookup", {})
    name_lookup = map_db_data.get("name_lookup", {})
    mid = state.get("map_id")
    if isinstance(mid, int) and mid in id_lookup:
        return id_lookup[mid]
    return name_lookup.get(state.get("map_name", ""))


def map_walk_to(obj, state, pokeball_names):
    """Walk to an object using map-based BFS pathfinding.

    Returns (updated_state, screen_lines_or_none).
    screen_lines_or_none is a list of text lines if a stationary object
    was interacted with and screen text was decoded (e.g. Poke Ball).
    """
    passability = state.get("_passability")
    px, py = state.get("_player_pos", (state.get("player_x", 0), state.get("player_y", 0)))
    tx, ty = obj.get("x", px), obj.get("y", py)
    tile_type = obj.get("_tile_type", "passable")

    if not passability or not HAS_BFS:
        return walk_to_object(obj, state), None

    start = (px, py)
    goal = (tx, ty)

    # For signs/bookshelves on walls: prefer approaching from below (face up)
    if tile_type == "bookshelf":
        path, face_dir = _pathfind_prefer_below(passability, start, goal)
    else:
        path, face_dir = bfs_pathfind_adjacent(passability, start, goal)

    if path is None or not face_dir:
        # Try direct path as fallback (warp tile might be walkable)
        path = bfs_pathfind(passability, start, goal)
        if path is not None:
            _send_moves(path)
            time.sleep(0.5)
            return read_state(), None
        return walk_to_object(obj, state), None

    # Build full move list: path + step(s) INTO the target
    if tile_type == "warp":
        all_moves = list(path) + [face_dir, face_dir]
    else:
        all_moves = list(path) + [face_dir]
    _send_moves(all_moves)

    # Check for wild encounter interruption during walk
    check = read_state()
    if check and check.get("mode") not in ("overworld", None):
        return check, None  # Battle/dialogue started - let caller handle it

    # For NPCs/signs: press A to trigger the interaction.
    screen_lines = None
    if tile_type in ("bookshelf", "counter"):
        stays = obj.get("_stays", True)
        time.sleep(0.3)
        send_command("INTERACT")

        if stays:
            # Stationary object (bookshelf, sign, Poke Ball, stationary NPC)
            # Read screen text by pressing A through pages
            prev_screen = ""
            all_screen_lines = []
            seen_lines = set()
            for _ in range(10):
                time.sleep(1.5)
                result = read_state()
                if result and result.get("mode") != "overworld":
                    break
                if result and result.get("tolk_output"):
                    break
                tm = result.get("tile_map", []) if result else []
                s_lines = decode_screen_text(tm)
                screen_text = "\n".join(s_lines)
                if s_lines and screen_text != prev_screen:
                    for line in s_lines:
                        if line not in seen_lines:
                            seen_lines.add(line)
                            all_screen_lines.append(line)
                    prev_screen = screen_text
                send_command("INTERACT")
            if all_screen_lines:
                screen_lines = all_screen_lines
                # If this was a Poke Ball, extract Pokemon name
                obj_name = obj.get("name", "")
                if "Poke Ball" in obj_name:
                    ox = obj.get("x", -1)
                    oy = obj.get("y", -1)
                    if ox >= 0 and oy >= 0 and all_screen_lines:
                        pokemon_name = all_screen_lines[0].strip()
                        if pokemon_name and pokemon_name.isalpha():
                            pokeball_names[(ox, oy)] = pokemon_name
            return read_state(), screen_lines
        else:
            # Wandering NPC - check for dialogue, if no response repath to new position
            npc_name = obj.get("name", "")
            for attempt in range(3):
                # Check if dialogue started
                for _ in range(5):
                    time.sleep(0.2)
                    result = read_state()
                    if result and result.get("mode") != "overworld":
                        return result, None
                    if result and result.get("tolk_output"):
                        return result, None
                if attempt >= 2:
                    break
                # NPC didn't respond - repath to their new position
                fresh = read_state()
                if not fresh or fresh.get("mode") != "overworld":
                    return fresh, None
                new_px = fresh.get("player_x", px)
                new_py = fresh.get("player_y", py)
                # Find NPC's updated position from bridge objects
                found = False
                for bridge_obj in fresh.get("objects", []):
                    if bridge_obj.get("type") in ("warp", "signpost", "connection"):
                        continue
                    bname = bridge_obj.get("name", "")
                    if bname == npc_name or bname in npc_name or npc_name in bname:
                        ntx, nty = bridge_obj.get("x", -1), bridge_obj.get("y", -1)
                        if ntx >= 0 and nty >= 0 and (ntx != new_px or nty != new_py):
                            new_path, new_face = bfs_pathfind_adjacent(passability, (new_px, new_py), (ntx, nty))
                            if new_path is not None and new_face:
                                _send_moves(list(new_path) + [new_face])
                                # Check for wild encounter during walk
                                post_walk = read_state()
                                if post_walk and post_walk.get("mode") not in ("overworld", None):
                                    return post_walk, None
                                send_command("INTERACT")
                                found = True
                                break
                if not found:
                    # Can't find NPC - try facing around and interacting
                    send_command(f"PRESS:{face_dir}")
                    time.sleep(0.2)
                    send_command("INTERACT")
            return read_state(), ["They walked away before you could talk to them."]

    elif tile_type != "warp":
        # NPC/object - press A to interact, repath up to 3 times
        npc_name = obj.get("name", "")
        for attempt in range(3):
            time.sleep(0.2)
            send_command("INTERACT")
            # Check for dialogue
            for _ in range(5):
                time.sleep(0.2)
                result = read_state()
                if result and result.get("mode") != "overworld":
                    return result, None
            if attempt >= 2:
                break
            # No dialogue - repath to NPC's new position
            fresh = read_state()
            if not fresh or fresh.get("mode") != "overworld":
                return fresh, None
            new_px = fresh.get("player_x", px)
            new_py = fresh.get("player_y", py)
            found = False
            for bridge_obj in fresh.get("objects", []):
                if bridge_obj.get("type") in ("warp", "signpost", "connection"):
                    continue
                bname = bridge_obj.get("name", "")
                if bname == npc_name or bname in npc_name or npc_name in bname:
                    ntx, nty = bridge_obj.get("x", -1), bridge_obj.get("y", -1)
                    if ntx >= 0 and nty >= 0 and (ntx != new_px or nty != new_py):
                        new_path, new_face = bfs_pathfind_adjacent(passability, (new_px, new_py), (ntx, nty))
                        if new_path is not None and new_face:
                            _send_moves(list(new_path) + [new_face])
                            # Check for wild encounter during walk
                            post_walk = read_state()
                            if post_walk and post_walk.get("mode") not in ("overworld", None):
                                return post_walk, None
                            found = True
                            break
            if not found:
                send_command(f"PRESS:{face_dir}")
                time.sleep(0.2)
        # 3 attempts failed
        return read_state(), ["They walked away before you could talk to them."]

    time.sleep(0.5)
    return read_state(), None


def _pathfind_prefer_below(passability, start, goal):
    """BFS to cell below goal first (face up = natural front).
    Falls back to other adjacent cells if below isn't reachable.
    """
    gx, gy = goal
    rows = len(passability)
    cols = len(passability[0]) if rows > 0 else 0

    by = gy + 1
    if 0 <= by < rows and 0 <= gx < cols and passability[by][gx]:
        path = bfs_pathfind(passability, start, (gx, by))
        if path is not None:
            return path, "up"

    return bfs_pathfind_adjacent(passability, start, goal)


def _focus_emulator(vba_hwnd=None):
    """Bring the VBA-RR emulator window to the foreground so input is received."""
    try:
        hwnd = vba_hwnd or find_vba_window(timeout=1)
        if hwnd:
            user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def _send_moves(moves, vba_hwnd=None):
    """Send movement commands one at a time, waiting for each step to complete."""
    if not moves:
        return
    for m in moves:
        send_command(f"PRESS:{m}")
        time.sleep(0.3)


def walk_to_object(obj, state):
    """Walk to an object using pathfinding with direct-walk fallback.
    Returns the updated state after walking.
    """
    send_command(f"PATHFINDXY:{obj['x']},{obj['y']}")
    time.sleep(0.5)
    check = read_state()
    if check and check.get("auto_walk", False):
        wait_for_walk_complete()
        send_command("INTERACT")
        time.sleep(0.5)
        return read_state()

    px = state.get("player_x", 0)
    py = state.get("player_y", 0)
    dx = obj.get("x", px) - px
    dy = obj.get("y", py) - py
    cmds = []
    for _ in range(min(abs(dy), 15)):
        cmds.append(f"PRESS:{'down' if dy > 0 else 'up'}")
    for _ in range(min(abs(dx), 15)):
        cmds.append(f"PRESS:{'right' if dx > 0 else 'left'}")
    if cmds:
        send_commands(cmds)
        time.sleep(len(cmds) * 0.2 + 0.3)
    send_command("INTERACT")
    time.sleep(0.5)
    return read_state()


def open_start_menu():
    """Send START and wait for the game to enter menu mode."""
    send_command("START")
    for _ in range(20):
        time.sleep(0.15)
        s = read_state()
        if s and s.get("mode") == "menu":
            return


def menu_nav_to(index, selected):
    """Move the menu cursor from `selected` to `index`.  Same delta walk as
    save_state() and the menu_select action."""
    if selected >= 0:
        delta = index - selected
        for _ in range(abs(delta)):
            send_command("NAV:DOWN" if delta > 0 else "NAV:UP")
            time.sleep(0.15)
    else:
        for _ in range(index):
            send_command("NAV:DOWN")
            time.sleep(0.15)


def menu_pick(targets, settle=0.4):
    """Select the first on-screen menu entry matching one of `targets`.

    Targets are compared uppercase against the entry with any trailing quantity
    stripped, the same way the item hints match.  Returns True on success.
    Only for short menus that fit on screen - the bag needs _scan_item_list.
    """
    time.sleep(settle)
    s = read_state()
    if not s or s.get("mode") != "menu":
        return False
    items, sel = get_menu_items(s)
    upper = [it.split("x")[0].strip().split("×")[0].strip().upper() for it in items]
    idx = -1
    for target in targets:
        if target in upper:
            idx = upper.index(target)
            break
    if idx < 0:
        return False
    menu_nav_to(idx, sel)
    send_command("INTERACT")
    time.sleep(0.4)
    return True


# ── Dialogue auto-advance ───────────────────────────────────────────────

def auto_advance_dialogue(initial_state):
    """Auto-press A through dialogue, collecting all tolk text until an
    interactive mode appears (menu, keyboard, battle) or the game stays in
    overworld for a few seconds (dialogue is truly over).

    Returns (final_state, cleaned_lines).
    """
    collected = []
    seen = set()
    state = initial_state

    def collect_tolk(st):
        for line in st.get("tolk_output", []):
            clean = line.lstrip("\u2192").strip()
            if clean and clean not in seen:
                seen.add(clean)
                collected.append(clean)
                add_to_history([clean])

    collect_tolk(state)

    if not collected:
        for _ in range(20):
            time.sleep(0.1)
            s = read_state()
            if not s:
                continue
            collect_tolk(s)
            if collected:
                state = s
                break

    max_presses = 200
    overworld_since = None
    OVERWORLD_TIMEOUT = 5.0

    for _ in range(max_presses):
        state = read_state()
        if state is None:
            time.sleep(0.3)
            continue

        mode = state.get("mode", "")

        if mode in ("menu", "keyboard", "battle"):
            collect_tolk(state)
            break

        if mode == "dialogue":
            overworld_since = None
            collect_tolk(state)
            send_command("INTERACT")
            for _ in range(15):
                time.sleep(0.1)
                s = read_state()
                if s is None:
                    continue
                m = s.get("mode", "")
                if m != "dialogue":
                    state = s
                    break
                if s.get("tolk_output"):
                    collect_tolk(s)
                    state = s
                    break

        elif mode in ("overworld", "loading"):
            if overworld_since is None:
                overworld_since = time.time()
            if time.time() - overworld_since > OVERWORLD_TIMEOUT:
                break
            time.sleep(0.3)

        else:
            send_command("INTERACT")
            time.sleep(0.5)

    # Filter out menu-item echoes and title screen remnants
    TITLE_ECHOES = {"NEW GAME", "CONTINUE", "OPTIONS"}
    if state:
        menu_set = {item.strip().upper() for item in state.get("menu_items", [])}
        menu_set |= TITLE_ECHOES
        if menu_set:
            collected = [l for l in collected if l.strip().upper() not in menu_set]
    else:
        collected = [l for l in collected if l.strip().upper() not in TITLE_ECHOES]

    return state, collected


# ── Battle helpers ──────────────────────────────────────────────────────

def _get_battle_info(state):
    """Extract battle data from bridge state, handling both dict and list formats."""
    battle = state.get("battle", {})
    if isinstance(battle, list):
        battle = {}
    return battle


def _battle_data_valid(state):
    """Check if battle RAM data is populated (names/HP loaded)."""
    battle = _get_battle_info(state)
    return battle.get("player_max_hp", 0) > 0 and battle.get("enemy_max_hp", 0) > 0


def _scripted_battle_kind(state):
    """Return "tutorial", "safari" or "" for the current battle.

    wBattleType (0xD05A): 0 normal, 1 the OLD MAN catching demo, 2 Safari Zone.
    The demo draws FIGHT/PKMN/ITEM/RUN and a real move list but plays itself and
    discards every input, which is why it looked like a hardcoded battle menu.
    Absent on older bridges, in which case this returns "" and the caller falls
    back to its own flag.
    """
    flag = _get_battle_info(state).get("battle_type_flag", 0)
    if flag == 1:
        return "tutorial"
    if flag == 2:
        return "safari"
    return ""


def auto_advance_scripted_battle(initial_state):
    """Press A through a battle the game is playing for us, collecting text.

    Returns (final_state, lines).  Mirrors auto_advance_battle_text, but never
    stops to offer a choice - the whole point is that choices do nothing here.
    """
    state = initial_state
    seen = set()
    lines = []
    for _ in range(60):
        for line in state.get("tolk_output", []) if state else []:
            clean = line.lstrip("→").strip()
            if clean and clean not in seen:
                seen.add(clean)
                lines.append(clean)
        if state and state.get("mode") != "battle":
            break
        send_command("INTERACT")
        time.sleep(0.5)
        state = read_state() or state
    return state, _dedup_progressive_text(lines)


def _format_battle_stats(state):
    """Format battle stats as a string block."""
    battle = _get_battle_info(state)
    enemy_name = decode_gen1_name(battle.get("enemy_name", []))
    player_name = decode_gen1_name(battle.get("player_name", []))
    enemy_lv = battle.get("enemy_level", "?")
    player_lv = battle.get("player_level", "?")
    enemy_hp = battle.get("enemy_hp", "?")
    enemy_max = battle.get("enemy_max_hp", "?")
    player_hp = battle.get("player_hp", "?")
    player_max = battle.get("player_max_hp", "?")
    battle_type = battle.get("battle_type", 0)
    enemy_status = get_status_name(battle.get("enemy_status", 0))
    player_status = get_status_name(battle.get("player_status", 0))
    type_label = "Wild" if battle_type == 1 else "Trainer"
    es = f" [{enemy_status}]" if enemy_status else ""
    ps = f" [{player_status}]" if player_status else ""
    enemy_hp_desc = _hp_descriptor(enemy_hp, enemy_max)
    player_hp_desc = _hp_descriptor(player_hp, player_max)
    return (f"{type_label}: {enemy_name} Lv{enemy_lv}  HP: {enemy_hp}/{enemy_max}{enemy_hp_desc}{es}\n"
            f"Yours: {player_name} Lv{player_lv}  HP: {player_hp}/{player_max}{player_hp_desc}{ps}")


def _get_battle_options(state):
    """Read battle menu options from bridge-extracted battle_menu."""
    items = state.get("battle_menu", [])
    if not items:
        return []
    result = [r.strip() for r in items if r and r.strip() and r.strip() not in ("-", "--")]
    return result


def _cancel_to_battle_menu(state):
    """Press CANCEL once at a time until we're back at the battle menu."""
    for _ in range(5):
        send_command("CANCEL")
        time.sleep(0.3)
        state = read_state() or state
        if state.get("mode") != "battle":
            return state
        if state.get("battle_menu"):
            return state
    return state


def _find_items_on_screen(state, items):
    """Find cursor arrow and all menu item positions from the tile map."""
    tile_map = state.get("tile_map", [])
    if not tile_map or len(tile_map) < 360 or not items:
        return -1, {}, None

    ARROW_TILE = 0xED

    named_items = []
    for i, item in enumerate(items):
        name = (item or "").split("(")[0].strip().upper()
        if name and name not in ("-", "--"):
            named_items.append((name, i))

    if not named_items:
        return -1, {}, None

    current_idx = -1
    positions = {}

    # Pass 1: find arrow tiles
    for row in range(10, 18):
        for col in range(0, 19):
            if tile_map[row * 20 + col] != ARROW_TILE:
                continue
            text_chars = []
            for c in range(col + 1, 20):
                tid = tile_map[row * 20 + c]
                ch = GEN1_CHARS.get(tid, "")
                text_chars.append(ch)
            text = "".join(text_chars).strip().upper()
            if not text:
                continue
            for name, idx in named_items:
                if text.startswith(name) or name.startswith(text):
                    current_idx = idx
                    positions[idx] = (row, col)
                    break

    # Pass 2: find positions of OTHER items
    # Track actual tile columns alongside compressed string so that
    # positions[idx] stores (row, tile_col), not (row, string_pos).
    for row in range(10, 18):
        line_chars = []
        col_map = []  # col_map[string_idx] = tile_column
        for col in range(0, 20):
            tid = tile_map[row * 20 + col]
            ch = GEN1_CHARS.get(tid, "")
            if ch:
                col_map.append(col)
                line_chars.append(ch)
        line_text = "".join(line_chars).upper()
        for name, idx in named_items:
            if idx in positions:
                continue
            pos = line_text.find(name)
            if pos >= 0:
                actual_col = col_map[pos] if pos < len(col_map) else pos
                positions[idx] = (row, actual_col)

    return current_idx, positions, "tiles" if current_idx >= 0 else None


def _detect_battle_cursor(state, fallback_options=None):
    """Detect current battle cursor position from screen tiles or tolk output."""
    # Prefer explicit options over state's battle_menu - the state may still
    # have the main menu (FIGHT/PKMN/ITEM/RUN) while we're in the move menu,
    # or vice versa. Callers pass the correct options for the active menu.
    items = fallback_options or state.get("battle_menu", [])
    if not items:
        return -1, {}, None

    current, positions, source = _find_items_on_screen(state, items)
    if current >= 0:
        return current, positions, source

    tolk = state.get("tolk_output", [])
    if not tolk:
        return -1, {}, None

    named_items = []
    for i, item in enumerate(items):
        name = (item or "").split("(")[0].strip().upper()
        if name and name not in ("-", "--"):
            named_items.append((name, i))
    if not named_items:
        return -1, {}, None

    for line in reversed(tolk):
        clean = line.lstrip("\u2192").strip().upper()
        if not clean:
            continue
        for name, grid_idx in named_items:
            if clean == name or name.startswith(clean) or clean.startswith(name):
                return grid_idx, {}, "tolk"

    return -1, {}, None


def _battle_select(state, target_idx, options=None):
    """Navigate main battle menu (FIGHT/PKMN/ITEM/RUN) to target_idx and press A.

    Uses the BATTLE: bridge command which resets cursor to top-left (UP+LEFT)
    then navigates to the target position in the 2x2 grid.  The main battle
    menu does not wrap, so this is always reliable.
    """
    print(f"  [DEBUG] BATTLE:{target_idx} (bridge 2x2 grid select)")
    send_command(f"BATTLE:{target_idx}")


def _move_menu_select(current_cursor, target_idx, num_moves):
    """Navigate the move sub-menu from current_cursor to target_idx and press A.

    Uses tracked cursor position instead of unreliable screen detection.
    Navigates with UP/DOWN only (move menu is a vertical list).

    Args:
        current_cursor: Where the cursor is right now (tracked from last selection).
        target_idx: Which move to select (0-based).
        num_moves: Total number of moves available.

    Returns:
        None. Sends nav commands to the bridge.
    """
    if current_cursor == target_idx:
        print(f"  [DEBUG] Move cursor already at {target_idx} (tracked), pressing A")
        send_command("INTERACT")
        return

    delta = target_idx - current_cursor
    cmds = []
    if delta > 0:
        cmds = ["NAV:DOWN"] * delta
    else:
        cmds = ["NAV:UP"] * (-delta)
    cmds.append("INTERACT")
    print(f"  [DEBUG] Move cursor at {current_cursor} (tracked), target {target_idx}, sending: {cmds}")
    send_commands(cmds)


def _party_menu_select(current_idx, target_idx, party_count):
    """Select an in-battle party slot relative to the active Pokémon.

    The bridge's ``menu_selected`` is a rendered text-row index, not a party
    slot.  Pokémon Blue's party cursor also *wraps*, so repeated UP presses do
    not reset it to slot zero.  When the battle party menu opens, its saved
    cursor is on the active Pokémon; navigate from that real party index and
    use the shorter wrapping direction.
    """
    count = max(1, party_count)
    safe_current = current_idx if 0 <= current_idx < count else 0
    safe_target = max(0, min(target_idx, count - 1))
    down_steps = (safe_target - safe_current) % count
    up_steps = (safe_current - safe_target) % count
    if up_steps < down_steps:
        cmds = ["NAV:UP"] * up_steps
    else:
        cmds = ["NAV:DOWN"] * down_steps
    cmds.append("INTERACT")
    print(f"  [DEBUG] Party cursor at active slot {safe_current}, "
          f"target {safe_target}, sending: {cmds}")
    send_commands(cmds)


def _dedup_progressive_text(lines):
    """Remove partial-text fragments from progressive screen reader output.

    Screen readers emit text as it's drawn character-by-character:
      "BULBA", "BULBAS", "BULBASAUR", "used T", "used TACKLE!"
    This keeps only the longest/complete version of each text chunk.

    Removes a line if it's a substring of ANY other line in the batch.
    """
    if not lines or len(lines) <= 1:
        return lines
    result = []
    for i, line in enumerate(lines):
        # Skip if this line is contained within any other (longer) line
        is_fragment = any(
            line in other and len(other) > len(line)
            for j, other in enumerate(lines) if j != i
        )
        if not is_fragment:
            result.append(line)
    return result


# Phrases the party screen prints inside a battle: after your Pokémon faints,
# and after the opponent's does ("Will you switch?" -> the party list).  Same
# phrases the PKMN branch already watches for.
_PARTY_PROMPTS = ("BRING OUT WHICH", "CHOOSE A POK", "ALREADY OUT")


def _party_select_showing(state, extra_lines=None):
    """True when the in-battle party list is on screen awaiting a pick."""
    lines = list(state.get("tolk_output", []) or [])
    lines += list(state.get("text_lines", []) or [])
    if extra_lines:
        lines += list(extra_lines)
    joined = " ".join(lines).upper()
    return any(p in joined for p in _PARTY_PROMPTS)


# "TRAINER is about to use MON.  Will RED change POKéMON?" - a plain YES/NO
# box, so neither extract_menu_items() (skipped during battle) nor
# extract_battle_menu() (needs the 2x2 column divider) reports it.  Nothing
# stopped auto_advance pressing A, which is YES, so every time an opponent
# swapped the agent was dumped into the party list it never asked for.
_SWITCH_PROMPTS = ("CHANGE POK",)


def _switch_prompt_showing(state, extra_lines=None):
    """True when the optional "change POKéMON?" YES/NO box is up."""
    lines = list(state.get("tolk_output", []) or [])
    lines += list(state.get("text_lines", []) or [])
    if extra_lines:
        lines += list(extra_lines)
    joined = " ".join(lines).upper()
    return any(p in joined for p in _SWITCH_PROMPTS)


def _yes_no_cursor_on_no(state):
    """True when the arrow in a YES/NO box is parked on NO.

    Gen 1 draws these boxes with the cursor on YES, but read it rather than
    assume it - answering the price prompt with the wrong half silently
    cancels a purchase.
    """
    for line in state.get("tolk_output", []) or []:
        clean = line.strip()
        if clean.startswith("→") and clean.lstrip("→").strip().upper() == "NO":
            return True
    return False


def _active_party_index(state):
    """Party slot of the Pokémon currently out in battle, or -1.

    Matched off the battle struct (nickname + level + max HP) rather than a
    new RAM read.  Needed because picking the Pokémon that is already out
    just makes the game say "X is already out!" and redraw the party list - which read to the agent as an unbreakable loop.
    """
    battle = _get_battle_info(state)
    if not battle:
        return -1
    active_name = decode_gen1_name(battle.get("player_name", []))
    active_level = battle.get("player_level", 0)
    active_max = battle.get("player_max_hp", 0)
    if not active_max:
        return -1
    for i, mon in enumerate(state.get("party", []) or []):
        if (decode_gen1_name(mon.get("name_bytes", [])) == active_name
                and mon.get("level", 0) == active_level
                and mon.get("max_hp", 0) == active_max):
            return i
    return -1


# The mart's quantity spinner: a lone "×01" row, with the running total
# merged onto it by get_menu_items().  The bag list has "×NN" rows too, but
# always underneath an item name, so a one-row list is the tell.
def _is_qty_box(items):
    """True when `items` is the mart buy/sell quantity spinner."""
    real = [it for it in items if it.strip() and it.strip().upper() != "CANCEL"]
    if len(real) != 1:
        return False
    head = real[0].split("¥")[0].strip()
    return bool(_QUANTITY_RE.match(head))


# The blinking ▼ Gen 1 draws in the corner of a textbox once the line has
# finished printing and it is waiting on A.  Read straight out of the tile map
# the bridge already exports.
_TEXT_PROMPT_TILE = 0xEE


def _wait_for_text_prompt(state, tries=10):
    """Wait for the textbox to finish printing.  Returns the settled state.

    Gives up after ~0.6s and returns whatever it has: some battle text scrolls
    away on its own and never draws the prompt, and waiting on it forever would
    stall the battle.
    """
    for _ in range(tries):
        if _TEXT_PROMPT_TILE in (state.get("tile_map") or []):
            return state
        time.sleep(0.06)
        state = read_state() or state
        if state.get("mode") != "battle" or _battle_input_needed(state):
            return state
    return state


def _battle_input_needed(state):
    """Screens inside a battle that need a real choice, not another A press.

    The move menu, the name-entry keyboard, the party list, and the "change
    POKéMON?" box.  Pressing A on the last three picked for the agent: A's typed
    into a nickname, whichever Pokémon the cursor happened to sit on sent
    into a gym fight, and YES to a switch it never wanted.
    """
    return bool(state.get("battle_menu")) or bool(state.get("keyboard")) \
        or _party_select_showing(state) or _switch_prompt_showing(state)


def auto_advance_battle_text(state):
    """Auto-advance through all battle text until a menu appears or text clears.

    Returns (final_state, collected_lines).

    The name-entry keyboard counts as a menu.  Catching a Pokémon answers YES
    to "give a nickname?" and lands on the keyboard while the game still
    reports mode "battle" - pressing A there types A's into the nickname.
    """
    collected = []
    seen = set()

    def collect(st):
        for line in st.get("tolk_output", []):
            clean = line.strip()
            if clean and clean not in seen:
                seen.add(clean)
                collected.append(clean)
                add_to_history([clean])

    def collect_text_lines(st):
        for line in st.get("text_lines", []):
            clean = line.strip()
            if clean and clean not in seen:
                seen.add(clean)
                collected.append(clean)
                add_to_history([clean])

    collect(state)
    if not collected:
        collect_text_lines(state)

    if not collected:
        for _ in range(20):
            time.sleep(0.1)
            s = read_state()
            if not s:
                continue
            if s.get("mode") != "battle":
                return s, collected
            if _battle_input_needed(s):
                return s, collected
            collect(s)
            if not collected:
                collect_text_lines(s)
            if collected:
                state = s
                break

    idle_count = 0
    for _ in range(60):
        state = read_state() or state
        if state.get("mode") != "battle":
            return state, collected
        if _battle_input_needed(state):
            return state, collected

        if idle_count >= 8 and _battle_data_valid(state):
            battle = _get_battle_info(state)
            enemy_hp = battle.get("enemy_hp", 1)
            if enemy_hp > 0:
                break  # mid-battle idle, safe to break
            # enemy fainted - keep pressing A for victory/EXP text
            if idle_count >= 15:
                break  # ultimate safety valve

        # Brief pause before pressing A - gives the bridge time to export
        # state if the battle menu just appeared.  Without this, an A press
        # can queue up and blow past a newly-drawn menu (especially after
        # multi-line battle text like "not very effective" + enemy attack).
        time.sleep(0.15)
        state = read_state() or state
        if state.get("mode") != "battle":
            return state, collected
        if _battle_input_needed(state):
            return state, collected

        # Let the line finish printing, then collect it.  A press part-way
        # through the typewriter wipes the box, which is why lines arrived
        # clipped ("BULBASAUR's" / "attack m").  The full line is a superset of
        # the fragment, so the substring filter below folds the two together.
        state = _wait_for_text_prompt(state)
        if state.get("mode") != "battle":
            return state, collected
        if _battle_input_needed(state):
            return state, collected
        collect(state)

        send_command("INTERACT")
        got_new = False
        for _w in range(15):
            time.sleep(0.1)
            s = read_state()
            if not s:
                continue
            if s.get("mode") != "battle":
                return s, collected
            if _battle_input_needed(s):
                return s, collected
            new_tolk = s.get("tolk_output", [])
            old_count = len(collected)
            if new_tolk:
                collect(s)
            if len(collected) == old_count:
                collect_text_lines(s)
            if len(collected) > old_count:
                got_new = True
                idle_count = 0
                state = s
                break
        if not got_new:
            idle_count += 1
            state = read_state() or state
            if state.get("mode") != "battle":
                break
            if _battle_input_needed(state):
                break

    # Filter partial-text duplicates: remove any line that is a substring
    # of a longer collected line (handles both prefix fragments like
    # "fai"→"fainted!" and mid-string fragments like "to level 10!"
    # being part of "BULBASAUR grew to level 10!")
    if len(collected) > 1:
        filtered = []
        for i, line in enumerate(collected):
            if any(line in other and len(other) > len(line)
                   for j, other in enumerate(collected) if j != i):
                continue
            filtered.append(line)
        collected = filtered

    return state, collected


# ── In-game menu helpers ────────────────────────────────────────────────

def get_menu_items(state):
    """Get menu items from state, falling back to tolk if needed."""
    items = state.get("menu_items", [])
    selected = state.get("menu_selected", -1)
    if not items:
        tolk = state.get("tolk_output", [])
        seen = set()
        for l in tolk:
            clean = l.lstrip("\u2192").strip()
            if clean and clean not in seen:
                seen.add(clean)
                items.append(clean)
        selected = 0
    # Mart lists draw each entry on two lines - the item name, then its price
    # underneath - and the bridge reports both lines as separate menu items.
    # Fold the price back into the name so one entry == one cursor row;
    # otherwise every index below the first item is off by one and selecting
    # an item buys the one above it.
    if any(_is_price_line(item) for item in items):
        merged = []
        index_map = []  # bridge index -> merged index
        for item in items:
            if _is_price_line(item) and merged:
                merged[-1] = f"{merged[-1].rstrip()}  {item.strip()}"
            else:
                merged.append(item)
            index_map.append(len(merged) - 1)
        if 0 <= selected < len(index_map):
            selected = index_map[selected]
        items = merged
    # Deduplicate while preserving order (bridge can repeat CANCEL etc.)
    seen = set()
    deduped = []
    for item in items:
        key = item.strip().upper()
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped, selected


def _is_quantity_line(text):
    """Check if text is just an item count like 'x 1', 'x2', 'x 3'."""
    return bool(_QUANTITY_RE.match(text.strip()))


def _is_price_line(text):
    """Check if text is just a mart price like '¥200'."""
    return bool(_PRICE_RE.match(text.strip()))


def _read_arrow_item_and_qty(state):
    """Read the currently arrow-selected item name and quantity from tile_map."""
    tile_map = state.get("tile_map", [])
    if not tile_map or len(tile_map) < 360:
        return None, None

    ARROW_TILE = 0xED

    for row in range(0, 18):
        for col in range(0, 19):
            if tile_map[row * 20 + col] != ARROW_TILE:
                continue
            name_chars = []
            for c in range(col + 1, 20):
                tid = tile_map[row * 20 + c]
                ch = GEN1_CHARS.get(tid, "")
                name_chars.append(ch)
            name = "".join(name_chars).strip()
            if not name:
                continue
            if name.upper() == "CANCEL":
                return "CANCEL", None
            qty = None
            if row + 1 < 18:
                for c2 in range(0, 20):
                    if tile_map[(row + 1) * 20 + c2] == 0xF1:
                        digits = []
                        for c3 in range(c2 + 1, 20):
                            tid2 = tile_map[(row + 1) * 20 + c3]
                            if 0xF6 <= tid2 <= 0xFF:
                                digits.append(str(tid2 - 0xF6))
                            elif tid2 == 0x7F:
                                continue
                            else:
                                break
                        if digits:
                            qty = int("".join(digits))
                        break
            return name, qty

    return None, None


def _scan_item_list(state):
    """Navigate through an item list menu, reading name+qty for each entry.

    Returns (items, cursor_index).
    """
    state = read_state() or state
    start_name, start_qty = _read_arrow_item_and_qty(state)
    if start_name is None:
        return [], -1

    scanned = []
    scanned.append((start_name, start_qty))

    prev_name = start_name
    for _ in range(21):
        send_command("NAV:DOWN")
        # Wait longer than a single frame export interval - when the list
        # scrolls past the visible window the entire tile map redraws,
        # which takes more frames than a simple cursor move.
        time.sleep(0.35)
        name, qty = None, None
        for _retry in range(5):
            state = read_state() or state
            name, qty = _read_arrow_item_and_qty(state)
            if name is not None and name != prev_name:
                break  # got a new item
            # Stale read (mid-scroll) or same item - wait and retry
            time.sleep(0.15)
            name, qty = None, None
        if name is None:
            break  # genuinely couldn't read after retries
        if name == start_name:
            break  # wrapped around to the beginning
        scanned.append((name, qty))
        prev_name = name

    cancel_idx = None
    for i, (name, _) in enumerate(scanned):
        if name.upper() == "CANCEL":
            cancel_idx = i
            break

    if cancel_idx is not None:
        above = scanned[cancel_idx + 1:]
        below = scanned[:cancel_idx]
        ordered = above + below + [scanned[cancel_idx]]
    else:
        ordered = scanned

    items = []
    for name, qty in ordered:
        if name.upper() == "CANCEL":
            items.append("CANCEL")
        elif qty is not None:
            items.append(f"{name}  x{qty}")
        else:
            items.append(name)

    cursor_index = 0
    for i, (name, _) in enumerate(ordered):
        if name == start_name:
            cursor_index = i
            break

    return items, cursor_index


def _item_menu_select_by_name(target_name, list_len):
    """Move the bag arrow onto target_name and press A.  Returns True on success.

    Counting NAV presses does not work here.  The presses are queued to the
    bridge and get dropped when the list redraws, so "press DOWN once from the
    top" can leave the arrow where it started -- and pressing A on the item
    above a Poke Ball is what produced twelve turns of "can't use POKe BALL
    right now".  Read the arrow back after every press instead, the same
    read-after-press the surrounding scan already does, and only confirm once
    the arrow is provably on the right item.

    Gen 1 item lists wrap, so walking DOWN always reaches the target.
    """
    target = (target_name or "").split("x")[0].strip().split("×")[0].strip().upper()
    steps = max(4, (list_len + 1) * 2)

    for step in range(steps):
        name, _qty = None, None
        for _retry in range(5):
            st = read_state()
            if not st:
                time.sleep(0.15)
                continue
            name, _qty = _read_arrow_item_and_qty(st)
            if name is not None:
                break
            time.sleep(0.15)

        if name is not None and name.strip().upper() == target:
            print(f"  [DEBUG] Bag arrow on {name!r} after {step} step(s), pressing A")
            send_command("INTERACT")
            return True

        if name is None:
            print("  [DEBUG] Bag arrow unreadable -- aborting item select")
            return False

        send_command("NAV:DOWN")
        time.sleep(0.35)

    print(f"  [DEBUG] Never landed on {target!r} in {steps} steps -- aborting")
    return False


def _read_visible_item_list(state):
    """Read ALL visible items from the tile_map without pressing any buttons.

    Scans the screen for item entries by finding the arrow cursor column,
    then reading all rows at that column for item text + quantities.
    Returns (items, cursor_index) - same interface as _scan_item_list.
    """
    tile_map = state.get("tile_map", [])
    if not tile_map or len(tile_map) < 360:
        return [], -1

    ARROW_TILE = 0xED

    # First pass: find the arrow to determine the item name column
    arrow_row = -1
    arrow_col = -1
    for row in range(0, 18):
        for col in range(0, 19):
            if tile_map[row * 20 + col] == ARROW_TILE:
                arrow_row = row
                arrow_col = col
                break
        if arrow_row >= 0:
            break

    if arrow_row < 0:
        return [], -1

    name_col = arrow_col + 1  # item names start one column after the arrow column

    # Second pass: scan ALL rows at the arrow column position for item text
    # In Gen 1 item lists, each item takes 2 rows: name row + quantity row
    # The arrow column has ARROW_TILE on selected row, blank (0x7F) on others
    scanned = []   # list of (row_index, name, qty)
    cursor_index = -1
    skip_rows = set()  # rows to skip (quantity rows already consumed)

    for row in range(0, 18):
        if row in skip_rows:
            continue

        tile_at_arrow_col = tile_map[row * 20 + arrow_col]

        # Check if this row has text starting at the name column
        # Item rows either have the arrow tile or a space tile (0x7F) at the arrow column
        if tile_at_arrow_col != ARROW_TILE and tile_at_arrow_col != 0x7F:
            continue

        # Try to read item name starting at name_col
        name_chars = []
        for c in range(name_col, 20):
            tid = tile_map[row * 20 + c]
            ch = GEN1_CHARS.get(tid, "")
            name_chars.append(ch)
        name = "".join(name_chars).strip()
        if not name:
            continue

        # Skip if this looks like a quantity line (starts with x/× followed by digits)
        if _QUANTITY_RE.match(name):
            continue

        # Track cursor position
        if tile_at_arrow_col == ARROW_TILE:
            cursor_index = len(scanned)

        if name.upper() == "CANCEL":
            scanned.append((row, "CANCEL", None))
            continue

        # Check next row for quantity (0xF1 = "x" prefix)
        qty = None
        if row + 1 < 18:
            for c2 in range(0, 20):
                if tile_map[(row + 1) * 20 + c2] == 0xF1:
                    digits = []
                    for c3 in range(c2 + 1, 20):
                        tid2 = tile_map[(row + 1) * 20 + c3]
                        if 0xF6 <= tid2 <= 0xFF:
                            digits.append(str(tid2 - 0xF6))
                        elif tid2 == 0x7F:
                            continue
                        else:
                            break
                    if digits:
                        qty = int("".join(digits))
                        skip_rows.add(row + 1)  # mark quantity row as consumed
                    break

        scanned.append((row, name, qty))

    if not scanned:
        return [], -1

    # Build items list (no reordering needed - screen order is correct)
    items = []
    for _, name, qty in scanned:
        if name.upper() == "CANCEL":
            items.append("CANCEL")
        elif qty is not None:
            items.append(f"{name}  x{qty}")
        else:
            items.append(name)

    return items, cursor_index


# =========================================================================
# PokemonGame Class
# =========================================================================

class PokemonGame:
    """Wraps one Pokemon Red or Blue session for the run loop.

    All bridge I/O is run via asyncio.to_thread so the run loop's event
    loop is never blocked by the emulator polling.
    """

    def __init__(self, rom_path):
        self.game_key = GAME              # "red" or "blue"
        self.game_name = GAMES[GAME]["title"]
        self.process = None               # VBA-RR subprocess
        self.vba_hwnd = None              # Window handle
        self.first_battle_completed = False
        self.first_battle_seen = False
        self.in_battle = False
        self.in_tutorial = True
        self.accumulated_text = []
        self._pending_text = []        # dialogue/battle text awaiting next interactive state
        self.player_name = ""
        self.rival_name = ""
        self.current_location = ""
        self.current_map_id = None
        self._starting_house = ""       # raw map name of player's starting house
        self.interacted_objects = set()
        self.interacted_map_id = None
        # {(x, y)} - objects that were actually walked to twice with no
        # interaction resulting.  Observed failures only, never predicted.
        # Cleared on map change so re-entering an area gives a clean slate.
        self._blocked_objects = set()
        self.last_interacted_name = None
        self.last_warp_used = None
        self._just_left_location = ""   # location name before last warp (for "just left" label)
        self._just_left_remaining = 0   # turns remaining for "just left" label
        # (oak hint fields removed - replaced by dynamic direction hints)
        self._recent_actions = deque(maxlen=5)  # recent action log to prevent looping
        self._prev_state_text = ""  # track previous state to detect stuck/no-change
        self.map_db = None
        self.pokeball_names = {}          # {(x, y): "CHARMANDER", ...}

        # Battle sub-menu: when send_choice returns SUB_MENU, store the options
        # so get_state() can present them directly without auto-advancing
        self._pending_sub_menu = None  # list of option strings, or None

        # Track move cursor position - Gen 1 remembers the last-selected move
        # within a battle, resets to 0 at battle start
        self._move_cursor = 0

        # Battle outcome tracking (for the one-line battle summary)
        self._battle_opponent = ""        # e.g. "RATTATA"
        self._battle_type_label = ""      # "Wild" or "Trainer"
        self._battle_opponent_level = 0
        self._last_battle_outcome = ""    # summary string set when battle ends

        # Scripted demo battles (the Viridian City OLD MAN catching tutorial).
        # The game draws the normal battle menus but plays itself and ignores
        # every input.  Turn countdown used only when the running bridge is too
        # old to report wBattleType; see _looks_like_tutorial_battle().
        self._tutorial_hint_turns = 0

        # Consecutive battle sub-menus opened and backed out of without doing
        # anything.  The main battle menu redraws byte-identical each time, so
        # ITEM -> Back -> ITEM -> Back read to the agent as the game being stuck and
        # it sat in it for over an hour.  Same nudge idea as the overworld
        # stuck detection below.
        self._battle_cancel_streak = 0

        # Set when this battle has already refused a Poké Ball.  The catch tip
        # is generated from enemy HP alone, so it kept saying "you can try to
        # CATCH it" after the game had said no twelve times running.
        self._ball_refused_this_battle = False

        # Internal: map database lookups (populated by _init_map_db)
        self._map_id_lookup = {}
        self._map_name_lookup = {}
        self._map_available = False

        # The run's own ROM copy. VBA loads and writes the .sav beside it.
        self._rom_path = str(rom_path)
        self._rom_dir = Path(rom_path).parent

        # Item list scanning cache
        self._item_list_cache = None

        # Bill's PC deposit flow. The generic screen scraper splits the party
        # list into nickname/level rows and hides the DEPOSIT confirmation as a
        # short all-caps word. While this is set, use party RAM for labels and
        # drive the confirmation internally.
        self._pc_deposit_pending = False
        self._pc_party_cursor_parked = False

        # True between pressing USE on a bag item and the game resolving it.
        # While set, the party screen picks a target instead of auto-cancelling,
        # otherwise using a Potion with a single Pokémon backs out immediately.
        self._item_use_pending = False

        # Name of the bag entry the agent last picked, so the USE/TOSS handler knows
        # which item it is about to use (needed to intercept the TOWN MAP).
        self._selected_item_name = ""

        # Previous mode for cutscene detection
        self._prev_mode = "loading"

        # How far into the bridge's input log read_new_inputs() has read
        self._input_log_offset = 0

    # ── Save file management ─────────────────────────────────────────────

    def _rom_sav_path(self):
        """Return the expected .sav path in the ROM directory, or None."""
        if not self._rom_path:
            return None
        return Path(self._rom_path).with_suffix(".sav")

    def _clear_rom_sav(self):
        """Remove .sav from ROM dir for a fresh start."""
        rom_sav = self._rom_sav_path()
        if rom_sav and rom_sav.exists():
            rom_sav.unlink()
            print(f"  Cleared .sav for fresh start: {rom_sav}")

    # ── Battle outcome ────────────────────────────────────────────────────

    def _note_tutorial_dialogue(self, lines):
        """Arm the fallback demo-battle check from the OLD MAN's dialogue.

        Only used when the running bridge predates the wBattleType field.  The
        Viridian City old man asks "Are you in a hurry?" and answering NO starts
        its catching demonstration a turn or two later.
        """
        combined = " ".join(lines).upper()
        if "HURRY" in combined and "VIRIDIAN" in (self.current_location or "").upper():
            self._tutorial_hint_turns = 3

    def _looks_like_tutorial_battle(self, state):
        """Fallback demo detection for bridges without wBattleType.

        Deliberately narrow: the hint must be fresh, we must still be in
        Viridian City, and the opponent must be the WEEDLE the demo always
        uses.  A real wild battle can't satisfy all three - there is no grass
        in Viridian City.
        """
        if self._tutorial_hint_turns <= 0:
            return False
        if "VIRIDIAN CITY" not in (self.current_location or "").upper():
            return False
        enemy = decode_gen1_name(_get_battle_info(state).get("enemy_name", []))
        return enemy.upper() == "WEEDLE"

    def _compose_battle_outcome(self, battle_text):
        """Compose a one-line battle outcome summary from tracked data + end text."""
        opponent = self._battle_opponent or "unknown"
        type_label = self._battle_type_label or "Wild"
        level = self._battle_opponent_level

        # Scan battle_text for outcome keywords
        combined = " ".join(battle_text).upper() if battle_text else ""
        exp_match = ""
        for line in (battle_text or []):
            if "EXP" in line.upper():
                exp_match = line.strip()
                break

        if "RAN AWAY" in combined or "GOT AWAY" in combined:
            outcome = f"Encountered a {type_label.lower()} {opponent} (Lv{level}). Ran away safely."
        elif "CAUGHT" in combined:
            outcome = f"Encountered a {type_label.lower()} {opponent} (Lv{level}). Caught it!"
        elif exp_match:
            outcome = f"Encountered a {type_label.lower()} {opponent} (Lv{level}). Defeated it! {exp_match}"
        elif "FAINTED" in combined and opponent.upper() not in combined:
            # Player's Pokemon fainted
            outcome = f"Encountered a {type_label.lower()} {opponent} (Lv{level}). Lost the battle."
        else:
            outcome = f"Encountered a {type_label.lower()} {opponent} (Lv{level}). Battle ended."

        self._last_battle_outcome = outcome
        # Reset tracking for next battle
        self._battle_opponent = ""
        self._battle_type_label = ""
        self._battle_opponent_level = 0

    # ── Map database ────────────────────────────────────────────────────

    def _init_map_db(self):
        """Load map_database.json. Returns True if ready."""
        if self._map_available:
            return True
        if not MAP_DB_PATH.exists():
            return False
        try:
            with open(MAP_DB_PATH) as f:
                self.map_db = json.load(f)
            id_to_key = self.map_db.get("_id_to_key", {})
            for mid_str, key in id_to_key.items():
                entry = self.map_db.get(key)
                if entry:
                    self._map_id_lookup[int(mid_str)] = entry
            for key, val in self.map_db.items():
                if key.startswith("_") or not isinstance(val, dict):
                    continue
                # Tag outdoor maps: towns, cities, main routes, and a few specials.
                # ViridianForest reports as an indoor map (FOREST tileset) but is
                # navigated by compass like a route, so it must be tagged outdoor
                # or it loses its directional options entirely.
                # Caves are the same case, and worse.  Mt Moon, Rock Tunnel,
                # Victory Road and the rest are open areas you cross by compass,
                # but untagged they got no directional options at all. The only
                # thing in the Nearby list was the door it came in through, so
                # every trip into Mt Moon ended with it walking back out to
                # Route 3.  Tileset 17 is the cave tileset.
                val["_outdoor"] = (
                    key.endswith("Town") or key.endswith("City")
                    or bool(re.match(r"^Route\d+$", key))
                    or val.get("tileset_id") == CAVE_TILESET
                    or key in ("CinnabarIsland", "IndigoPlateau", "ViridianForest")
                )
                dn = val.get("display_name")
                if dn:
                    self._map_name_lookup[dn] = val
            self._map_available = True
            return True
        except Exception:
            return False

    def _get_map_db_data(self):
        """Return a dict usable by the module-level get_map_objects()."""
        return {
            "id_lookup": self._map_id_lookup,
            "name_lookup": self._map_name_lookup,
            "available": self._map_available,
            "pokeball_names": self.pokeball_names,
            "interacted_objects": self.interacted_objects,
        }

    # ── Interaction tracking ────────────────────────────────────────────

    def _mark_interacted(self, obj, current_map_id):
        """Mark an object as interacted. Clears tracking if map changed."""
        if current_map_id != self.interacted_map_id:
            self.interacted_objects.clear()
            self.interacted_map_id = current_map_id
        x, y = obj.get("x", -1), obj.get("y", -1)
        if x >= 0 and y >= 0:
            self.interacted_objects.add((x, y))

    # ── Location tracking ───────────────────────────────────────────────

    def _update_location(self, map_name):
        """Track the current location, naming the player's own house."""
        if not map_name:
            return
        # Clean brackets and normalize apostrophes for comparison
        clean = map_name.replace("[", "").replace("]", "").replace("\u2019", "'").strip()
        upper = clean.upper()
        # Map known player houses to "Your House"
        # Preset player names for this game + any custom name
        _PLAYER_HOUSE_NAMES = set(GAMES[GAME]["player_presets"])
        if self.player_name:
            _PLAYER_HOUSE_NAMES.add(self.player_name.upper())
        display_name = clean
        for name in _PLAYER_HOUSE_NAMES:
            if upper.startswith(name + "'S HOUSE"):
                display_name = "Your House"
                break
        if display_name == self.current_location:
            return
        self.current_location = display_name

    # ── Core async methods ──────────────────────────────────────────────

    async def launch(self, fresh_start=False):
        """Clean bridge files, patch the VBA ini, launch VBA-RR,
        auto-load Lua, auto-play title screen, load map_database.json.

        Args:
            fresh_start: If True, delete any existing .sav so VBA starts
                         a brand-new game instead of loading an old save.

        Returns True on success, False on failure.
        """
        def _launch_sync():
            BRIDGE_DIR.mkdir(parents=True, exist_ok=True)

            # Clean up stale bridge files
            for f in [STATE_FILE, CMD_FILE, TEXT_FILE, INPUT_LOG_FILE]:
                try:
                    f.unlink()
                except FileNotFoundError:
                    pass

            rom_path = self._rom_path
            if not rom_path or not Path(rom_path).exists():
                print(f"  ROM not found: {rom_path}")
                return False

            # Patch VBA-RR ini: point batteryDir at the ROM directory so VBA
            # loads .sav from the same place it naturally checks on startup.
            rom_dir_str = str(self._rom_dir)
            vba_ini = VBA_EXE.parent / "vba.ini"
            if vba_ini.exists():
                ini_text = vba_ini.read_text()
                ini_text = ini_text.replace("pauseWhenInactive=1", "pauseWhenInactive=0")
                ini_text = ini_text.replace("enableBackgroundInput=1", "enableBackgroundInput=0")
                ini_text = re.sub(r"soundEnable=\d+", "soundEnable=0", ini_text)
                ini_text = re.sub(r"soundVolume=\d+", "soundVolume=0", ini_text)
                ini_text = re.sub(r"batteryDir=.*", lambda _: f"batteryDir={rom_dir_str}", ini_text)
                ini_text = re.sub(r"saveDir=.*", lambda _: f"saveDir={rom_dir_str}", ini_text)
                # DirectDraw. Direct3D made the mouse cursor lag across the
                # whole desktop while the emulator was running.
                ini_text = re.sub(r"renderMethod=\d+", "renderMethod=1", ini_text)
                vba_ini.write_text(ini_text)

            # On fresh start, clear any existing .sav so VBA starts a new game
            if fresh_start:
                self._clear_rom_sav()

            if not VBA_EXE.exists():
                print(f"  VBA-RR not found: {VBA_EXE}")
                return False

            try:
                self.process = subprocess.Popen(
                    [str(VBA_EXE), rom_path],
                    cwd=str(VBA_EXE.parent),
                )
            except OSError:
                return False

            time.sleep(2)

            # Auto-load Lua bridge
            auto_load_lua_script(pid=self.process.pid)

            # Find the VBA window handle
            self.vba_hwnd = find_vba_window(timeout=15, pid=self.process.pid)

            # Wait for bridge connection
            start = time.time()
            ready = False
            while time.time() - start < 30:
                state = read_state()
                if state and state.get("ready"):
                    ready = True
                    break
                time.sleep(0.5)
            if not ready:
                print("  The Lua bridge did not start.")
                return False

            # Title screen is handled by get_game_state() which auto-selects
            # CONTINUE (if .sav exists) or NEW GAME. Just wait for bridge ready.
            # Press START a few times to get past the title animation.
            for _ in range(10):
                state = read_state()
                if not state:
                    time.sleep(0.5)
                    continue
                if state.get("mode") == "menu":
                    break  # Title menu appeared - get_game_state will handle it
                px = state.get("player_x", -1)
                if px > 0:
                    break  # Already in game
                send_command("START")
                time.sleep(1)

            # Auto-close any menu that's open on start
            state = read_state()
            if state and state.get("mode") == "menu":
                send_command("CANCEL")
                time.sleep(0.5)

            # Initialize map database
            self._init_map_db()

            return True

        return await asyncio.to_thread(_launch_sync)

    async def get_game_state(self):
        """Poll state.json, handle modes, return (formatted_text, choice_map, metadata).

        choice_map: dict mapping letter -> {"action": ..., "label": ..., ...}
        metadata: dict with keys like "mode", "needs_name", "battle_text", etc.
        """
        def _get_state_sync():
            # Read intercepted text
            new_text = read_text_file()
            if new_text:
                add_to_history(new_text)

            # Read current state
            state = read_state()
            if state is None:
                return "Lost connection to bridge...", {}, {"mode": "error"}

            mode = state.get("mode", "loading")
            map_name = state.get("map_name", "???")

            # Screen reader override
            tolk = state.get("tolk_output", [])
            if mode == "overworld" and tolk:
                tolk_upper = " ".join(l.upper() for l in tolk)
                if "SAVE" in tolk_upper and "EXIT" in tolk_upper:
                    mode = "menu"

            # Update location tracking
            self._update_location(map_name)

            # ── Loading mode ────────────────────────────────────────
            if mode == "loading":
                time.sleep(1)
                return "Game is loading...", {}, {"mode": "loading"}

            # ── Walking mode ────────────────────────────────────────
            if mode == "walking":
                steps = state.get("walk_steps_left", 0)
                if steps == 0:
                    send_command("STOP_WALK")
                    time.sleep(0.2)
                    send_command("INTERACT")
                    time.sleep(0.5)
                else:
                    wait_for_walk_complete(timeout=30)
                    send_command("INTERACT")
                    time.sleep(0.5)
                # Re-read state and process normally
                state = read_state()
                if state is None:
                    return "Lost connection to bridge...", {}, {"mode": "error"}
                mode = state.get("mode", "loading")
                map_name = state.get("map_name", "???")

            # ── Overworld: poll for transitions ─────────────────────
            if mode == "overworld":
                if self._prev_mode in ("keyboard", "menu", "dialogue"):
                    poll_count = 25
                else:
                    poll_count = 10
                for _ in range(poll_count):
                    time.sleep(0.2)
                    s = read_state()
                    if s and s.get("mode") not in ("overworld", "loading"):
                        state = s
                        mode = state.get("mode", "overworld")
                        break
                if mode != "overworld":
                    pass  # fall through to handle the new mode below

            # ── Overworld: title/intro auto-advance ─────────────────
            if mode == "overworld":
                px = state.get("player_x", -1)
                py = state.get("player_y", -1)
                if px <= 0 and py <= 0:
                    send_command("START")
                    time.sleep(0.5)
                    self._prev_mode = mode
                    return "Title/intro screen - auto-advancing...", {}, {"mode": "loading"}

            # ── Dialogue mode ──────────────────────────────────────
            if mode == "dialogue":
                pre_map = state.get("map_id", -1)
                all_lines = []
                while True:
                    state, lines = auto_advance_dialogue(state)
                    all_lines.extend(_dedup_progressive_text(lines))
                    mode = state.get("mode", "unknown") if state else "unknown"
                    if mode != "overworld":
                        break
                    post_map = state.get("map_id", -1) if state else -1
                    is_cutscene = post_map != pre_map
                    poll_time = 25 if is_cutscene else 5
                    more = False
                    for _ in range(poll_time):
                        time.sleep(0.2)
                        s = read_state()
                        if s and s.get("mode") == "dialogue":
                            more = True
                            state = s
                            break
                    if not more:
                        break

                # Format dialogue text - include any pending text from previous states
                text_parts = []
                if self._pending_text:
                    text_parts.extend(self._pending_text)
                    text_parts.append("")
                    self._pending_text = []
                if all_lines:
                    speaker = ""
                    has_name = any(":" in line and line.split(":")[0].strip().isalpha() for line in all_lines)
                    if not has_name and self.last_interacted_name:
                        speaker = self.last_interacted_name
                    if speaker:
                        text_parts.append(f"[{speaker}]")
                    for line in all_lines:
                        text_parts.append(line)
                    self.last_interacted_name = None
                    self.accumulated_text.extend(all_lines)

                    # Tutorial hint: Poke Ball description before Oak lets you pick
                    self._note_tutorial_dialogue(all_lines)

                    combined = " ".join(all_lines)
                    if (hint("story_notes") and "Those are" in combined
                            and "contain" in combined and "MON" in combined):
                        text_parts.append("[You looked at the Poké Balls but cannot take one yet - you need Professor Oak's permission first.]")


                # After dialogue, check if we landed on a mode that needs input
                if state:
                    mode = state.get("mode", "overworld")
                else:
                    mode = "overworld"

                if mode == "menu":
                    # YES/NO menu after dialogue
                    menu_items, selected = get_menu_items(state)

                    text_lines_ctx = state.get("text_lines", [])
                    if text_lines_ctx:
                        for line in text_lines_ctx:
                            text_parts.append(line)
                    choice_map = {}
                    letter_idx = 0
                    for i, item in enumerate(menu_items):
                        ch = chr(ord("A") + letter_idx)
                        choice_map[ch] = {
                            "action": "menu_select",
                            "index": i,
                            "selected": selected,
                            "total": len(menu_items),
                            "label": item,
                        }
                        text_parts.append(f"{ch}. {item}")
                        letter_idx += 1
                    ch = chr(ord("A") + letter_idx)
                    choice_map[ch] = {"action": "cancel", "label": "Back/Cancel"}
                    text_parts.append(f"{ch}. Back/Cancel")
                    self._prev_mode = "dialogue"
                    return "\n".join(text_parts), choice_map, {"mode": "menu_after_dialogue"}

                if mode == "keyboard":
                    self._prev_mode = "dialogue"
                    # Collect context for keyboard
                    kb_tolk = state.get("tolk_output", []) if state else []
                    kb_text = state.get("text_lines", []) if state else []
                    for line in kb_tolk:
                        clean = line.strip()
                        if clean:
                            text_parts.append(clean)
                    for line in kb_text:
                        text_parts.append(line)
                    return "\n".join(text_parts), {}, {"mode": "keyboard", "needs_name": True}

                if mode == "battle":
                    # Fall through to battle handling below
                    pass
                else:
                    # Landed back in overworld after dialogue
                    # Store text for next interactive state (overworld menu)
                    self._prev_mode = "dialogue"
                    if text_parts:
                        self._pending_text.extend(text_parts)
                    return None  # Retry - overworld handler will include the text

            # ── Battle mode ────────────────────────────────────────
            if mode != "battle" and self._pending_sub_menu:
                self._pending_sub_menu = None  # clear stale sub-menu if battle ended

            if mode == "battle":
                self.in_battle = True

                # Scripted demo battles play themselves.  The Viridian City OLD
                # MAN's catching tutorial draws the full battle UI and a real
                # move list, but the game supplies the inputs - anything the agent
                # picks is discarded, so it sat there choosing into a void.
                # Auto-advance it and hand back a narrated summary instead.
                if (_scripted_battle_kind(state) == "tutorial"
                        or self._looks_like_tutorial_battle(state)):
                    state, demo_lines = auto_advance_scripted_battle(state)
                    self.in_battle = False
                    self._tutorial_hint_turns = 0
                    self._pending_sub_menu = None
                    # Deliberately no _compose_battle_outcome() and no opponent
                    # tracking - this was not the agent's battle and must not be
                    # summarised as one.
                    self._battle_opponent = ""
                    self._battle_opponent_level = 0
                    self._battle_type_label = ""
                    if demo_lines:
                        self._pending_text.extend(demo_lines)
                        self.accumulated_text.extend(demo_lines)
                    if hint("story_notes"):
                        self._pending_text.append(
                            "[You watched a demonstration - the other person did all of it. "
                            "This was not your battle and none of your choices applied.]"
                        )
                    self._prev_mode = "battle"
                    return None  # Retry - next state shows the overworld

                # Reset move cursor at the start of each new battle
                if self._prev_mode != "battle":
                    self._move_cursor = 0
                    self._battle_cancel_streak = 0
                    self._ball_refused_this_battle = False

                # Track opponent info when first entering battle
                if self._prev_mode != "battle" and not self._battle_opponent:
                    battle_info = _get_battle_info(state)
                    if _battle_data_valid(state):
                        self._battle_opponent = decode_gen1_name(battle_info.get("enemy_name", []))
                        self._battle_opponent_level = battle_info.get("enemy_level", 0)
                        self._battle_type_label = "Wild" if battle_info.get("battle_type", 0) == 1 else "Trainer"

                # If a sub-menu is pending (player needs to pick a move/item),
                # present it directly WITHOUT auto-advancing battle text.
                # auto_advance presses A which would accidentally confirm a move.
                if self._pending_sub_menu:
                    sub_options = self._pending_sub_menu
                    text_parts = []
                    if self._pending_text:
                        text_parts.extend(self._pending_text)
                        text_parts.append("")
                        self._pending_text = []
                    if _battle_data_valid(state):
                        text_parts.append(_format_battle_stats(state))
                        text_parts.append("")
                    choice_map = {}
                    for i, opt in enumerate(sub_options):
                        ch = chr(ord("A") + i)
                        choice_map[ch] = {
                            "action": "battle_sub_select",
                            "index": i,
                            "label": opt,
                            "options": sub_options,
                        }
                        # Add item hints for non-move sub-menus
                        display_opt = opt
                        if "PP:" in opt:
                            # Mark empty moves so the agent knows not to pick them
                            if "(PP: 0)" in opt and hint("battle_tips"):
                                display_opt = opt.replace("(PP: 0)", "(PP: 0 EMPTY)")
                        elif hint("item_descriptions"):
                            opt_base = opt.split("x")[0].strip().split("×")[0].strip().upper()
                            item_hint = _ITEM_HINTS.get(opt_base, "")
                            if item_hint:
                                display_opt = f"{opt} - {item_hint}"
                        text_parts.append(f"{ch}. {display_opt}")
                    # Struggle guidance - when moves are running low
                    is_move_menu = any("PP:" in o for o in sub_options)
                    if is_move_menu and hint("battle_tips"):
                        has_pp = [o for o in sub_options if "PP:" in o and "(PP: 0)" not in o]
                        all_empty = len(has_pp) == 0
                        only_nondamage = (has_pp and all(
                            any(nd in o.upper() for nd in ("GROWL", "LEER", "TAIL WHIP", "STRING SHOT",
                                "SAND ATTACK", "HARDEN", "WITHDRAW", "DEFENSE CURL",
                                "DOUBLE TEAM", "MINIMIZE", "SMOKESCREEN", "FLASH",
                                "KINESIS", "SCREECH", "SUPERSONIC", "CONFUSE RAY"))
                            for o in has_pp
                        ))
                        if all_empty:
                            text_parts.append("[All moves are out of PP - select any move and the game will use STRUGGLE, a weak typeless attack that also deals recoil damage to you. PP refills when you heal at a Pokémon Center, so don't worry about running out permanently.]")
                        elif only_nondamage:
                            text_parts.append(f"[Some moves still have PP - use them! PP refills when you heal at a Pokémon Center, so there's no reason to save them. Use what you've got.]")

                    # Add cancel option
                    ch = chr(ord("A") + len(sub_options))
                    choice_map[ch] = {"action": "battle_sub_cancel", "label": "Back"}
                    text_parts.append(f"{ch}. Back")
                    self._prev_mode = "battle"
                    return "\n".join(text_parts), choice_map, {"mode": "battle", "state": state, "options": sub_options}

                state, battle_text = auto_advance_battle_text(state)
                battle_text = _dedup_progressive_text(battle_text)

                if state.get("keyboard"):
                    # Caught it - the nickname keyboard is up while the game
                    # still reports "battle".  Hand it to the agent to type a name;
                    # the battle is over either way.
                    self.in_battle = False
                    self._pending_sub_menu = None
                    if not self.first_battle_completed and self.first_battle_seen:
                        self.first_battle_completed = True
                    self._compose_battle_outcome(battle_text)
                    text_parts = []
                    if self._pending_text:
                        text_parts.extend(self._pending_text)
                        self._pending_text = []
                    if battle_text:
                        text_parts.extend(battle_text)
                        self.accumulated_text.extend(battle_text)
                    text_parts.append(
                        "[You caught it! Name entry is open - type a nickname "
                        "for your new Pokémon.]"
                    )
                    self._prev_mode = "keyboard"
                    return "\n".join(text_parts), {}, {
                        "mode": "keyboard", "needs_name": True, "nickname": True,
                    }

                if _party_select_showing(state, battle_text):
                    # The party list is up - either the agent's Pokémon fainted or the
                    # opponent sent out a new one.  It has to be answered with a
                    # pick, so present the party instead of pressing A.
                    text_parts = []
                    if self._pending_text:
                        text_parts.extend(self._pending_text)
                        self._pending_text = []
                    if battle_text:
                        text_parts.extend(battle_text)
                        self.accumulated_text.extend(battle_text)
                    # Text that landed in the same frame as the prompt (e.g.
                    # "TRAINER sent out SANDSHREW!") is still unread - the
                    # auto-advance bails before collecting it.
                    for line in state.get("tolk_output", []) or []:
                        clean = line.strip()
                        if clean and clean not in text_parts:
                            text_parts.append(clean)
                            self.accumulated_text.append(clean)
                    party = state.get("party", []) or []
                    active_idx = _active_party_index(state)
                    text_parts.append("")
                    text_parts.append("Choose a Pokémon to send out:")
                    choice_map = {}
                    for i, mon in enumerate(party):
                        ch = chr(ord("A") + i)
                        hp = mon.get("hp", 0)
                        max_hp = mon.get("max_hp", 0)
                        label = (f"{party_mon_name(mon)} Lv{mon.get('level', 0)} "
                                 f"({hp}/{max_hp} HP{_hp_descriptor(hp, max_hp)})")
                        if hp <= 0:
                            label += " - FAINTED, can't fight"
                        elif i == active_idx:
                            label += " - ALREADY OUT (picking this keeps it in)"
                        choice_map[ch] = {
                            "action": "party_select", "index": i,
                            "label": label, "party": party,
                            "active_index": active_idx,
                        }
                        text_parts.append(f"{ch}. {label}")
                    ch = chr(ord("A") + len(party))
                    choice_map[ch] = {"action": "party_cancel", "label": "Back",
                                      "active_index": active_idx}
                    text_parts.append(f"{ch}. Back (keep your current Pokémon "
                                      " - only works if you weren't forced to switch)")
                    self._prev_mode = "battle"
                    return "\n".join(text_parts), choice_map, {
                        "mode": "battle", "state": state, "options": [],
                    }

                if _switch_prompt_showing(state, battle_text):
                    # The opponent is swapping and the game is offering the agent a
                    # free switch.  Ask it - auto-advance used to answer YES
                    # and drop it into the party list every single time.
                    text_parts = []
                    if self._pending_text:
                        text_parts.extend(self._pending_text)
                        self._pending_text = []
                    if battle_text:
                        text_parts.extend(battle_text)
                        self.accumulated_text.extend(battle_text)
                    for line in state.get("tolk_output", []) or []:
                        clean = line.lstrip("→").strip()
                        if clean and clean not in text_parts:
                            text_parts.append(clean)
                            self.accumulated_text.append(clean)
                    active = ""
                    if _battle_data_valid(state):
                        active = decode_gen1_name(
                            _get_battle_info(state).get("player_name", []))
                    keep_label = f"Keep {active} in" if active else "Keep your current Pokémon"
                    text_parts.append("")
                    text_parts.append("The other trainer is sending out a new Pokémon. "
                                      "You may switch first - this is optional.")
                    choice_map = {
                        "A": {"action": "switch_prompt", "switch": True,
                              "label": "Switch to a different Pokémon"},
                        "B": {"action": "switch_prompt", "switch": False,
                              "label": keep_label},
                    }
                    text_parts.append("A. Switch to a different Pokémon")
                    text_parts.append(f"B. {keep_label} - the battle just continues")
                    self._prev_mode = "battle"
                    return "\n".join(text_parts), choice_map, {
                        "mode": "battle", "state": state, "options": [],
                    }

                if state.get("mode") != "battle":
                    # Battle ended - store text for next interactive state
                    self.in_battle = False
                    self._pending_sub_menu = None  # clear any stale sub-menu
                    if not self.first_battle_completed and self.first_battle_seen:
                        self.first_battle_completed = True

                    # Compose battle outcome summary
                    self._compose_battle_outcome(battle_text)

                    if battle_text:
                        self._pending_text.extend(battle_text)
                    self._pending_text.append("[Battle ended]")
                    self.accumulated_text.extend(battle_text)
                    self._prev_mode = "battle"
                    return None  # Retry - next state will include the text

                # Wait for valid battle data
                for _ in range(20):
                    if _battle_data_valid(state):
                        break
                    time.sleep(0.2)
                    state = read_state() or state

                # Track opponent once battle data is valid (may not have been ready earlier)
                if _battle_data_valid(state) and not self._battle_opponent:
                    battle_info = _get_battle_info(state)
                    self._battle_opponent = decode_gen1_name(battle_info.get("enemy_name", []))
                    self._battle_opponent_level = battle_info.get("enemy_level", 0)
                    self._battle_type_label = "Wild" if battle_info.get("battle_type", 0) == 1 else "Trainer"

                # Read menu options
                options = _get_battle_options(state)
                if not options:
                    for _ in range(10):
                        time.sleep(0.2)
                        state = read_state() or state
                        if state.get("mode") != "battle":
                            self.in_battle = False
                            self._compose_battle_outcome(battle_text)
                            self._prev_mode = "battle"
                            if battle_text:
                                self._pending_text.extend(battle_text)
                            self._pending_text.append("[Battle ended]")
                            return None  # Retry - next state will include the text
                        options = _get_battle_options(state)
                        if options:
                            break

                if not options and _battle_data_valid(state):
                    screen_lines = state.get("screen_lines", [])
                    screen_text = " ".join(screen_lines).upper()
                    if "FIGHT" in screen_text:
                        options = ["FIGHT", "PKMN", "ITEM", "RUN"]
                    else:
                        send_command("INTERACT")
                        time.sleep(0.5)
                        self._prev_mode = "battle"
                        return None  # Retry - more battle text coming

                # Format battle output - include any pending text from previous states
                text_parts = []
                if self._pending_text:
                    text_parts.extend(self._pending_text)
                    text_parts.append("")
                    self._pending_text = []
                if battle_text:
                    text_parts.extend(battle_text)
                    self.accumulated_text.extend(battle_text)
                if _battle_data_valid(state):
                    text_parts.append("")
                    text_parts.append(_format_battle_stats(state))

                if not options:
                    send_command("INTERACT")
                    time.sleep(0.3)
                    self._prev_mode = "battle"
                    if text_parts:
                        self._pending_text.extend(text_parts)
                    return None  # Retry - more battle text coming

                # Wild Pokémon capture hint - show when enemy HP is low
                if hint("battle_tips") and _battle_data_valid(state):
                    battle = _get_battle_info(state)
                    battle_type = battle.get("battle_type", 0)
                    enemy_hp = battle.get("enemy_hp", 999)
                    enemy_max = battle.get("enemy_max_hp", 999)
                    if (battle_type == 1 and enemy_max > 0
                            and enemy_hp <= enemy_max * 0.5 and enemy_hp > 0):
                        hp_pct = int(100 * enemy_hp / enemy_max)
                        # Only urge a catch it can actually make.  Unconditional,
                        # this told it to throw balls it didn't have at a party
                        # with no room, every low-HP wild battle.
                        battle_bag = read_bag(state)
                        party_full = len(state.get("party", []) or []) >= 6
                        out_of_balls = bool(battle_bag) and not has_balls(battle_bag)
                        # Is this species already on the team?  Balls are bought
                        # with money it does not have much of, so a duplicate is
                        # worth beating rather than catching.
                        enemy_nm = decode_gen1_name(battle.get("enemy_name", [])).strip().upper()
                        already_own = any(
                            GEN1_SPECIES.get(m.get("species", 0), "").strip().upper() == enemy_nm
                            for m in (state.get("party", []) or [])
                        ) if enemy_nm else False
                        if self._ball_refused_this_battle:
                            text_parts.append(f"[Note: This wild Pokémon is at {hp_pct}% HP, but the "
                                              f"game already refused a Poké Ball in this battle and "
                                              f"the ball was not used up. Throwing another will be "
                                              f"refused the same way. Beat this Pokémon or RUN.]")
                        elif already_own:
                            text_parts.append(f"[Note: This wild Pokémon is at {hp_pct}% HP so a Poké "
                                              f"Ball would probably work, but you already have a "
                                              f"{enemy_nm.title()} on your team. Poké Balls cost money "
                                              f" - only spend one on a Pokémon you actually want. "
                                              f"Otherwise just beat it for the EXP.]")
                        elif party_full:
                            text_parts.append(f"[Note: This wild Pokémon is at {hp_pct}% HP, but "
                                              f"your party is full (6 Pokémon) so a Poké Ball would "
                                              f"be refused. Beat it or run, then deposit a Pokémon "
                                              f"in the PC at a Pokémon Center to make room.]")
                        elif out_of_balls:
                            text_parts.append(f"[Note: This wild Pokémon is at {hp_pct}% HP and would "
                                              f"be easy to catch, but you have no Poké Balls. Beat it "
                                              f"or run, then buy some at a Poké Mart.]")
                        else:
                            text_parts.append(f"[Tip: This wild Pokémon is at {hp_pct}% HP, "
                                              f"you can try to CATCH it with a Poké Ball! "
                                              f"Select ITEM and use a Poké Ball. Lower HP = better catch rate.]")

                # Nudge out of the ITEM -> Back -> ITEM -> Back spiral.  The
                # menu redraws identically every turn, so without a note in the
                # text there is nothing to tell the agent the loop is its own doing.
                if hint("stuck_notes") and self._battle_cancel_streak >= 2:
                    text_parts.append(
                        f"[Note: you have opened a sub-menu and backed straight "
                        f"out of it {self._battle_cancel_streak} times in a row "
                        f"without doing anything. The battle is NOT stuck or "
                        f"glitched - it is waiting on you. Pick FIGHT and attack; "
                        f"winning or running is the only way out of a battle.]")

                # Build choice map for battle
                choice_map = {}
                text_parts.append("")
                for i, opt in enumerate(options):
                    ch = chr(ord("A") + i)
                    choice_map[ch] = {"action": "battle_select", "index": i, "label": opt, "options": options}
                    text_parts.append(f"{ch}. {opt}")

                self._prev_mode = "battle"
                return "\n".join(text_parts), choice_map, {"mode": "battle", "state": state, "options": options}

            # ── Menu mode ──────────────────────────────────────────
            if mode == "menu":
                menu_items, selected = get_menu_items(state)

                # Auto-handle title screen (NEW GAME / CONTINUE)
                upper_items = [item.strip().upper() for item in menu_items]

                # Bill's PC party list is visually two-line (nickname + level),
                # so the generic menu scraper turns one Pokemon into multiple
                # choices and exposes old junk nicknames such as AAAAAAAAAA.
                # Once DEPOSIT PKMN has been chosen, render the authoritative
                # party from RAM instead. The cursor is parked at the first
                # Pokemon so each choice index maps directly to a party slot.
                pc_main_items = {"WITHDRAW PKMN", "DEPOSIT PKMN", "RELEASE PKMN",
                                 "CHANGE BOX", "SEE YA!"}
                pc_party_list_visible = any(
                    re.search(r"\bLV\s*\d+", item) for item in upper_items
                )
                if (self._pc_deposit_pending and pc_party_list_visible
                        and not (set(upper_items) & pc_main_items)
                        and "DEPOSIT" not in upper_items):
                    party = state.get("party", []) or []
                    if party:
                        if not self._pc_party_cursor_parked:
                            for _ in range(len(party)):
                                send_command("NAV:UP")
                                time.sleep(0.1)
                            self._pc_party_cursor_parked = True
                            time.sleep(0.2)

                        text_parts = ["Choose a Pokemon to deposit safely in BOX No. 1:"]
                        choice_map = {}
                        for i, mon in enumerate(party):
                            ch = chr(ord("A") + i)
                            name = party_mon_name(mon)
                            level = mon.get("level", 0)
                            choice_map[ch] = {
                                "action": "pc_deposit_select",
                                "party_index": i,
                                "label": name,
                                "party_count": len(party),
                            }
                            text_parts.append(f"{ch}. {name} Lv{level}")
                        ch = chr(ord("A") + len(party))
                        choice_map[ch] = {"action": "cancel", "label": "Back/Cancel"}
                        text_parts.append(f"{ch}. Back/Cancel")
                        text_parts.append(
                            "[After you choose a Pokemon, DEPOSIT will be confirmed "
                            "automatically. This stores it safely; it will not be released.]"
                        )
                        self._prev_mode = "menu"
                        return "\n".join(text_parts), choice_map, {"mode": "menu"}

                if "NEW GAME" in upper_items:
                    has_save = self.has_save_files()
                    if has_save and "CONTINUE" in upper_items:
                        target_idx = upper_items.index("CONTINUE")
                    else:
                        target_idx = upper_items.index("NEW GAME")
                    # Navigate to target and select
                    if selected >= 0:
                        delta = target_idx - selected
                        if delta > 0:
                            for _ in range(delta):
                                send_command("NAV:DOWN")
                                time.sleep(0.15)
                        elif delta < 0:
                            for _ in range(-delta):
                                send_command("NAV:UP")
                                time.sleep(0.15)
                    else:
                        for _ in range(target_idx):
                            send_command("NAV:DOWN")
                            time.sleep(0.15)
                    send_command("INTERACT")
                    time.sleep(0.5)
                    return None  # Retry - title screen will transition

                # Auto-handle POKéMON party screen
                # The bridge reports "Choose a POKéMON." and HP values as menu items
                # instead of Pokemon names. Read tile_map directly for reliable status.
                # Also check text_lines - bridge may put "Choose a POKéMON." there instead.
                text_lines_check = state.get("text_lines", [])
                if (any("CHOOSE A" in it.upper() for it in upper_items)
                        or any("CHOOSE A" in t.upper() for t in text_lines_check)):
                    # Read screen tiles directly for Pokémon name, level, HP
                    tile_map = state.get("tile_map", [])
                    screen_lines = decode_screen_text(tile_map) if tile_map else []

                    # Also gather tolk + menu_items as fallback sources
                    tolk = state.get("tolk_output", [])
                    all_text_lines = screen_lines + tolk + menu_items

                    # Count Pokémon (lines containing HP fraction like "27/ 39")
                    hp_lines = [t for t in all_text_lines if "/" in t and any(c.isdigit() for c in t)]

                    # Extract Pokémon name: first line that isn't a header/command/stat
                    skip_patterns = {"CHOOSE", "CANCEL", "TYPE", "MENU"}
                    pkmn_name = ""
                    for t in all_text_lines:
                        clean = t.strip()
                        upper = clean.upper()
                        if not clean or len(clean) < 2:
                            continue
                        # Skip known non-name patterns
                        if any(p in upper for p in skip_patterns):
                            continue
                        if "/" in clean:  # HP fraction
                            continue
                        if upper.startswith(":L") or upper.startswith("LV"):  # Level
                            continue
                        # Must start with a letter (Pokémon names always do)
                        if not clean[0].isalpha():
                            continue
                        # Likely a Pokémon name - grab it
                        # Strip trailing level/HP if on same line (e.g. "BLAZE  :L5")
                        name_part = clean.split(":L")[0].split("Lv")[0].split("LV")[0].strip()
                        if name_part and len(name_part) >= 2:
                            pkmn_name = name_part
                            break

                    # Extract level
                    pkmn_level = ""
                    for t in all_text_lines:
                        clean = t.strip()
                        # Match ":L5", ":L 5", "Lv5", "LV5", "Lv 15" etc.
                        lv_match = re.search(r'(?::L|[Ll][Vv])\s*(\d+)', clean)
                        if lv_match:
                            pkmn_level = f"Lv{lv_match.group(1)}"
                            break

                    # Extract HP fraction
                    pkmn_hp = ""
                    for t in all_text_lines:
                        clean = t.strip()
                        hp_match = re.search(r'(\d+)\s*/\s*(\d+)', clean)
                        if hp_match:
                            pkmn_hp = f"{hp_match.group(1)}/{hp_match.group(2)} HP"
                            break

                    if self._item_use_pending:
                        # This party screen is "use item on which POKéMON?" - # confirm the highlighted one instead of backing out.
                        self._item_use_pending = False
                        send_command("INTERACT")
                        time.sleep(0.6)
                        used_lines = []
                        for _ in range(12):
                            s = read_state()
                            if s:
                                for line in s.get("tolk_output", []):
                                    clean = line.lstrip("→").strip()
                                    if clean and clean not in used_lines:
                                        used_lines.append(clean)
                                if s.get("mode") == "dialogue":
                                    s, lines = auto_advance_dialogue(s)
                                    for line in lines:
                                        if line.strip() and line.strip() not in used_lines:
                                            used_lines.append(line.strip())
                                    break
                                if used_lines:
                                    break
                            time.sleep(0.2)
                        if used_lines:
                            self._pending_text.extend(used_lines)
                        return None  # Retry - land on whatever screen follows

                    if len(hp_lines) <= 1:
                        # Only 1 Pokémon - show status info, then auto-cancel back
                        send_command("CANCEL")
                        time.sleep(0.3)
                        status_parts = []
                        if pkmn_name:
                            status_parts.append(pkmn_name)
                        if pkmn_level:
                            status_parts.append(pkmn_level)
                        if pkmn_hp:
                            status_parts.append(pkmn_hp)
                        status_line = " - ".join(status_parts) if status_parts else "status unknown"
                        self._pending_text.append(f"Your Pokémon: {status_line}. Only one Pokémon, no one to switch with.")
                        return None  # Retry - will return to previous menu

                # Item action sub-menu (USE / TOSS).  The agent already picked the item
                # from the bag, so pressing USE is the obvious follow-up - don't
                # make it choose again, and never silently back out.  If the game
                # refuses ("This isn't the time to use that!"), surface that note.
                if "USE" in upper_items and "TOSS" in upper_items:
                    # TOWN MAP opens a full-screen graphic the bridge can't read
                    # - it reports mode "overworld" with no menu, so every choice
                    # after that quietly does nothing and even SAVE can't find
                    # the START menu.  Never open it; back out and describe the
                    # region instead.
                    if self._selected_item_name == "TOWN MAP":
                        send_command("CANCEL")
                        time.sleep(0.3)
                        self._selected_item_name = ""
                        self._pending_text.extend(_town_map_text(self.current_location))
                        return None  # Retry - back on the bag list

                    use_idx = upper_items.index("USE")
                    if selected >= 0:
                        delta = use_idx - selected
                        for _ in range(abs(delta)):
                            send_command("NAV:DOWN" if delta > 0 else "NAV:UP")
                            time.sleep(0.15)
                    else:
                        for _ in range(use_idx):
                            send_command("NAV:DOWN")
                            time.sleep(0.15)
                    self._item_use_pending = True
                    send_command("INTERACT")

                    # Collect whatever the game says back
                    said = []
                    for _ in range(12):
                        time.sleep(0.2)
                        s = read_state()
                        if not s:
                            continue
                        for line in s.get("tolk_output", []):
                            clean = line.lstrip("→").strip()
                            if clean and clean not in said:
                                said.append(clean)
                        if s.get("mode") == "dialogue":
                            s, lines = auto_advance_dialogue(s)
                            for line in lines:
                                if line.strip() and line.strip() not in said:
                                    said.append(line.strip())
                            break
                        if said:
                            break

                    joined = " ".join(said).upper()
                    refused = any(p in joined for p in (
                        "ISN'T THE TIME", "IS NOT THE TIME", "NOT THE TIME",
                        "WON'T HAVE ANY EFFECT", "NO EFFECT",
                        "CAN'T USE THAT", "CANNOT USE",
                        "OAK:",
                    ))
                    if refused:
                        self._item_use_pending = False
                        self._pending_text.append(
                            f"You can't use that here - {' '.join(said)}"
                            if said else "You can't use that item right now."
                        )
                    elif said:
                        self._pending_text.extend(said)
                    return None  # Retry - show whatever screen we landed on

                # Auto-handle empty menus (e.g. ITEM with no items)
                # If the only option is CANCEL, there's nothing to do - auto-cancel
                non_cancel = [it for it in upper_items if it not in ("CANCEL",)]
                if not non_cancel and "CANCEL" in upper_items:
                    send_command("CANCEL")
                    time.sleep(0.3)
                    self._pending_text.append("No items in your bag!")
                    return None  # Retry - will return to previous menu

                # Mart quantity spinner.  Gen 1 leaves this box drawn while
                # "That will be ¥N. OK?" prints underneath it, so the generic
                # menu path handed the agent the same "×01" screen two turns running
                # and again after "Here you are!" - four choices to buy one
                # Potion, two of which did nothing.  Drive the confirmation
                # here so one pick completes the purchase.
                if _is_qty_box(menu_items):
                    qty_entry = next(
                        (it.strip() for it in menu_items
                         if _QUANTITY_RE.match(it.split("¥")[0].strip())),
                        "×01",
                    )
                    item_name = self._selected_item_name or "it"
                    text_parts = []
                    if self._pending_text:
                        text_parts.extend(self._pending_text)
                        text_parts.append("")
                        self._pending_text = []
                    player_money = state.get("player_money")
                    if player_money is not None:
                        text_parts.append(f"[Your balance: ${player_money}]")
                    text_parts.append(f"The clerk is ringing up {item_name} - {qty_entry}")
                    choice_map = {
                        "A": {"action": "buy_confirm",
                              "label": f"{item_name} {qty_entry}"},
                        "B": {"action": "cancel", "label": "Don't buy it"},
                    }
                    text_parts.append(f"A. Buy it ({qty_entry}) - this finishes the "
                                      "purchase, no further confirmation")
                    text_parts.append("B. Don't buy it - back to the shelf")
                    self._prev_mode = "menu"
                    return "\n".join(text_parts), choice_map, {"mode": "menu"}

                # Detect item-list menus.  Gen 1 only draws ~4 entries at a time,
                # so reading the visible tile_map alone hides the rest of the bag.
                # Scroll the whole list, then park the cursor back at the top so
                # menu_select's index math starts from a known position.
                if any(_is_quantity_line(item) for item in menu_items):
                    scanned_items, _ = _scan_item_list(state)
                    if len(scanned_items) > 1:
                        for _ in range(len(scanned_items)):
                            send_command("NAV:UP")
                            time.sleep(0.1)
                        time.sleep(0.2)
                        menu_items = scanned_items
                        selected = 0
                        # True = indices refer to the FULL list and the cursor was
                        # deliberately parked at index 0; don't re-derive it from
                        # the visible window, which only sees ~4 rows.
                        self._item_list_cache = (menu_items, selected, True)
                    else:
                        # Scroll scan failed - fall back to the on-screen reader
                        visible_items, cursor_idx = _read_visible_item_list(state)
                        if visible_items:
                            menu_items = visible_items
                            selected = cursor_idx
                            self._item_list_cache = (menu_items, selected, False)

                # Check if this is a name preset menu (NEW NAME / BLUE / GARY / JOHN)
                is_name_menu = "NEW NAME" in upper_items

                # Detect player name from START menu context:
                # The START menu contains known items - any unknown short
                # alphabetic item is the player name (works for mixed-case names like "Ash")
                _KNOWN_MENU_ITEMS = {"EXIT", "CANCEL", "ITEM", "POKÉMON", "POKÉDEX",
                                     "POKEDEX", "POKéMON", "POKéDEX",
                                     "SAVE", "OPTION", "YES", "NO",
                                     "USE", "TOSS",
                                     "NEW NAME", "BLUE", "GARY", "JOHN"}
                if not self.player_name and not is_name_menu:
                    for item in menu_items:
                        stripped = item.strip()
                        if (1 <= len(stripped) <= 7 and stripped.isalpha()
                                and stripped.upper() not in _KNOWN_MENU_ITEMS):
                            self.player_name = stripped
                            break

                # Filter: remove player name entry and OPTION/SAVE
                # Skip filtering for name preset menus (BLUE/GARY/JOHN are valid choices)
                filtered_items = []
                for item in menu_items:
                    upper = item.strip().upper()
                    # Skip OPTION and SAVE from in-game menu (save handled by overworld Save)
                    # Skip "Choose a POKéMON." header text (not a selectable item)
                    if upper in ("OPTION", "SAVE") or "CHOOSE A" in upper:
                        continue
                    # Skip player name if it appears as a menu item (START menu shows it)
                    if self.player_name and item.strip().upper() == self.player_name.upper():
                        continue
                    # Skip single short all-caps words that look like player names
                    # (Gen 1 player names are 1-7 uppercase letters)
                    # But NOT when this is a name preset menu
                    if (not is_name_menu
                            and len(item.strip()) <= 7 and item.strip().isalpha()
                            and item.strip().isupper()
                            and upper not in _KNOWN_MENU_ITEMS):
                        continue
                    filtered_items.append(item)

                text_parts = []
                if self._pending_text:
                    text_parts.extend(self._pending_text)
                    text_parts.append("")
                    self._pending_text = []
                text_lines_ctx = state.get("text_lines", [])
                if text_lines_ctx:
                    for line in text_lines_ctx:
                        text_parts.append(line)

                # Show player money balance in menus
                player_money = state.get("player_money")
                if player_money is not None:
                    text_parts.append(f"[Your balance: ${player_money}]")

                choice_map = {}
                letter_idx = 0
                for i, item in enumerate(filtered_items):
                    ch = chr(ord("A") + letter_idx)
                    # Find original index in menu_items for navigation
                    orig_idx = menu_items.index(item)
                    # Rename EXIT to avoid confusion with exiting the game
                    display_label = "Close" if item.strip().upper() == "EXIT" else item
                    # Gen 1's PC labels are misleading: the player's own PC is
                    # for items, while SOMEONE'S/BILL'S PC stores Pokemon.
                    if upper in ("SOMEONE'S PC", "BILL'S PC"):
                        display_label = f"{item} - Pokemon storage (use this)"
                    elif (upper.endswith("'S PC")
                          and "PROF.OAK" not in upper
                          and upper not in ("SOMEONE'S PC", "BILL'S PC")):
                        display_label = f"{item} - item storage only, not Pokemon"
                    elif upper == "PROF.OAK'S PC":
                        display_label = f"{item} - Pokedex evaluation, not storage"
                    elif upper == "DEPOSIT PKMN":
                        display_label = f"{item} - safely store a party Pokemon"
                    elif upper == "RELEASE PKMN":
                        display_label = f"{item} - PERMANENTLY LOSE a Pokemon; DO NOT USE"
                    # Add item usage hints (e.g. "PARLYZ HEAL x1 - cures paralysis ONLY")
                    # Strip a merged mart price ("POTION  ¥300") the same way
                    item_base = item.split("¥")[0].split("x")[0].strip().split("×")[0].strip().upper()
                    item_hint = _ITEM_HINTS.get(item_base, "") if hint("item_descriptions") else ""
                    if item_hint:
                        display_label = f"{display_label} - {item_hint}"
                    choice_map[ch] = {
                        "action": "menu_select",
                        "index": orig_idx,
                        "selected": selected,
                        "total": len(menu_items),
                        "label": item,
                        "_menu_items": state.get("menu_items", []),
                    }
                    text_parts.append(f"{ch}. {display_label}")
                    letter_idx += 1

                # Only add Back/Cancel if EXIT or CANCEL isn't already an option
                filtered_upper = {it.strip().upper() for it in filtered_items}
                if not ("EXIT" in filtered_upper or "CANCEL" in filtered_upper):
                    ch = chr(ord("A") + letter_idx)
                    choice_map[ch] = {"action": "cancel", "label": "Back/Cancel"}
                    text_parts.append(f"{ch}. Back/Cancel")

                # The PC uses "SOMEONE'S PC" until the player meets Bill. Make
                # that identity explicit so it is not mistaken for an unrelated
                # computer, and keep the destructive RELEASE command from ever
                # being treated as a fallback for a confusing deposit attempt.
                if "SOMEONE'S PC" in filtered_upper or "BILL'S PC" in filtered_upper:
                    text_parts.append(
                        "[PC guidance: SOMEONE'S PC is Bill's Pokemon Storage "
                        "System before you meet Bill; choose it to store Pokemon. "
                        "Your own PC stores items, and PROF.OAK's PC does not store "
                        "your party Pokemon.]"
                    )
                if "DEPOSIT PKMN" in filtered_upper and "RELEASE PKMN" in filtered_upper:
                    text_parts.append(
                        "[STORAGE SAFETY: To make party room, choose DEPOSIT PKMN. "
                        "Never choose RELEASE PKMN - it permanently gives up a "
                        "Pokemon. If deposit seems to fail, retry DEPOSIT or cancel; "
                        "RELEASE is not a fallback.]"
                    )

                # Starter Pokemon hint - when choosing between a starter and CANCEL
                _STARTERS = {"CHARMANDER", "SQUIRTLE", "BULBASAUR"}
                chosen_starter = filtered_upper & _STARTERS
                if chosen_starter and hint("story_notes"):
                    name = chosen_starter.pop()
                    others = _STARTERS - {name}
                    text_parts.append(f"[Note: There are 3 starter Pokémon to choose from: {', '.join(sorted(_STARTERS))}. Pick wisely!]")

                self._prev_mode = "menu"
                return "\n".join(text_parts), choice_map, {"mode": "menu"}

            # ── Keyboard mode ──────────────────────────────────────
            if mode == "keyboard":
                text_parts = []
                tolk = state.get("tolk_output", [])
                if tolk:
                    add_to_history(tolk)
                    for line in tolk:
                        clean = line.strip()
                        if clean:
                            text_parts.append(clean)
                text_lines = state.get("text_lines", [])
                if text_lines:
                    for line in text_lines:
                        text_parts.append(line)

                self._prev_mode = "keyboard"
                return "\n".join(text_parts), {}, {"mode": "keyboard", "needs_name": True}

            # ── Overworld mode (main) ──────────────────────────────
            if mode == "overworld":
                self._item_use_pending = False  # never carry across screens
                self._selected_item_name = ""
                current_map_id = state.get("map_id")
                if current_map_id != self.interacted_map_id:
                    self.interacted_objects.clear()
                    self._blocked_objects.clear()
                    self.interacted_map_id = current_map_id
                self.current_map_id = current_map_id

                map_name = state.get("map_name", "???")
                self._update_location(map_name)

                # Get objects
                map_db_data = self._get_map_db_data()
                if self._map_available:
                    objects = get_map_objects(state, map_db_data)
                else:
                    objects = prepare_objects(state.get("objects", []))

                if not objects:
                    for _retry in range(6):
                        time.sleep(0.5)
                        state = read_state()
                        if not state:
                            continue
                        if state.get("mode") != "overworld":
                            break
                        if self._map_available:
                            objects = get_map_objects(state, map_db_data)
                        else:
                            objects = prepare_objects(state.get("objects", []))
                        if objects:
                            break

                if not objects and state and state.get("mode") == "overworld":
                    auto_explore()
                    state = read_state() or state
                    if self._map_available:
                        objects = get_map_objects(state, map_db_data)
                    else:
                        objects = prepare_objects(state.get("objects", []))

                # Format output
                # Prepend any pending dialogue/battle text from previous states
                text_parts = []
                if self._pending_text:
                    text_parts.extend(self._pending_text)
                    text_parts.append("")
                    self._pending_text = []

                # Location header -- use friendly display name from _update_location
                clean_name = self.current_location or map_name.replace("[", "").replace("]", "").strip()
                text_parts.append(f"Current Location - {clean_name}")

                # Dynamic direction hints from connection objects
                dir_hints = []
                for obj in objects:
                    if obj.get("type") == "connection":
                        c_dir = obj.get("connection_dir", "")
                        c_name = obj.get("name", "")
                        if c_dir and c_name:
                            # Strip "North connection, " prefix - just show destination
                            parts = c_name.split(", ", 1)
                            dest = parts[1] if len(parts) > 1 else c_name
                            dir_hints.append(f"{c_dir.capitalize()} \u2192 {dest}")
                if dir_hints and hint("navigation"):
                    text_parts.append(f"[Directions: {', '.join(dir_hints)}]")

                # Party summary line
                party_data = state.get("party", [])
                if party_data:
                    party_strs = []
                    for mon in party_data:
                        name = party_mon_name(mon)
                        lv = mon.get("level", 0)
                        hp = mon.get("hp", 0)
                        max_hp = mon.get("max_hp", 0)
                        hp_desc = _hp_descriptor(hp, max_hp)
                        party_strs.append(f"{name} Lv{lv} ({hp}/{max_hp} HP{hp_desc})")
                    text_parts.append(f"[Party: {', '.join(party_strs)}]")

                # Bag line - the in-game menu is no longer offered, so the bag
                # has to be visible here or the agent can't know what it's carrying.
                bag = read_bag(state)
                bag_line = format_bag_line(bag)
                if bag_line:
                    text_parts.append(bag_line)

                # Why it can't catch anything, said out here in the overworld
                # where it can still do something about it.  The same two notes
                # exist inside the battle ITEM sub-menu, but it mostly never
                # opens it, so it'd hunt for hours without ever being told.
                # An empty bag means the bridge didn't report one, so the
                # no-balls note stays quiet rather than guessing.
                if (hint("battle_tips") and len(party_data) >= 6
                        and "POKECENTER" in clean_name.upper()):
                    text_parts.append(
                        "[Your party is full (6 Pokemon). You cannot catch anything "
                        "new until you make room. Go to a Pokemon Center and use the "
                        "PC in the corner: choose SOMEONE'S PC (this is BILL'S PC "
                        "before you meet Bill), then DEPOSIT PKMN and put one of "
                        "yours in storage. It stays yours and you can take it back "
                        "out any time.]")
                if hint("battle_tips") and bag and not has_balls(bag):
                    text_parts.append("[Pokeballs x0, you cant catch any new pokemon]")

                # Tall grass detection: check player's tile against known grass tile IDs
                tileset_id = state.get("tileset_id", -1)
                tile_map = state.get("tile_map", [])
                if tileset_id in GRASS_TILES and len(tile_map) >= 189:
                    player_tile = tile_map[9 * 20 + 8]  # bottom-left of center 2x2 block (index 188)
                    if player_tile in GRASS_TILES[tileset_id]:
                        text_parts.append("[You are in tall grass \u2014 wild Pokemon may appear!]")

                # Show tolk output
                tolk = state.get("tolk_output", [])
                if tolk:
                    add_to_history(tolk)
                    for line in tolk:
                        clean = line.strip()
                        if clean:
                            text_parts.append(f">> {clean}")

                choice_map = {}
                letter_idx = 0

                # Object list (connections excluded - shown as directions instead)
                non_conn_objects = [
                    o for o in objects
                    if o.get("type") != "connection"
                ]
                if non_conn_objects:
                    text_parts.append("")
                    text_parts.append("Nearby:")
                    for i, obj in enumerate(non_conn_objects):
                        name = obj.get("name", "???")
                        obj_type = obj.get("type", "")
                        type_label = ""
                        if obj_type == "warp":
                            if name not in ("Stairs", "Outside"):
                                type_label = " (door)"

                        # Mark already-interacted objects
                        obj_x, obj_y = obj.get("x", -1), obj.get("y", -1)
                        mark = ""
                        if not hint("navigation"):
                            pass
                        elif (obj_x, obj_y) in self._blocked_objects:
                            mark = " (blocked - trees or walls in the way)"
                        elif (obj_x, obj_y) in self.interacted_objects:
                            mark = " (already interacted)"
                        elif (obj_type == "warp" and self._just_left_location
                              and name.upper().replace("'", "").replace(" ", "")
                              == self._just_left_location.upper().replace("'", "").replace(" ", "")):
                            mark = " (you just exited here, explore elsewhere)"

                        dist = obj.get("dist", "")
                        dist_str = f" - {dist}" if dist else ""

                        ch = chr(ord("A") + letter_idx)
                        choice_map[ch] = {
                            "action": "walk_to_object",
                            "object": obj,
                            "label": f"{name}{type_label}{mark}",
                            "_state": state,
                        }
                        text_parts.append(f"{ch}. {name}{type_label}{mark}{dist_str}")
                        letter_idx += 1
                else:
                    text_parts.append("")
                    text_parts.append("(No objects nearby)")

                # Directional walking (outdoor maps only).  Indoors there is
                # nothing to explore by compass - everything worth doing is in
                # the Nearby list, and offering directions there just gave the agent
                # a way to walk into walls.  Viridian Forest is tagged outdoor
                # in _init_map_db() so it keeps its directions.
                is_outdoor = False
                if self._map_available:
                    md = _get_map_data(state, map_db_data)
                    if md and md.get("_outdoor", False):
                        is_outdoor = True

                if is_outdoor:
                    text_parts.append("")
                    for direction, label in [("up", "North"), ("down", "South"),
                                             ("right", "East"), ("left", "West")]:
                        ch = chr(ord("A") + letter_idx)
                        choice_map[ch] = {
                            "action": "walk_direction",
                            "direction": direction,
                            "label": label,
                            "_state": state,
                        }
                        text_parts.append(f"{ch}. {label}")
                        letter_idx += 1

                # The in-game START menu is no longer offered as a choice.  the agent
                # got stuck in it - open it to heal, find no Potion, close it,
                # repeat - and everything it held is either on screen already
                # (party HP, bag) or reachable through the named actions below.
                # open_start_menu() and the menu handler stay: saving and these
                # actions still drive them internally.
                extra_actions = []

                # Healing: only when someone is hurt AND a healing item exists.
                hurt = [
                    party_mon_name(m)
                    for m in (state.get("party") or [])
                    if 0 < m.get("hp", 0) < m.get("max_hp", 0)
                ]
                heal_item = next(
                    (name for name, _ in bag
                     if name.upper() in _HEALING_ITEMS), ""
                )
                if hurt and heal_item:
                    extra_actions.append({
                        "action": "heal_pokemon",
                        "item": heal_item,
                        "label": f"Use a {heal_item} on {hurt[0]}",
                    })

                # Field moves (CUT, SURF, ...) - only once a party member
                # actually knows one.  This is why the menu machinery is kept.
                for idx, mon_name, move in party_field_moves(state):
                    extra_actions.append({
                        "action": "field_move",
                        "party_index": idx,
                        "move": move,
                        "label": f"Use {move} ({mon_name})",
                    })

                # The TOWN MAP opens a full-screen graphic the bridge can't
                # read, so it is answered with text instead of ever being
                # opened - same interception as the bag route, one step shorter.
                if any(name.upper() == "TOWN MAP" for name, _ in bag):
                    extra_actions.append({
                        "action": "town_map",
                        "label": "Check the Town Map",
                    })

                if extra_actions:
                    text_parts.append("")
                    for act in extra_actions:
                        ch = chr(ord("A") + letter_idx)
                        choice_map[ch] = act
                        text_parts.append(f"{ch}. {act['label']}")
                        letter_idx += 1

                # Store state for send_choice
                state["_display_objects"] = objects

                # Tick down "just left" counter
                if self._just_left_remaining > 0:
                    self._just_left_remaining -= 1
                    if self._just_left_remaining <= 0:
                        self._just_left_location = ""

                # Tick down the fallback demo-battle hint
                if self._tutorial_hint_turns > 0:
                    self._tutorial_hint_turns -= 1

                # Stuck detection: if the state text matches previous turn, nudge a different action
                # Only blame a directional move if the most recent action was actually a walk
                final_text = "\n".join(text_parts)
                if (hint("stuck_notes") and self._prev_state_text
                        and final_text == self._prev_state_text):
                    last_action = self._recent_actions[-1] if self._recent_actions else ""
                    if "Walking" in last_action and "..." in last_action:
                        last_dir = last_action.split("Walking ")[-1].split("...")[0]
                        final_text += f"\n\n[Note: Your last move ({last_dir}) didn't change your position - you may be at the edge of this area or blocked by a wall. Try a different direction or interact with something from the Nearby list instead.]"
                    elif "Walking" not in last_action:
                        pass  # non-movement action (menu, etc.) - state not changing is expected
                    else:
                        final_text += "\n\n[Note: Nothing has changed since your last action. Try a different choice - pick something from the Nearby list or go a different direction.]"
                self._prev_state_text = "\n".join(text_parts)  # store without the stuck note

                self._prev_mode = "overworld"
                return final_text, choice_map, {
                    "mode": "overworld",
                    "state": state,
                    "map_name": map_name,
                    "objects": objects,
                    "is_outdoor": is_outdoor,
                }

            # ── Error mode ─────────────────────────────────────────
            if mode == "error":
                self._prev_mode = mode
                return f"Bridge error: {state.get('error_msg', 'unknown')}", {}, {"mode": "error"}

            # ── Unknown mode ───────────────────────────────────────
            text_parts = []
            tolk = state.get("tolk_output", [])
            if tolk:
                add_to_history(tolk)
                for line in tolk:
                    text_parts.append(line.strip())
            text_lines = state.get("text_lines", [])
            if text_lines:
                for line in text_lines:
                    text_parts.append(line)

            choice_map = {
                "A": {"action": "press_a", "label": "Press A"},
                "B": {"action": "press_b", "label": "Press B"},
            }
            text_parts.append("")
            text_parts.append("A. Press A")
            text_parts.append("B. Press B")

            self._prev_mode = mode
            return "\n".join(text_parts), choice_map, {"mode": "unknown"}

        return await asyncio.to_thread(_get_state_sync)

    async def send_choice(self, letter, choice_map):
        """Execute the chosen action from the choice_map.

        Returns a brief status string (e.g. "Walking to Oak's Lab...")
        """
        letter = letter.upper()
        if letter not in choice_map:
            return f"Invalid choice: {letter}"

        choice = choice_map[letter]
        action = choice.get("action", "")

        def _execute_sync():
            if action == "walk_to_object":
                obj = choice["object"]
                target_name = obj.get("name", "???")
                obj_x, obj_y = obj.get("x", -1), obj.get("y", -1)

                walk_state = choice.get("_state", read_state() or {})

                # NOTE: there is deliberately NO BFS "is it reachable?" pre-check
                # here.  Predicting blockage produced false positives on counters
                # (Mart clerks, Pokecenter nurses) - Gen 1 lets you talk ACROSS a
                # counter tile even though no adjacent cell is walkable, so a
                # perfectly reachable NPC looked unreachable and the walk was
                # refused.  Instead: always attempt the walk, retry once, and only
                # call something blocked after it has actually failed twice.
                def _attempt_walk(ws):
                    if obj.get("_map_obj"):
                        return map_walk_to(obj, ws, self.pokeball_names)
                    return walk_to_object(obj, ws), None

                def _interaction_happened(before, after, lines):
                    """Did the walk actually accomplish anything?"""
                    if after.get("mode") not in ("overworld", None, ""):
                        return True   # dialogue / battle / menu opened
                    if after.get("tolk_output"):
                        return True   # screen reader picked up text
                    if lines:
                        return True   # decoded screen text (incl. "walked away")
                    bx, by = before.get("player_x", -1), before.get("player_y", -1)
                    ax, ay = after.get("player_x", -2), after.get("player_y", -2)
                    if bx >= 0 and by >= 0 and (ax, ay) != (bx, by):
                        return True   # player physically moved
                    return False

                result_state, screen_lines = _attempt_walk(walk_state)
                after_state = read_state() or result_state or {}

                # Warps run their own retry loop further down; everything else
                # gets exactly one automatic retry before being called blocked.
                if (obj.get("type") not in ("warp", "connection")
                        and not _interaction_happened(walk_state, after_state, screen_lines)):
                    time.sleep(0.4)
                    retry_state = read_state() or after_state
                    result_state, screen_lines = _attempt_walk(retry_state)
                    after_state = read_state() or result_state or {}
                    if not _interaction_happened(retry_state, after_state, screen_lines):
                        if obj_x >= 0 and obj_y >= 0:
                            self._blocked_objects.add((obj_x, obj_y))
                        if not hint("navigation"):
                            return (f"Couldn't reach {target_name} - you walked at it twice "
                                    f"and something is in the way (trees or a wall).")
                        return (f"Couldn't reach {target_name} - you walked at it twice "
                                f"and something is in the way (trees or a wall). "
                                f"It's marked blocked now; try approaching from a "
                                f"different direction.")

                # Mark non-warp, non-connection objects as interacted
                if obj.get("type") not in ("warp", "connection"):
                    state = after_state
                    self._mark_interacted(obj, state.get("map_id") if state else None)
                    self.last_interacted_name = target_name
                else:
                    self.last_interacted_name = None
                    self.last_warp_used = target_name

                # For warps: verify map change, retry if needed
                warp_succeeded = False
                if obj.get("type") == "warp":
                    old_map_id = choice.get("_state", {}).get("map_id", -1)
                    old_location = self.current_location  # save before warp
                    # The map_walk_to already handled the warp steps,
                    # but we may need to retry
                    time.sleep(0.5)
                    for attempt in range(8):
                        check = read_state()
                        if not check:
                            break
                        check_mode = check.get("mode", "overworld")
                        if check_mode in ("dialogue", "battle", "menu"):
                            warp_succeeded = True
                            break
                        new_id = check.get("map_id", -1)
                        if new_id != old_map_id:
                            # Warp succeeded - record where we just came from
                            warp_succeeded = True
                            self._just_left_location = old_location
                            self._just_left_remaining = 4
                            break
                        rpx = check.get("player_x", -1)
                        rpy = check.get("player_y", -1)
                        tx, ty = obj.get("x", rpx), obj.get("y", rpy)
                        rdx, rdy = tx - rpx, ty - rpy
                        if rdx == 0 and rdy == 0:
                            send_command("PRESS:down")
                            time.sleep(0.5)
                        elif abs(rdy) >= abs(rdx):
                            send_command(f"PRESS:{'down' if rdy > 0 else 'up'}")
                            time.sleep(0.5)
                        else:
                            send_command(f"PRESS:{'right' if rdx > 0 else 'left'}")
                            time.sleep(0.5)
                        time.sleep(0.3)

                    if not warp_succeeded:
                        # Only reported after 8 real attempts to step through the
                        # warp - this is an observed failure, not a prediction.
                        return (f"Couldn't get through to {target_name} - something is in the way. "
                                f"Try walking around it first, or pick something else.")

                result = f"Walking to {target_name}..."
                if screen_lines:
                    result += "\n" + "\n".join(screen_lines)
                return result

            elif action == "walk_direction":
                direction = choice["direction"]
                state = choice.get("_state", read_state() or {})
                if not state:
                    return f"Cannot walk {direction} - no state"

                passability = state.get("_passability")
                pos = state.get("_player_pos")
                DELTAS = {"up": (0, -1), "down": (0, 1), "right": (1, 0), "left": (-1, 0)}
                ddx, ddy = DELTAS.get(direction, (0, 0))

                if passability and pos and HAS_BFS:
                    px, py = pos
                    rows = len(passability)
                    cols = len(passability[0]) if rows > 0 else 0

                    # Block door/warp cells
                    objects = state.get("_display_objects", [])
                    blocked_warps = []
                    for o in objects:
                        if o.get("_tile_type") == "warp":
                            wx, wy = o.get("x", -1), o.get("y", -1)
                            if 0 <= wy < rows and 0 <= wx < cols and passability[wy][wx]:
                                passability[wy][wx] = False
                                blocked_warps.append((wx, wy))

                    # Find a target tile far in the desired direction.
                    # Use large distance (up to 15) so BFS routes THROUGH
                    # wall gaps and continues past them, not just to the gap.
                    # Allow lateral offset up to 8 tiles to reach gaps in
                    # long walls (Route 1 has 10-tile ledges with gaps at
                    # the far side). Prefer paths that end closest to the
                    # player's current perpendicular position (smallest offset).
                    path = None
                    for dist in range(15, 0, -1):
                        best_path = None
                        best_offset = 999
                        ty = py + ddy * dist
                        tx = px + ddx * dist
                        for offset in range(9):
                            for sign in ([0] if offset == 0 else [1, -1]):
                                off = offset * sign
                                if ddy != 0:
                                    gx, gy = px + off, ty
                                else:
                                    gx, gy = tx, py + off
                                if not (0 <= gy < rows and 0 <= gx < cols):
                                    continue
                                if not passability[gy][gx]:
                                    continue
                                p = bfs_pathfind(passability, (px, py), (gx, gy))
                                if p is not None and (best_path is None or len(p) < len(best_path)):
                                    best_path = p
                                    best_offset = abs(offset)
                                    if best_offset == 0:
                                        break
                            if best_path and best_offset == 0:
                                break
                        if best_path:
                            path = best_path
                            break

                    for wx, wy in blocked_warps:
                        passability[wy][wx] = True

                    if path:
                        steps = len(path)
                        _send_moves(path, self.vba_hwnd)
                    elif ((direction == "up" and py <= 1) or
                          (direction == "down" and py >= rows - 2) or
                          (direction == "left" and px <= 1) or
                          (direction == "right" and px >= cols - 2)):
                        steps = 3
                        _send_moves([direction] * 3, self.vba_hwnd)
                    else:
                        alt = "East or West" if direction in ("up", "down") else "North or South"
                        if not hint("navigation"):
                            return f"Can't go {choice['label']} - blocked!"
                        return f"Can't go {choice['label']} - blocked! Try {alt}."
                else:
                    steps = 5
                    _send_moves([direction] * 5, self.vba_hwnd)

                # Check for cutscene/dialogue
                time.sleep(0.5)
                post = read_state()
                if post and post.get("mode") == "dialogue":
                    return f"Walking {choice['label']}... moved {steps} tile{'s' if steps != 1 else ''} (something happened)"
                return f"Walking {choice['label']}... moved {steps} tile{'s' if steps != 1 else ''}"

            elif action == "heal_pokemon":
                # Drives START -> ITEM -> the item, then stops.  The USE/TOSS
                # interception and _item_use_pending party confirm in
                # get_game_state() finish the job - same path a bag selection
                # always took, just without the agent having to walk the menu.
                item_name = choice.get("item", "").upper()
                open_start_menu()
                if not menu_pick(("ITEM",)):
                    send_command("CANCEL")
                    return "Couldn't open your bag."

                time.sleep(0.4)
                s = read_state() or {}
                scanned, _ = _scan_item_list(s)
                if not scanned:
                    send_command("CANCEL")
                    return "Couldn't read your bag."
                # _scan_item_list scrolled to the bottom - park back at the top
                # so the index below is measured from a known position.
                for _ in range(len(scanned)):
                    send_command("NAV:UP")
                    time.sleep(0.1)
                time.sleep(0.2)

                upper = [it.split("x")[0].strip().split("×")[0].strip().upper()
                         for it in scanned]
                if item_name not in upper:
                    send_command("CANCEL")
                    return f"No {item_name} in your bag."
                menu_nav_to(upper.index(item_name), 0)
                self._selected_item_name = item_name
                send_command("INTERACT")
                time.sleep(0.4)
                return f"Using {item_name}..."

            elif action == "field_move":
                # START -> POKéMON -> the Pokémon -> the move.  Runs to
                # completion here rather than handing back to get_game_state,
                # whose party-screen handler auto-cancels a one-Pokémon party.
                move = choice.get("move", "").upper()
                party_index = choice.get("party_index", 0)
                open_start_menu()
                if not menu_pick(("POKéMON", "POKEMON")):
                    send_command("CANCEL")
                    return "Couldn't open your party."

                time.sleep(0.5)
                s = read_state()
                _, sel = get_menu_items(s) if s else ([], -1)
                menu_nav_to(party_index, sel)
                send_command("INTERACT")
                time.sleep(0.5)

                if not menu_pick((move,)):
                    for _ in range(4):
                        send_command("CANCEL")
                        time.sleep(0.3)
                    return f"{move} can't be used here."

                # Collect whatever the game says back, then make sure we end
                # up in the overworld rather than parked in a menu.
                said = []
                for _ in range(15):
                    time.sleep(0.2)
                    st = read_state()
                    if not st:
                        continue
                    for line in st.get("tolk_output", []):
                        clean = line.lstrip("→").strip()
                        if clean and clean not in said:
                            said.append(clean)
                    if st.get("mode") == "dialogue":
                        st, lines = auto_advance_dialogue(st)
                        for line in lines:
                            if line.strip() and line.strip() not in said:
                                said.append(line.strip())
                        break
                    if st.get("mode") == "overworld":
                        break
                for _ in range(3):
                    st = read_state()
                    if st and st.get("mode") == "overworld":
                        break
                    send_command("CANCEL")
                    time.sleep(0.3)
                return "\n".join(said) if said else f"Used {move}."

            elif action == "town_map":
                # Never actually opened - the real TOWN MAP is a full-screen
                # graphic the bridge reads as an empty overworld, which used to
                # softlock the session.  Answer with the same text the USE/TOSS
                # interception uses.
                return "\n".join(_town_map_text(self.current_location))

            elif action == "battle_select":
                target_idx = choice["index"]
                options = choice.get("options", [])
                state = choice.get("_state") or read_state()

                _battle_select(state, target_idx, options=options)

                # BATTLE: queues 3-5 button presses (~0.5s at 60fps).
                # Wait for them to drain and the game to process.
                time.sleep(0.6)

                # Poll until the menu ACTUALLY changes.  Never accept
                # FIGHT/PKMN/ITEM/RUN as a sub-menu - that's the main
                # battle menu being read back before the transition.
                _MAIN_MENU = {"FIGHT", "PKMN", "ITEM", "RUN"}
                sub_options = []
                for _ in range(20):  # 20 × 0.15s = 3s max
                    time.sleep(0.15)
                    state = read_state() or state
                    if state.get("mode") != "battle":
                        self.in_battle = False
                        return "Battle action executed."
                    opts = _get_battle_options(state)
                    if opts:
                        opts_set = set(o.strip().upper() for o in opts)
                        if opts_set != _MAIN_MENU:
                            # Real sub-menu (move names, etc.)
                            sub_options = opts
                            break
                        # Still showing main menu - keep waiting
                    elif target_idx != 0:
                        # Menu disappeared - sub-screen for PKMN/ITEM/RUN
                        break
                    # For FIGHT: keep waiting even if menu is
                    # temporarily empty during transition

                if not sub_options and target_idx != 0:
                    # PKMN/ITEM/RUN sub-screen
                    sub_text = []
                    sub_seen = set()
                    for _ in range(20):
                        s = read_state()
                        if not s or s.get("mode") != "battle":
                            return "Action completed."
                        for line in s.get("tolk_output", []):
                            clean = line.strip()
                            if clean and clean not in sub_seen:
                                sub_seen.add(clean)
                                sub_text.append(clean)
                        tolk_joined = " ".join(s.get("tolk_output", [])).upper()
                        if ("ALREADY OUT" in tolk_joined or "NO WILL TO FIGHT" in tolk_joined
                                or "CHOOSE A" in tolk_joined):
                            break
                        time.sleep(0.2)

                    if target_idx == 1:  # PKMN
                        # Party size comes from RAM, not from counting "LV"
                        # lines in the screen reader text - that text arrives a
                        # line at a time and the loop above breaks as soon as
                        # the prompt appears, so a full party read as one.
                        pkmn_count = len((read_state() or state).get("party", []) or [])
                        if pkmn_count <= 1:
                            _cancel_to_battle_menu(state)
                            return "You can't swap - you only have one Pokémon!"
                        # Leave the party screen open: the next state read
                        # presents it as a choice list, same as a forced switch.
                        return "\n".join(sub_text) if sub_text else "Choose a Pokémon."
                    if target_idx == 2:  # ITEM
                        # Scroll through the FULL item list (Gen 1 only
                        # shows ~3-4 items at a time on screen).
                        time.sleep(0.3)  # let item menu fully render
                        scan_state = read_state() or state
                        scanned_items, _ = _scan_item_list(scan_state)
                        # Filter out CANCEL entry
                        item_options = [
                            it for it in scanned_items
                            if it.upper() != "CANCEL"
                        ]
                        if not item_options:
                            _cancel_to_battle_menu(read_state() or state)
                            return "No items in your bag!"
                        # Navigate cursor back to the top of the list
                        # (_scan_item_list scrolled down through everything)
                        for _ in range(len(scanned_items)):
                            send_command("NAV:UP")
                            time.sleep(0.1)
                        if item_options:
                            self._pending_sub_menu = item_options
                            has_balls = any(
                                it.split("x")[0].strip().split("×")[0].strip().upper() in _BALL_NAMES
                                for it in item_options
                            )
                            result = "SUB_MENU:" + json.dumps(item_options)
                            if not hint("battle_tips"):
                                pass
                            elif not has_balls:
                                result += "\n[You have no Poké Balls! Visit a mart to buy some after this battle.]"
                            elif len((scan_state.get("party", []) or [])) >= 6:
                                result += ("\n[Your party is full (6 Pokémon) - a Poké Ball may be refused. "
                                           "Deposit a Pokémon in the PC at a Pokémon Center to make room.]")
                            return result
                    if target_idx == 3:  # RUN
                        # RUN was already pressed - collect result text
                        self._battle_cancel_streak = 0
                        time.sleep(1.0)
                        s = read_state()
                        run_tolk = s.get("tolk_output", []) if s else []
                        result_text = " ".join(t.strip() for t in run_tolk if t.strip())
                        if not result_text:
                            result_text = "Attempted to run..."
                        _cancel_to_battle_menu(s or state)
                        return result_text

                    _cancel_to_battle_menu(state)
                    return "\n".join(sub_text) if sub_text else "Action completed."

                if not sub_options and target_idx == 0:
                    # FIGHT -- fallback to RAM for moves
                    battle = _get_battle_info(state)
                    player_moves = battle.get("player_moves", [])
                    player_pp = battle.get("player_pp", [])
                    for i, move_id in enumerate(player_moves):
                        if move_id == 0:
                            continue
                        move_name = GEN1_MOVES[move_id] if 0 < move_id < len(GEN1_MOVES) else f"Move {move_id}"
                        pp = player_pp[i] if i < len(player_pp) else "?"
                        sub_options.append(f"{move_name} (PP: {pp})")

                if sub_options:
                    # Store pending sub-menu so get_state() presents it
                    # instead of auto-advancing and accidentally confirming
                    self._pending_sub_menu = sub_options
                    return "SUB_MENU:" + json.dumps(sub_options)

                return "Battle action executed."

            elif action == "battle_sub_select":
                target_idx = choice["index"]
                sub_options = choice.get("options", [])
                self._pending_sub_menu = None  # clear - player made their choice

                # Block moves with 0 PP - UNLESS all moves are 0 PP,
                # in which case the game uses Struggle automatically.
                selected_opt = sub_options[target_idx] if target_idx < len(sub_options) else ""
                all_moves_empty = all("(PP: 0)" in o for o in sub_options if "PP:" in o)
                if "(PP: 0)" in selected_opt and not all_moves_empty:
                    move_name = selected_opt.split("(")[0].strip()
                    # Re-present the same sub-menu so the agent picks a different move
                    self._pending_sub_menu = sub_options
                    return f"Can't use {move_name} - no PP left! Pick a different move. (PP refills when you visit a Pokémon Center.)"

                self._battle_cancel_streak = 0  # something actually happened
                fresh = read_state()

                # Detect if this is an item selection (items are vertical list)
                # vs move selection (2x2 grid). Items don't have "(PP:" in them.
                is_item_list = sub_options and not any("PP:" in o for o in sub_options)

                if is_item_list:
                    # Item bag: navigate vertically from current position
                    # Read screen to find current cursor, then navigate
                    item_name = sub_options[target_idx] if target_idx < len(sub_options) else "item"
                    base_name = item_name.split("x")[0].strip().split("×")[0].strip().upper()

                    def _qty_of(st):
                        """Bag count for the item being used, or None if unknown.

                        An empty bag means the bridge didn't report one, not
                        that it has nothing.  A readable bag without the item
                        is a real zero - that's the last-ball-spent case.
                        """
                        bag = read_bag(st or {})
                        if not bag:
                            return None
                        for nm, q in bag:
                            if nm.strip().upper() == base_name:
                                return q
                        return 0

                    qty_before = _qty_of(fresh)
                    if not _item_menu_select_by_name(item_name, len(sub_options)):
                        _cancel_to_battle_menu(read_state() or fresh)
                        return (f"Couldn't get the cursor onto {item_name} - the bag "
                                f"menu didn't respond. Nothing was used; try ITEM again.")

                    # Poll for result instead of blind delay
                    post = None
                    for _ in range(15):
                        time.sleep(0.15)
                        post = read_state()
                        if post and (post.get("tolk_output") or post.get("mode") != "battle"):
                            break

                    # Check if the game accepted or rejected the item use.
                    #
                    # The bag count is what settles it.  A thrown ball is spent
                    # whether or not the catch sticks, and a refused one is not.
                    # Judging by "the battle menu went away" instead reported a
                    # successful throw as a refusal, because the throw animation
                    # clears the menu too.
                    if post:
                        post_tolk = " ".join(post.get("tolk_output", [])).upper()
                        game_said = " ".join(
                            t.strip() for t in _dedup_progressive_text(
                                post.get("tolk_output", []) or []) if t.strip()
                        )
                        rejected = any(phrase in post_tolk for phrase in [
                            "WON'T HAVE ANY EFFECT",
                            "NOT THE TIME",
                            "CAN'T USE",
                            "NO EFFECT",
                            "OAK:",
                            "THIS ISN'T THE TIME",
                        ])
                        qty_after = _qty_of(post)
                        spent = (qty_before is not None and qty_after is not None
                                 and qty_after < qty_before)
                        if rejected and not spent:
                            send_command("INTERACT")
                            time.sleep(0.5)
                            after = read_state() or post
                            _cancel_to_battle_menu(after)
                            # Quote the game.  The old catch-all ("it won't have
                            # any effect") was our own wording, not Blue's, so
                            # every refusal read the same and there was no way to
                            # tell a full party from the wrong item being picked.
                            reason = f' The game said: "{game_said}"' if game_said else ""
                            if base_name in _BALL_NAMES:
                                self._ball_refused_this_battle = True
                                if len(after.get("party", []) or []) >= 6:
                                    return (f"Can't use {item_name} - your party is full (6 Pokémon) and "
                                            f"there's no room to store another. Deposit a Pokémon in the PC "
                                            f"at a Pokémon Center before trying to catch anything else.")
                                return (f"The game refused {item_name} and the ball was NOT used up."
                                        f"{reason} In a wild battle a Poké Ball is only refused when "
                                        f"there is nowhere to put the Pokémon - a status condition like "
                                        f"poison never blocks a catch, it helps. Do NOT retry the same "
                                        f"ball this battle: beat this Pokémon or RUN, and report the "
                                        f"message above so it can be looked at.")
                            return f"Can't use {item_name} right now.{reason}"
                        if game_said:
                            return f"Used {item_name}. {game_said}"

                    return f"Used {item_name}."
                else:
                    # Move menu - navigate from tracked cursor position.
                    # Gen 1 remembers cursor between turns; we track it.
                    _move_menu_select(self._move_cursor, target_idx, len(sub_options))
                    self._move_cursor = target_idx  # update tracked position

                    # Poll for the move to execute
                    for _ in range(15):
                        time.sleep(0.15)
                        check = read_state()
                        if check and check.get("mode") != "battle":
                            break
                        # Move executed when battle_menu clears (attack animating)
                        if check and not check.get("battle_menu"):
                            break
                    return "Move selected."

            elif action == "battle_sub_cancel":
                self._pending_sub_menu = None  # clear - player cancelled
                self._battle_cancel_streak += 1
                send_command("CANCEL")
                time.sleep(0.3)
                return "Cancelled."

            elif action == "party_select":
                # In-battle party entries span multiple rendered rows, so the
                # bridge's screen-derived menu_selected is not a party index.
                # Reset to the top and navigate from that known origin.
                target_idx = choice["index"]
                party = choice.get("party", [])
                mon = party[target_idx] if target_idx < len(party) else {}
                name = party_mon_name(mon) if mon else "that Pokémon"
                if mon.get("max_hp", 0) and mon.get("hp", 0) <= 0:
                    return (f"{name} has fainted and can't fight! "
                            f"Pick a Pokémon that still has HP.")
                self._battle_cancel_streak = 0
                if target_idx == choice.get("active_index", -1):
                    # Confirming the Pokémon that is already out just gets
                    # "X is already out!" and the list redrawn, forever.  Back
                    # out instead - on an optional switch that IS "keep it in".
                    send_command("CANCEL")
                    time.sleep(0.4)
                    s = read_state()
                    if s and _party_select_showing(s):
                        return (f"{name} is already out - this switch is forced, "
                                f"so you have to pick a DIFFERENT Pokémon.")
                    return f"Kept {name} in the battle."
                _party_menu_select(choice.get("active_index", -1),
                                   target_idx, len(party))
                time.sleep(0.6)
                # Backstop for when the name match above couldn't identify the
                # active Pokémon: the game rejects the pick and leaves the list
                # up, which looks identical to a successful send.
                s = read_state()
                if s:
                    joined = " ".join(s.get("tolk_output", []) or []).upper()
                    if "ALREADY OUT" in joined:
                        send_command("CANCEL")
                        time.sleep(0.4)
                        s = read_state()
                        if s and _party_select_showing(s):
                            return (f"{name} is already out - pick a DIFFERENT "
                                    f"Pokémon, this switch is forced.")
                        return f"Kept {name} in the battle."
                # Never claim the requested Pokémon was sent unless battle RAM
                # agrees. The switch animation may still be finishing, so poll
                # briefly for the active-party identity to settle.
                actual_idx = -1
                final_state = s or {}
                for _ in range(12):
                    if final_state:
                        actual_idx = _active_party_index(final_state)
                        if actual_idx >= 0 and actual_idx != choice.get("active_index", -1):
                            break
                    time.sleep(0.15)
                    final_state = read_state() or final_state
                if actual_idx >= 0 and actual_idx != target_idx:
                    actual_mon = party[actual_idx] if actual_idx < len(party) else {}
                    actual_name = party_mon_name(actual_mon) if actual_mon else "another Pokémon"
                    return (f"Tried to send out {name}, but the game actually selected "
                            f"{actual_name}.")
                if actual_idx == target_idx:
                    return f"Sent out {name}."
                return f"Selected {name} to send out; the switch is still resolving."

            elif action == "party_cancel":
                self._battle_cancel_streak = 0
                send_command("CANCEL")
                time.sleep(0.4)
                s = read_state()
                if s and _party_select_showing(s):
                    # Forced switch - the game won't take B for an answer.
                    return ("You have to send out a Pokémon - you can't back "
                            "out of this one.")
                return "Kept your current Pokémon."

            elif action == "switch_prompt":
                # YES/NO on "Will RED change POKéMON?".  NO is answered with B,
                # which Gen 1 accepts on any yes/no box; YES needs the cursor,
                # so check where the arrow actually is before pressing A.
                self._battle_cancel_streak = 0
                if choice.get("switch"):
                    s = read_state() or {}
                    if _yes_no_cursor_on_no(s):
                        send_command("NAV:UP")
                        time.sleep(0.15)
                    send_command("INTERACT")
                    time.sleep(0.5)
                    return "Opening your party - pick who goes in."
                send_command("CANCEL")
                # Wait for the box to actually close.  If the prompt text is
                # still on screen when the next state is read it gets presented
                # again, which is the loop this whole branch exists to stop.
                for _ in range(12):
                    time.sleep(0.2)
                    s = read_state()
                    if s and not _switch_prompt_showing(s):
                        break
                return f"{choice.get('label', 'Kept your current Pokémon')}."

            elif action == "menu_select":
                idx = choice["index"]
                selected = choice.get("selected", -1)
                total = choice.get("total", 0)

                # Remember the entry name - if a USE/TOSS sub-menu comes back,
                # this is the item it belongs to.  Strip the quantity the same
                # way the item hints do.
                raw_label = choice.get("label", "")
                if raw_label.strip().upper() == "DEPOSIT PKMN":
                    self._pc_deposit_pending = True
                    self._pc_party_cursor_parked = False
                self._selected_item_name = (
                    raw_label.split("¥")[0].split("x")[0].strip()
                    .split("×")[0].strip().upper()
                )

                # Use item list cache if available, and re-detect cursor
                # from fresh state to ensure accurate navigation delta
                if self._item_list_cache:
                    cached = self._item_list_cache
                    cached_items, cached_sel = cached[0], cached[1]
                    full_scan = cached[2] if len(cached) > 2 else False
                    self._item_list_cache = None
                    if full_scan:
                        # Cursor was parked at the top of the full list already.
                        selected = cached_sel
                    else:
                        # Re-read screen to confirm current arrow position
                        fresh = read_state()
                        if fresh:
                            _, fresh_sel = _read_visible_item_list(fresh)
                            selected = fresh_sel if fresh_sel >= 0 else cached_sel
                        else:
                            selected = cached_sel

                if selected >= 0:
                    delta = idx - selected
                    if delta > 0:
                        for _ in range(delta):
                            send_command("NAV:DOWN")
                            time.sleep(0.15)
                    elif delta < 0:
                        for _ in range(-delta):
                            send_command("NAV:UP")
                            time.sleep(0.15)
                else:
                    for _ in range(idx):
                        send_command("NAV:DOWN")
                        time.sleep(0.15)

                send_command("INTERACT")
                time.sleep(0.3)

                # Capture any response text (e.g., "You have no items")
                # Also break early if a new sub-menu appeared (e.g., USE/TOSS)
                response_lines = []
                pre_items = choice.get("_menu_items", [])
                for _ in range(10):
                    s = read_state()
                    if not s:
                        time.sleep(0.2)
                        continue
                    m = s.get("mode", "")
                    if m == "dialogue":
                        # Auto-advance dialogue and capture response text
                        s, lines = auto_advance_dialogue(s)
                        response_lines.extend(lines)
                        break
                    elif m != "menu":
                        # Capture tolk from non-menu state (brief messages)
                        for line in s.get("tolk_output", []):
                            clean = line.lstrip("\u2192").strip()
                            if clean:
                                response_lines.append(clean)
                        break
                    else:
                        # Still in menu - check if menu items changed (sub-menu appeared)
                        new_items = s.get("menu_items", [])
                        if new_items and new_items != pre_items:
                            break  # new sub-menu appeared, let get_state() handle it
                    time.sleep(0.2)

                # After YES/NO in dialogue: step away to prevent re-trigger
                time.sleep(0.5)
                post = read_state()
                if post and post.get("mode") == "overworld":
                    send_command("PRESS:down")
                    time.sleep(0.3)

                label = choice.get('label', '?')
                if response_lines:
                    return f"Selected: {label}\n" + "\n".join(response_lines)
                return f"Selected: {label}"

            elif action == "pc_deposit_select":
                party_index = choice.get("party_index", 0)
                party_count = choice.get("party_count", 0)
                mon_name = choice.get("label", "that Pokemon")

                # get_game_state() parked the list at party slot zero.
                for _ in range(party_index):
                    send_command("NAV:DOWN")
                    time.sleep(0.15)
                send_command("INTERACT")

                # Gen 1 now opens a DEPOSIT / STATS / CANCEL action box. It is
                # not a yes/no prompt; the generic filter previously removed
                # DEPOSIT and STATS because both look like short all-caps names.
                confirm_state = None
                confirm_items = []
                confirm_selected = -1
                for _ in range(20):
                    time.sleep(0.15)
                    s = read_state()
                    if not s:
                        continue
                    items, sel = get_menu_items(s)
                    upper = [item.strip().upper() for item in items]
                    if "DEPOSIT" in upper:
                        confirm_state = s
                        confirm_items = upper
                        confirm_selected = sel
                        break

                if confirm_state is None:
                    self._pc_party_cursor_parked = False
                    return (f"Selected {mon_name}, but the DEPOSIT confirmation did "
                            "not appear. Nothing was released; retry DEPOSIT PKMN.")

                deposit_index = confirm_items.index("DEPOSIT")
                menu_nav_to(deposit_index, confirm_selected)
                send_command("INTERACT")

                # Wait for RAM to prove the party shrank, collecting the game's
                # stored-in-box message if it appears. Never infer success from
                # the button press alone.
                said = []
                deposited = False
                for _ in range(25):
                    time.sleep(0.2)
                    s = read_state()
                    if not s:
                        continue
                    for line in ((s.get("tolk_output") or [])
                                 + (s.get("text_lines") or [])):
                        clean = line.lstrip("→").strip()
                        if clean and clean not in said:
                            said.append(clean)
                    if len(s.get("party", []) or []) < party_count:
                        deposited = True
                        break

                self._pc_deposit_pending = False
                self._pc_party_cursor_parked = False
                if deposited:
                    # Advance the success message back to the storage menu.
                    send_command("INTERACT")
                    time.sleep(0.4)
                    detail = " ".join(said)
                    return (f"Deposited {mon_name} safely in the PC."
                            + (f" {detail}" if detail else ""))
                return (f"The game did not confirm that {mon_name} was deposited. "
                        "Nothing was released; choose DEPOSIT PKMN and try again.")

            elif action == "buy_confirm":
                label = choice.get("label", "the item")
                said = []

                def _absorb(s):
                    for line in ((s.get("tolk_output") or [])
                                 + (s.get("text_lines") or [])):
                        clean = line.lstrip("→").strip()
                        if clean and clean not in said:
                            said.append(clean)

                send_command("INTERACT")  # lock in the quantity

                # Answer "That will be ¥N. OK?" as soon as it prints.
                answered = False
                for _ in range(20):
                    time.sleep(0.2)
                    s = read_state()
                    if not s:
                        continue
                    _absorb(s)
                    joined = " ".join(said).upper()
                    if any(p in joined for p in ("HERE YOU ARE", "THANK YOU",
                                                 "NOT ENOUGH", "CAN'T CARRY",
                                                 "CAN'T AFFORD")):
                        break
                    if not answered and "OK?" in joined:
                        if _yes_no_cursor_on_no(s):
                            send_command("NAV:UP")
                            time.sleep(0.15)
                        send_command("INTERACT")
                        answered = True

                # Press through "Here you are!  Thank you!" - but check the
                # screen BEFORE each press, or the first A landing back on the
                # shelf starts a second purchase.
                for _ in range(15):
                    s = read_state()
                    if s:
                        _absorb(s)
                        items, _ = get_menu_items(s)
                        if (s.get("mode") == "menu" and len(items) > 1
                                and not _is_qty_box(items)):
                            break
                    send_command("INTERACT")
                    time.sleep(0.25)

                joined = " ".join(said).upper()
                if any(p in joined for p in ("NOT ENOUGH", "CAN'T CARRY",
                                             "CAN'T AFFORD")):
                    return f"Couldn't buy {label} - {' '.join(said)}"
                money = (read_state() or {}).get("player_money")
                bought = f"Bought {label}."
                if money is not None:
                    bought += f" Balance is now ${money}."
                return bought

            elif action == "cancel":
                self._pc_deposit_pending = False
                self._pc_party_cursor_parked = False
                pre = read_state() or {}
                pre_items = pre.get("menu_items", [])
                pre_text = pre.get("text_lines", [])
                pre_mode = pre.get("mode")
                send_command("CANCEL")
                # Poll until the screen actually changes - a flat sleep can read
                # the pre-cancel frame back and show the same menu again, so the agent
                # sees the prompt it just backed out of.
                for _ in range(10):
                    time.sleep(0.15)
                    s = read_state()
                    if not s:
                        continue
                    if (s.get("menu_items", []) != pre_items
                            or s.get("text_lines", []) != pre_text
                            or s.get("mode") != pre_mode):
                        break
                return "Cancelled."

            elif action == "press_a":
                send_command("INTERACT")
                time.sleep(0.3)
                return "Pressed A."

            elif action == "press_b":
                send_command("CANCEL")
                time.sleep(0.3)
                return "Pressed B."

            else:
                return f"Unknown action: {action}"

        result = await asyncio.to_thread(_execute_sync)
        # Log directional actions for stuck detection
        if result and choice.get("action") == "walk_direction":
            self._recent_actions.append(result)
        return result

    async def send_name(self, name):
        """Type a name on the in-game keyboard."""
        def _type_sync():
            if name and name.strip():
                type_on_keyboard(name.strip())
            else:
                send_command("START")
                time.sleep(0.5)
        await asyncio.to_thread(_type_sync)

    async def save_state(self):
        """Save game via in-game SAVE menu (writes .sav battery file).

        Returns True once the save prompts were pressed through, False when
        the save could not be reached (in a battle, or no START menu).

        Opens START menu, navigates to SAVE, presses A through all
        confirmations (player info, 'Would you like to save?',
        'Erase old data?', save animation, 'Saved!' text).
        """
        def _save_sync():
            print("Save:Starting in-game save...")

            # Make sure we're in overworld first - cancel out of any menus/dialogue
            state = read_state()
            mode = state.get("mode", "") if state else ""
            print(f"Save:Current mode: {mode}")

            if mode == "battle":
                print("Save:In battle - skipping in-game save")
                return False

            # If already in START menu, try to use it directly
            if mode == "menu":
                s = state
                items, sel = get_menu_items(s)
                upper_check = [it.strip().upper() for it in items]
                if "SAVE" in upper_check:
                    print("Save:Already in START menu - navigating to SAVE directly")
                else:
                    # In a different menu (items, party, etc.) - exit to overworld first
                    print("Save:In non-START menu - pressing B to exit...")
                    for _ in range(8):
                        send_command("CANCEL")
                        time.sleep(0.3)
                        state = read_state()
                        if state and state.get("mode") == "overworld":
                            break
                    s = None  # force re-open below

            elif mode == "dialogue":
                print("Save:In dialogue - pressing B/A to exit...")
                for i in range(8):
                    # Alternate A and B - the save/badge screen needs A to advance
                    send_command("INTERACT" if i % 2 == 0 else "CANCEL")
                    time.sleep(0.3)
                    state = read_state()
                    if state and state.get("mode") == "overworld":
                        break
                s = None
            else:
                s = None

            # Open START menu if we're not already in it
            if s is None or s.get("mode") != "menu":
                # Wait for game to settle after exiting menus, then retry
                time.sleep(0.5)
                s = None
                for _retry in range(3):
                    open_start_menu()
                    time.sleep(0.5)
                    s = read_state()
                    if s and s.get("mode") == "menu":
                        break
                    # May still be in dialogue/transition - press B and retry
                    send_command("CANCEL")
                    time.sleep(0.5)
                    s = None
                if not s or s.get("mode") != "menu":
                    print(f"Save:Failed to open START menu (mode: {s.get('mode') if s else 'None'})")
                    return False

            items, sel = get_menu_items(s)
            upper_items = [it.strip().upper() for it in items]
            print(f"Save:Menu items: {upper_items}, selected: {sel}")

            if "SAVE" not in upper_items:
                # This isn't the START menu - a sub-menu (the bag, most likely)
                # was already open when START was pressed, so START just closed
                # something instead of opening the main menu.  Back all the way
                # out to the overworld and open it once more before giving up;
                # the .sav is worth one retry.
                print("Save:Not the START menu - backing out and retrying once")
                for _ in range(8):
                    send_command("CANCEL")
                    time.sleep(0.3)
                    st = read_state()
                    if st and st.get("mode") == "overworld":
                        break
                time.sleep(0.5)
                open_start_menu()
                time.sleep(0.5)
                s = read_state()
                items, sel = get_menu_items(s) if s else ([], -1)
                upper_items = [it.strip().upper() for it in items]
                print(f"Save:Retry menu items: {upper_items}, selected: {sel}")

            if "SAVE" not in upper_items:
                print("Save:SAVE not in menu - cancelling")
                send_command("CANCEL")
                return False

            save_idx = upper_items.index("SAVE")
            if sel >= 0:
                delta = save_idx - sel
                for _ in range(abs(delta)):
                    send_command("NAV:DOWN" if delta > 0 else "NAV:UP")
                    time.sleep(0.15)
            else:
                for _ in range(save_idx):
                    send_command("NAV:DOWN")
                    time.sleep(0.15)

            # Select SAVE
            print(f"Save:Navigated to SAVE (index {save_idx}), selecting...")
            send_command("INTERACT")
            time.sleep(1.0)

            # Now press A repeatedly to get through ALL save prompts:
            # 1. Player info screen → A
            # 2. "Would you like to SAVE?" YES is default → A
            # 3. "Erase old data? Okay?" YES is default → A
            # 4. Saving animation (~2-3 sec) → wait
            # 5. "saved the game!" → A
            # Press A 10 times with 1s gaps - more than enough
            for i in range(10):
                send_command("INTERACT")
                time.sleep(1.0)
                s2 = read_state()
                s2_mode = s2.get("mode", "") if s2 else ""
                print(f"Save:Press A #{i+1}, mode: {s2_mode}")
                if s2_mode == "overworld":
                    print("Save:Back to overworld - save complete!")
                    break

            # Press B a few times to close any lingering menu
            for _ in range(3):
                send_command("CANCEL")
                time.sleep(0.3)

            # Wait a moment for the .sav file to be flushed to disk
            time.sleep(1.0)
            print("Save:In-game save finished.")
            return True

        return await asyncio.to_thread(_save_sync)

    async def quit(self):
        """Terminate VBA-RR, clean up. Call save_state() before this."""
        def _quit_sync():

            # Clean up bridge files
            for f in [CMD_FILE, TEXT_FILE]:
                try:
                    f.unlink()
                except FileNotFoundError:
                    pass

            # Terminate VBA-RR
            if self.process:
                try:
                    self.process.terminate()
                    self.process.wait(timeout=5)
                except Exception:
                    try:
                        self.process.kill()
                    except Exception:
                        pass
                self.process = None

        await asyncio.to_thread(_quit_sync)

    def has_save_files(self):
        """Check for existing .sav in ROM dir (where VBA loads from)."""
        rom_sav = self._rom_sav_path()
        return bool(rom_sav and rom_sav.exists())

    def get_text_history(self):
        """Return the text history buffer."""
        return list(TEXT_HISTORY)

    def get_accumulated_text(self):
        """Return accumulated dialogue text and clear the buffer."""
        text = list(self.accumulated_text)
        self.accumulated_text.clear()
        return text

    def get_sav_path(self):
        """Return the current .sav file path (battery save), or None."""
        rom_sav = self._rom_sav_path()
        if rom_sav and rom_sav.exists():
            return str(rom_sav)
        return None

    def read_new_inputs(self):
        """Return button presses the bridge logged since the last call.

        The bridge appends one JSON line per pressed frame. The file is read
        from where the last call stopped and never truncated, because any
        command sent to clear it would also clear the bridge's screen text.
        """
        try:
            with open(INPUT_LOG_FILE, "rb") as f:
                f.seek(self._input_log_offset)
                data = f.read()
        except (FileNotFoundError, OSError):
            return []
        end = data.rfind(b"\n")
        if end < 0:
            return []
        self._input_log_offset += end + 1
        inputs = []
        for line in data[:end].decode("utf-8", errors="replace").splitlines():
            try:
                inputs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return inputs
