"""Read-only Steam library discovery for CS2. Never writes, launches or uploads anything."""
from pathlib import Path
import os
import re

GAME_FOLDER = "Counter-Strike Global Offensive"
CONFIG = ("game", "csgo", "cfg", "autoexec.cfg")
EXECUTABLE = ("game", "bin", "win64", "cs2.exe")


def unescape(value):
    """Undo the double backslashes used in Steam's VDF string values."""
    return re.sub(r"\\(.)", r"\1", value)


def library_paths(text):
    """Return every ``"path"`` value from a libraryfolders.vdf document."""
    paths = []
    for match in re.finditer(r'"path"\s+"((?:[^"\\]|\\.)*)"', text):
        root = unescape(match.group(1)).strip()
        if root and root not in paths:
            paths.append(root)
    return paths


def registry_roots():
    """Steam install locations from the current user and both registry views."""
    if os.name != "nt":
        return []
    import winreg

    locations = (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    )
    roots = []
    for hive, path, name in locations:
        try:
            with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                value = winreg.QueryValueEx(key, name)[0]
        except OSError:
            continue
        if isinstance(value, str) and value and value not in roots:
            roots.append(value)
    return roots


def discover(roots=None):
    """Find ``autoexec.cfg`` and ``cs2.exe`` without changing either file.

    ``roots`` is injectable for tests; production reads the Steam registry and
    then every library listed in ``steamapps/libraryfolders.vdf``.
    """
    roots = list(roots) if roots is not None else registry_roots()
    result = {"steam": roots[0] if roots else None,
              "library": None, "autoexec": None, "executable": None}
    seen = set()
    for steam in roots:
        libraries = [Path(steam)]
        try:
            text = (Path(steam) / "steamapps" / "libraryfolders.vdf").read_text(
                encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        libraries += [Path(item) for item in library_paths(text)]
        for library in libraries:
            key = str(library).casefold()
            if key in seen:
                continue
            seen.add(key)
            game = library / "steamapps" / "common" / GAME_FOLDER
            config = game.joinpath(*CONFIG)
            executable = game.joinpath(*EXECUTABLE)
            # The cfg folder may exist without autoexec.cfg; the file is then
            # created by the first apply, exactly like a manually chosen path.
            if config.parent.is_dir() and result["autoexec"] is None:
                result.update(autoexec=str(config), library=str(library))
            if executable.is_file() and result["executable"] is None:
                result.update(executable=str(executable), library=str(library))
            if result["autoexec"] and result["executable"]:
                return result
    return result
