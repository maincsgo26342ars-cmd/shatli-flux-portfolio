"""Build-time only: make static 400/600 Mulish fallback faces from official variable TTF.

Usage: python tools/prepare_fonts.py /path/to/Mulish-variable.ttf
The distributed font files retain their accompanying OFL license.
"""
import json
import os
from pathlib import Path
import sys
import tempfile

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont


def main():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "config/app.json").read_text())
    source = Path(sys.argv[1]).expanduser()
    for key, weight in (("font_cyrillic", 400), ("font_cyrillic_semibold", 600)):
        with TTFont(source) as font:
            result = instantiateVariableFont(font, {"wght": weight}, updateFontNames=True)
            target = root / config["paths"][key]
            fd, name = tempfile.mkstemp(prefix=".font-", suffix=".ttf", dir=target.parent)
            os.close(fd)
            try:
                result.save(name)
                os.replace(name, target)
            finally:
                result.close()
                if os.path.exists(name):
                    os.unlink(name)


if __name__ == "__main__":
    main()
