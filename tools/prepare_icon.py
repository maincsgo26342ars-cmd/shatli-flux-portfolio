"""Render the SF Shatli FLUX mark as Windows ICO and high-resolution PNG."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "app" / "assets" / "icons"
SIZE = 1024
BLUE = "#0178D4"


def rounded_layer(box, radius, angle):
    layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle(box, radius=radius, fill="#000000", outline=BLUE, width=46)
    return layer.rotate(angle, Image.Resampling.BICUBIC, center=(SIZE // 2, SIZE // 2))


def italic_text():
    font = ImageFont.truetype(str(OUTPUT.parents[0] / "fonts" / "Mulish-SemiBold.ttf"), 310)
    scratch = Image.new("RGBA", (900, 500), (0, 0, 0, 0))
    draw = ImageDraw.Draw(scratch)
    bounds = draw.textbbox((0, 0), "SF", font=font, stroke_width=2)
    width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    text = Image.new("RGBA", (width + 100, height + 60), (0, 0, 0, 0))
    ImageDraw.Draw(text).text((50 - bounds[0], 30 - bounds[1]), "SF", font=font,
                              fill="#FFFFFF", stroke_width=2, stroke_fill="#FFFFFF")
    shear = 0.18
    return text.transform((text.width + round(text.height * shear), text.height),
                          Image.Transform.AFFINE, (1, -shear, 0, 0, 1, 0),
                          Image.Resampling.BICUBIC)


def main():
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    image.alpha_composite(rounded_layer((190, 150, 900, 910), 155, 7))
    image.alpha_composite(rounded_layer((85, 75, 825, 855), 155, -4))
    text = italic_text()
    image.alpha_composite(text, (445 - text.width // 2, 455 - text.height // 2))
    image = image.resize((256, 256), Image.Resampling.LANCZOS)
    image.save(OUTPUT / "app.png")
    image.save(OUTPUT / "app.ico", format="ICO",
               sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(OUTPUT / "app.ico")


if __name__ == "__main__":
    main()
