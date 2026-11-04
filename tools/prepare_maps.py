"""Build the map database from the pret/pokered source.

Downloads pokered at a pinned commit, then extracts:
  - Map dimensions, tileset IDs and map numbers from constants/headers
  - Warps, signs (bg_events), NPCs from data/maps/objects/*.asm
  - Block layouts from maps/*.blk
  - Blockset definitions from gfx/blocksets/*.bst
  - Passability grids from tile collision data

At runtime the game layer looks up the current map by number and gets:
  - Pre-computed passability grid (no screen capture needed)
  - Warps with destination names
  - Signs/interactables with correct labels
  - NPCs with sprite names and movement types

Usage: python tools/prepare_maps.py [output_path]
"""

import io
import json
import re
import zipfile
from pathlib import Path

import sys

import requests

# ── Paths ────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "runtime" / "map_database.json"

# Pinned so every copy of the benchmark builds the same map database.
POKERED_COMMIT = "0dbf95cd12f5b1511c62ce69efe283000f780a8f"
REPO_ZIP = f"https://github.com/pret/pokered/archive/{POKERED_COMMIT}.zip"

# ── Tileset data (same as prepare_tiles.py) ──────────────────────────────

PASSABLE_TILES = {
    0:  {0x00,0x10,0x1b,0x20,0x21,0x23,0x2c,0x2d,0x2e,0x30,0x31,0x33,0x39,0x3c,0x3e,0x52,0x54,0x58,0x5b},
    1:  {0x01,0x02,0x03,0x11,0x12,0x13,0x14,0x1c,0x1a},
    2:  {0x11,0x1a,0x1c,0x3c,0x5e},
    3:  {0x1e,0x20,0x2e,0x30,0x34,0x37,0x39,0x3a,0x40,0x51,0x52,0x5a,0x5c,0x5e,0x5f},
    4:  {0x01,0x02,0x03,0x11,0x12,0x13,0x14,0x1c,0x1a},
    5:  {0x11,0x16,0x19,0x2b,0x3c,0x3d,0x3f,0x4a,0x4c,0x4d,0x03},
    6:  {0x11,0x1a,0x1c,0x3c,0x5e},
    7:  {0x11,0x16,0x19,0x2b,0x3c,0x3d,0x3f,0x4a,0x4c,0x4d,0x03},
    8:  {0x01,0x12,0x14,0x28,0x32,0x37,0x44,0x54,0x5c},
    9:  {0x01,0x12,0x14,0x1a,0x1c,0x37,0x38,0x3b,0x3c,0x5e},
    10: {0x01,0x12,0x14,0x1a,0x1c,0x37,0x38,0x3b,0x3c,0x5e},
    11: {0x0b,0x0c,0x13,0x15,0x18},
    12: {0x01,0x12,0x14,0x1a,0x1c,0x37,0x38,0x3b,0x3c,0x5e},
    13: {0x04,0x0d,0x17,0x1d,0x1e,0x23,0x34,0x37,0x39,0x4a},
    14: {0x0a,0x1a,0x32,0x3b},
    15: {0x01,0x10,0x13,0x1b,0x22,0x42,0x52},
    16: {0x04,0x0f,0x15,0x1f,0x3b,0x45,0x47,0x55,0x56},
    17: {0x05,0x15,0x18,0x1a,0x20,0x21,0x22,0x2a,0x2d,0x30},
    18: {0x14,0x17,0x1a,0x1c,0x20,0x38,0x45},
    19: {0x01,0x05,0x11,0x12,0x14,0x1a,0x1c,0x2c,0x53},
    20: {0x0c,0x26,0x16,0x1e,0x34,0x37},
    21: {0x0f,0x1a,0x1f,0x26,0x28,0x29,0x2c,0x2d,0x2e,0x2f,0x41},
    22: {0x01,0x10,0x11,0x13,0x1b,0x20,0x21,0x22,0x30,0x31,0x32,0x42,0x43,0x48,0x52,0x55,0x58,0x5e},
    23: {0x1b,0x23,0x2c,0x2d,0x3b,0x45},
}

TILESET_NAME_TO_ID = {
    "OVERWORLD": 0, "REDS_HOUSE_1": 1, "MART": 2, "FOREST": 3,
    "REDS_HOUSE_2": 4, "DOJO": 5, "POKECENTER": 6, "GYM": 7,
    "HOUSE": 8, "FOREST_GATE": 9, "MUSEUM": 10, "UNDERGROUND": 11,
    "GATE": 12, "SHIP": 13, "SHIP_PORT": 14, "CEMETERY": 15,
    "INTERIOR": 16, "CAVERN": 17, "LOBBY": 18, "MANSION": 19,
    "LAB": 20, "CLUB": 21, "FACILITY": 22, "PLATEAU": 23,
}

BLOCKSET_FILES = {
    0: "overworld", 1: "reds_house", 2: "pokecenter", 3: "forest",
    4: "reds_house", 5: "gym", 6: "pokecenter", 7: "gym",
    8: "house", 9: "gate", 10: "gate", 11: "underground",
    12: "gate", 13: "ship", 14: "ship_port", 15: "cemetery",
    16: "interior", 17: "cavern", 18: "lobby", 19: "mansion",
    20: "lab", 21: "club", 22: "facility", 23: "plateau",
}

# ── Display name conversion ──────────────────────────────────────────────

_DISPLAY_SPECIAL = {
    "REDS_HOUSE_1F": "Red's House 1F",
    "REDS_HOUSE_2F": "Red's House 2F",
    "BLUES_HOUSE": "Blue's House",
    "OAKS_LAB": "Oak's Lab",
    "BILLS_HOUSE": "Bill's House",
    "MR_FUJIS_HOUSE": "Mr. Fuji's House",
    "MR_PSYCHICS_HOUSE": "Mr. Psychic's House",
    "NAME_RATERS_HOUSE": "Name Rater's House",
    "COPYCATS_HOUSE_1F": "Copycat's House 1F",
    "COPYCATS_HOUSE_2F": "Copycat's House 2F",
    "WARDENS_HOUSE": "Warden's House",
    "DIGLETTS_CAVE": "Diglett's Cave",
    "DIGLETTS_CAVE_ROUTE_2": "Diglett's Cave Route 2",
    "DIGLETTS_CAVE_ROUTE_11": "Diglett's Cave Route 11",
    "FUCHSIA_BILLS_GRANDPAS_HOUSE": "Fuchsia Bill's Grandpa's House",
    "SS_ANNE_1F": "SS Anne 1F", "SS_ANNE_2F": "SS Anne 2F",
    "SS_ANNE_3F": "SS Anne 3F", "SS_ANNE_B1F": "SS Anne B1F",
    "SS_ANNE_BOW": "SS Anne Bow", "SS_ANNE_KITCHEN": "SS Anne Kitchen",
    "SS_ANNE_CAPTAINS_ROOM": "SS Anne Captain's Room",
    "SS_ANNE_1F_ROOMS": "SS Anne 1F Rooms",
    "SS_ANNE_2F_ROOMS": "SS Anne 2F Rooms",
    "SS_ANNE_B1F_ROOMS": "SS Anne B1F Rooms",
    "POKEMON_TOWER_1F": "Pokemon Tower 1F", "POKEMON_TOWER_2F": "Pokemon Tower 2F",
    "POKEMON_TOWER_3F": "Pokemon Tower 3F", "POKEMON_TOWER_4F": "Pokemon Tower 4F",
    "POKEMON_TOWER_5F": "Pokemon Tower 5F", "POKEMON_TOWER_6F": "Pokemon Tower 6F",
    "POKEMON_TOWER_7F": "Pokemon Tower 7F",
    "POKEMON_MANSION_1F": "Pokemon Mansion 1F", "POKEMON_MANSION_2F": "Pokemon Mansion 2F",
    "POKEMON_MANSION_3F": "Pokemon Mansion 3F", "POKEMON_MANSION_B1F": "Pokemon Mansion B1F",
    "POKEMON_FAN_CLUB": "Pokemon Fan Club",
    "MT_MOON_1F": "Mt Moon 1F", "MT_MOON_B1F": "Mt Moon B1F", "MT_MOON_B2F": "Mt Moon B2F",
    "MT_MOON_POKECENTER": "Mt Moon Pokecenter",
    "LAST_MAP": "Outside",
}


def constant_to_display(constant):
    """Convert MAP_CONSTANT to display name like the bridge provides."""
    if constant in _DISPLAY_SPECIAL:
        return _DISPLAY_SPECIAL[constant]
    parts = constant.split("_")
    out = []
    for p in parts:
        if re.match(r'^B?\d+F$', p):
            out.append(p)  # floor designator as-is
        else:
            out.append(p.title())
    return " ".join(out)


# ── Sign / bg_event labeling ─────────────────────────────────────────────

_SIGN_KEYWORDS = {
    "TV": "TV", "TELEVISION": "TV", "PC": "PC", "COMPUTER": "PC",
    "BOOKSHELF": "Bookshelf", "POSTER": "Poster", "SIGN": "Sign",
    "TOWN_MAP": "Town Map", "TRASH": "Trash Can", "TRASH_CAN": "Trash Can",
    "BLACKBOARD": "Blackboard", "DIPLOMA": "Diploma", "MAP": "Map",
    "GLOBE": "Globe", "PICTURE": "Picture", "PAINTING": "Painting",
    "RADIO": "Radio", "NOTEBOOK": "Notebook", "MAGAZINE": "Magazine",
    "TRAINER_TIPS": "Sign", "GYM_SIGN": "Sign", "MARKET_SIGN": "Sign",
}


def label_sign(text_id, camel_name):
    """Extract a human-readable label from a bg_event text ID."""
    # Remove TEXT_ prefix and map name to get description
    upper_map = camel_name.upper()
    prefix = f"TEXT_{upper_map}_"
    if text_id.upper().startswith(prefix):
        desc = text_id[len(prefix):]
    elif text_id.upper().startswith("TEXT_"):
        desc = text_id[5:]
    else:
        desc = text_id

    # Check known keywords
    upper = desc.upper()
    for kw, label in _SIGN_KEYWORDS.items():
        if upper == kw or upper.endswith("_" + kw) or upper.startswith(kw + "_"):
            return label
        if kw in upper:
            return label

    # Fallback: if it looks like a numbered sign or tips, just call it "Sign"
    if any(c.isdigit() for c in desc):
        return "Sign"

    # Otherwise: clean up the description
    return desc.replace("_", " ").title()


# ── Zip reading helpers ──────────────────────────────────────────────────

def zread(zf, path):
    """Read text file from zip. Returns None if missing."""
    try:
        return zf.read(path).decode("utf-8", errors="replace")
    except KeyError:
        return None

def zread_bin(zf, path):
    """Read binary file from zip. Returns None if missing."""
    try:
        return zf.read(path)
    except KeyError:
        return None


# ── Parsing ──────────────────────────────────────────────────────────────

def parse_map_constants(text):
    """Parse map_constants.asm → {CONSTANT: {width, height, id}}.

    The map number the game keeps in wCurMap is the order of the map_const
    lines, starting from 0 for PALLET_TOWN.
    """
    maps = {}
    for map_id, m in enumerate(re.finditer(
            r'^\s*map_const\s+(\w+)\s*,\s*(\d+)\s*,\s*(\d+)', text, re.MULTILINE)):
        maps[m.group(1)] = {"width": int(m.group(2)), "height": int(m.group(3)),
                            "id": map_id}
    return maps


def parse_sprite_constants(text):
    """Parse sprite_constants.asm → {SPRITE_NAME: "Display Name"}."""
    sprites = {}
    for m in re.finditer(r'const\s+(SPRITE_\w+)', text):
        raw = m.group(1)
        display = raw.replace("SPRITE_", "").replace("_", " ").title()
        # Fix common names
        display = display.replace("Poke Ball", "Item").replace("Boulder", "Boulder")
        sprites[raw] = display
    return sprites


def parse_header(text):
    """Parse a map header file → (camel_name, constant, tileset_id)."""
    m = re.search(r'map_header\s+(\w+)\s*,\s*(\w+)\s*,\s*(\w+)', text)
    if not m:
        return None, None, 0
    return m.group(1), m.group(2), TILESET_NAME_TO_ID.get(m.group(3), 0)


def parse_objects(text, sprite_names, camel_name):
    """Parse a map objects file → (warps, signs, npcs)."""
    warps, signs, npcs = [], [], []

    for m in re.finditer(r'warp_event\s+(\d+)\s*,\s*(\d+)\s*,\s*(\w+)\s*,\s*(\d+)', text):
        warps.append({
            "x": int(m.group(1)), "y": int(m.group(2)),
            "dest_const": m.group(3),
        })

    for m in re.finditer(r'bg_event\s+(\d+)\s*,\s*(\d+)\s*,\s*(\w+)', text):
        text_id = m.group(3)
        signs.append({
            "x": int(m.group(1)), "y": int(m.group(2)),
            "label": label_sign(text_id, camel_name),
        })

    for m in re.finditer(
        r'object_event\s+(\d+)\s*,\s*(\d+)\s*,\s*(\w+)\s*,\s*(\w+)\s*,\s*(\w+)',
        text
    ):
        sprite_raw = m.group(3)
        movement = m.group(4)   # STAY or WALK
        direction = m.group(5)  # DOWN, UP, LEFT, RIGHT, NONE, etc.
        npcs.append({
            "x": int(m.group(1)), "y": int(m.group(2)),
            "sprite": sprite_names.get(sprite_raw, sprite_raw.replace("SPRITE_", "").replace("_", " ").title()),
            "stays": movement == "STAY",
        })

    return warps, signs, npcs


def parse_blockset(data):
    """Parse .bst → list of blocks, each block = 4x4 tile IDs."""
    blocks = []
    for i in range(len(data) // 16):
        b = data[i * 16:(i + 1) * 16]
        blocks.append([list(b[r * 4:(r + 1) * 4]) for r in range(4)])
    return blocks


# ── Passability ──────────────────────────────────────────────────────────

def build_passability(blk_data, blocks, w, h, passable_set):
    """Build passability grid (height*2 x width*2) from block layout.

    Each block (32x32 px) = 2x2 movement cells (16x16 px each).
    Gen 1 collision checks ONLY the bottom-left tile (dy=1, dx=0) of each
    2x2 movement cell against the passable tile list.
    """
    mw, mh = w * 2, h * 2
    grid = [[False] * mw for _ in range(mh)]

    for by in range(h):
        for bx in range(w):
            idx = by * w + bx
            if idx >= len(blk_data):
                continue
            bid = blk_data[idx]
            if bid >= len(blocks):
                continue
            block = blocks[bid]

            for cy in range(2):
                for cx in range(2):
                    # Gen 1 checks bottom-left tile of each 2x2 cell
                    tile_id = block[cy * 2 + 1][cx * 2]
                    grid[by * 2 + cy][bx * 2 + cx] = tile_id in passable_set
    return grid


# ── Main ─────────────────────────────────────────────────────────────────

def main(output_path=None):
    map_db_file = Path(output_path) if output_path else DEFAULT_OUTPUT

    # Download repo zip (one request, ~10 MB)
    print("Downloading pret/pokered to build the map database...")
    resp = requests.get(REPO_ZIP, timeout=180, stream=True)
    resp.raise_for_status()
    buf = io.BytesIO()
    total = int(resp.headers.get("content-length", 0))
    dl = 0
    for chunk in resp.iter_content(8192):
        buf.write(chunk)
        dl += len(chunk)
        if total:
            print(f"\r  {dl // 1024}KB / {total // 1024}KB ({dl * 100 // total}%)", end="", flush=True)
    print()
    buf.seek(0)
    zf = zipfile.ZipFile(buf)

    # Find zip prefix (e.g. "pokered-master/")
    prefix = ""
    for n in zf.namelist():
        if n.endswith("constants/map_constants.asm"):
            prefix = n.replace("constants/map_constants.asm", "")
            break

    # Parse constants
    map_consts = parse_map_constants(zread(zf, f"{prefix}constants/map_constants.asm") or "")
    sprite_names = parse_sprite_constants(zread(zf, f"{prefix}constants/sprite_constants.asm") or "")

    # Index files in zip
    headers, objects, blks = {}, {}, {}
    for n in zf.namelist():
        rel = n[len(prefix):] if n.startswith(prefix) else n
        stem = Path(rel).stem
        if rel.startswith("data/maps/headers/") and rel.endswith(".asm"):
            headers[stem] = n
        elif rel.startswith("data/maps/objects/") and rel.endswith(".asm"):
            objects[stem] = n
        elif rel.startswith("maps/") and rel.endswith(".blk"):
            blks[stem] = n

    # Load blocksets
    blocksets = {}
    loaded = {}
    for ts_id, bst_name in BLOCKSET_FILES.items():
        if bst_name in loaded:
            blocksets[ts_id] = loaded[bst_name]
            continue
        data = zread_bin(zf, f"{prefix}gfx/blocksets/{bst_name}.bst")
        if data:
            bs = parse_blockset(data)
            loaded[bst_name] = bs
            blocksets[ts_id] = bs

    # Build map database
    database = {}
    name_lookup = {}  # display_name → camel_name (for runtime lookup)
    id_to_key = {}    # map number → camel_name (what the game layer looks up)

    for camel, hpath in sorted(headers.items()):
        htxt = zread(zf, hpath)
        if not htxt:
            continue
        _, const, ts_id = parse_header(htxt)
        if not const or const not in map_consts:
            continue

        dims = map_consts[const]
        bw, bh = dims["width"], dims["height"]
        display = constant_to_display(const)

        # Objects
        otxt = zread(zf, objects.get(camel, ""))
        warps, signs, npcs_list = [], [], []
        if otxt:
            warps, signs, npcs_list = parse_objects(otxt, sprite_names, camel)

        # Resolve warp destinations
        for w in warps:
            w["dest"] = constant_to_display(w.pop("dest_const"))

        # Passability
        blk_data = zread_bin(zf, blks.get(camel, ""))
        passability = None
        if blk_data and ts_id in blocksets:
            passability = build_passability(
                blk_data, blocksets[ts_id], bw, bh, PASSABLE_TILES.get(ts_id, set())
            )

        entry = {
            "display_name": display,
            "width": bw * 2,
            "height": bh * 2,
            "tileset_id": ts_id,
            "warps": warps,
            "signs": signs,
            "npcs": npcs_list,
        }
        if passability:
            entry["passability"] = passability

        database[camel] = entry
        name_lookup[display] = camel

    # Number each map by its own constant. Matching on the header's constant
    # is not enough: UndergroundPathRoute7Copy's header reuses
    # UNDERGROUND_PATH_ROUTE_7, which would shift that map onto the wrong number.
    by_name = {camel.lower(): camel for camel in database}
    for const, dims in map_consts.items():
        camel = by_name.get(const.replace("_", "").lower())
        if camel:
            id_to_key[str(dims["id"])] = camel

    database["_name_lookup"] = name_lookup
    database["_id_to_key"] = id_to_key

    # Save
    map_db_file.parent.mkdir(parents=True, exist_ok=True)
    with open(map_db_file, "w") as f:
        json.dump(database, f, separators=(",", ":"))

    print(f"Saved the map database ({len(id_to_key)} maps) to {map_db_file}")
    return map_db_file


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
