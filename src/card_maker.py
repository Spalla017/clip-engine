"""Text card (news headline or Reddit post title + source name) drawn locally with PIL.

It is our own graphic quoting a source by name, not a screenshot of another site.
"""
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .common import ENGINE

FONT = ENGINE / "public" / "fonts" / "Montserrat-Bold.ttf"
SMALL = ENGINE / "public" / "fonts" / "Inter-Bold.ttf"


def make_card(text, source, out, accent="#FF2E88", width=860):
    title_font = ImageFont.truetype(str(FONT), 54)
    src_font = ImageFont.truetype(str(SMALL), 32)
    lines = textwrap.wrap(text.strip(), width=25)[:5]
    pad, line_h = 46, 70
    height = pad * 2 + 58 + line_h * len(lines)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, width - 1, height - 1], radius=30, fill=(14, 12, 28, 242))
    d.rounded_rectangle([0, 0, 16, height - 1], radius=8, fill=accent)
    d.text((pad + 8, pad - 8), source.upper(), font=src_font, fill=accent)
    for i, line in enumerate(lines):
        d.text((pad + 8, pad + 50 + i * line_h), line, font=title_font, fill="white")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out
