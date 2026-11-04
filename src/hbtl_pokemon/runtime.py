"""The emulator, third-party downloads, the ROM check, and the per-run game folder.

VBA-RR ships in vendor/VBA-RR and is copied into runtime/. pokemon-access and
the map database are fetched by setup.py into runtime/, and the player's own
ROM is copied into each run's folder.
"""

from __future__ import annotations

import filecmp
import io
import shutil
import zipfile
import zlib
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
VBA_DIR = RUNTIME / "vba"
ACCESS_DIR = RUNTIME / "pokemon-access"
MAP_DB = RUNTIME / "map_database.json"
GAMES_DIR = RUNTIME / "games"
BRIDGE_TEMPLATE = ROOT / "lua" / "pokemon_bridge.lua"

# VBA-RR (GPL-2.0) with its LuaJIT lua51.dll, the build the bridge was written
# against. Shipped in the repo and copied into runtime/ so the ini it rewrites
# per run never touches the committed copy.
VBA_VENDOR = ROOT / "vendor" / "VBA-RR"

# pokemon-access, the screen reader scripts the bridge loads (no license, so
# it is downloaded here and never committed).
ACCESS_COMMIT = "2deb98f38a4e6674ff60af6f9419be22865f1f39"
ACCESS_URL = f"https://github.com/nuive/pokemon-access/archive/{ACCESS_COMMIT}.zip"

# The two supported dumps, Pokemon Red and Blue (USA, Europe) (SGB Enhanced).
ROM_CRC32 = {"red": 0x9F7FDD53, "blue": 0xD6DA8A1A}
ROM_NAMES = {
    "red": "Pokemon - Red Version (USA, Europe) (SGB Enhanced)",
    "blue": "Pokemon - Blue Version (USA, Europe) (SGB Enhanced)",
}



def vba_exe() -> Path:
    return VBA_DIR / "vba.exe"


def _download(url: str) -> bytes:
    resp = requests.get(url, timeout=300)
    resp.raise_for_status()
    return resp.content


def install_vba() -> Path:
    """Copy the shipped VBA-RR into runtime/vba, unless the same build is there."""
    current = all(
        (VBA_DIR / name).exists() and filecmp.cmp(VBA_VENDOR / name, VBA_DIR / name, shallow=False)
        for name in ("vba.exe", "lua51.dll")
    )
    if not current:
        if VBA_DIR.exists():
            shutil.rmtree(VBA_DIR)
        shutil.copytree(VBA_VENDOR, VBA_DIR)
    return vba_exe()


def install_pokemon_access(say) -> Path:
    """Download pokemon-access at the pinned commit into runtime/pokemon-access."""
    if (ACCESS_DIR / "pokemon.lua").exists():
        return ACCESS_DIR
    say("Downloading pokemon-access...")
    zf = zipfile.ZipFile(io.BytesIO(_download(ACCESS_URL)))
    prefix = f"pokemon-access-{ACCESS_COMMIT}/"
    for name in zf.namelist():
        if not name.startswith(prefix) or name.endswith("/"):
            continue
        target = ACCESS_DIR / name[len(prefix):]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zf.read(name))
    return ACCESS_DIR


def build_map_database(say) -> Path:
    if MAP_DB.exists():
        return MAP_DB
    import importlib.util
    spec = importlib.util.spec_from_file_location("prepare_maps", ROOT / "tools" / "prepare_maps.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.main(MAP_DB)
    return MAP_DB


def rom_crc32(path: Path) -> int:
    return zlib.crc32(Path(path).read_bytes()) & 0xFFFFFFFF


def check_rom(path: Path, game: str) -> str:
    """Return "" if the ROM is the right dump, or a message saying what is wrong."""
    path = Path(path)
    if not path.is_file():
        return f"No file at {path}."
    crc = rom_crc32(path)
    if crc == ROM_CRC32[game]:
        return ""
    other = "blue" if game == "red" else "red"
    if crc == ROM_CRC32[other]:
        return (f"That file is Pokemon {other.title()}, not {game.title()}. "
                f"Choose {other.title()} as the game or pick the {game.title()} ROM.")
    return (f"That file doesn't match the supported dump. The benchmark needs "
            f"\"{ROM_NAMES[game]}\" (CRC32 {ROM_CRC32[game]:08X}); this file is {crc:08X}.")


def find_rom(path: Path, game: str) -> tuple[Path | None, str]:
    """Accept the ROM file itself or the folder it's in.

    Returns (rom path, "") when the right dump is found, or (None, message).
    """
    path = Path(str(path).strip().strip('"'))
    if path.is_dir():
        for candidate in sorted(path.glob("*.gb")):
            if rom_crc32(candidate) == ROM_CRC32[game]:
                return candidate, ""
        return None, (f"No Pokemon {game.title()} ROM in {path}. The benchmark needs "
                      f"\"{ROM_NAMES[game]}\" as a .gb file.")
    problem = check_rom(path, game)
    return (None, problem) if problem else (path, "")


def stage_game(game_dir: Path, rom_path: Path, game: str, save_file: Path | None) -> dict:
    """Prepare a run's own game folder: ROM copy, optional .sav, bridge and its paths.

    The .sav sits next to the ROM because that is where VBA-RR loads it from.
    """
    game_dir.mkdir(parents=True, exist_ok=True)
    rom = game_dir / f"pokemon_{game}.gb"
    if not rom.exists():
        shutil.copy2(rom_path, rom)
    sav = rom.with_suffix(".sav")
    if save_file is not None:
        shutil.copy2(save_file, sav)
    bridge_dir = game_dir / "_bridge"
    bridge_dir.mkdir(exist_ok=True)

    def lua_path(p: Path) -> str:
        return str(p.resolve()).replace("\\", "\\\\") + "\\\\"

    lua = BRIDGE_TEMPLATE.read_text(encoding="utf-8")
    lua = lua.replace("{{BRIDGE_DIR}}", lua_path(bridge_dir))
    lua = lua.replace("{{POKEMON_ACCESS_DIR}}", lua_path(ACCESS_DIR))
    bridge = game_dir / "pokemon_bridge.lua"
    bridge.write_text(lua, encoding="utf-8")
    return {"rom": rom, "sav": sav, "bridge_dir": bridge_dir, "lua": bridge}
