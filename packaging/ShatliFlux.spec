# Build from repository root: pyinstaller --clean packaging/ShatliFlux.spec
from pathlib import Path
root = Path(SPECPATH).parent

a = Analysis(
    [str(root / "app" / "main.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / "app" / "assets"), "app/assets"),
        (str(root / "app" / "ui" / "theme.qss"), "app/ui"),
        (str(root / "app" / "core" / "scripts"), "app/core/scripts"),
        (str(root / "config" / "app.json"), "config"),
        (str(root / "config" / "network.json"), "config"),
        (str(root / "README.md"), "."),
        (str(root / "THIRD_PARTY_NOTICES.md"), "."),
    ],
    hiddenimports=["app.core.admin_operations"],
    hookspath=[], runtime_hooks=[], excludes=["server", "tests", "aiogram"],
    noarchive=False, optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ShatliFlux",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False,
          icon=str(root / "app" / "assets" / "icons" / "app.ico"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ShatliFlux")
