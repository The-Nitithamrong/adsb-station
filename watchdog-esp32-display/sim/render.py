"""Replay a display list from the TFT_eSPI stub into PNGs (one per 'S name' snapshot).
Text uses the GLCD font's real metrics (6x8 px cell x textsize, opaque background, TFT_eSPI datums);
glyph shapes come from a monospace TTF squeezed into that cell, so it shows layout/overlap, not exact pixels."""
import sys, os
from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
_fonts = {}
def font(size):
    if size not in _fonts: _fonts[size] = ImageFont.truetype(FONT, int(round(size * 9.6)))
    return _fonts[size]
def rgb(c):
    c = int(c); return ((c >> 11) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31)

def run(dl, outdir, scale=2):
    os.makedirs(outdir, exist_ok=True)
    img = Image.new("RGB", (320, 240)); d = ImageDraw.Draw(img); d.fontmode = "1"
    names, warn = [], []
    for raw in open(dl, encoding="utf-8"):
        ln = raw.rstrip("\n"); k = ln[:1]; a = ln.split(" ")
        if k == "F":
            x, y, w, h = map(int, a[1:5]); d.rectangle([x, y, x + w - 1, y + h - 1], fill=rgb(a[5]))
        elif k == "R":
            x, y, w, h, r = map(int, a[1:6]); d.rounded_rectangle([x, y, x + w - 1, y + h - 1], r, fill=rgb(a[6]))
        elif k == "O":
            x, y, w, h, r = map(int, a[1:6]); d.rounded_rectangle([x, y, x + w - 1, y + h - 1], r, outline=rgb(a[6]))
        elif k == "C":
            x, y, r = map(int, a[1:4]); d.ellipse([x - r, y - r, x + r, y + r], fill=rgb(a[4]))
        elif k == "T":
            x, y, datum, size = map(int, a[1:5]); fg, bg = a[5], a[6]; text = ln.split(" ", 7)[7] if len(a) > 7 else ""
            w, h = 6 * size * len(text), 8 * size
            if not text: continue
            x0 = x - (w // 2 if datum % 3 == 1 else w if datum % 3 == 2 else 0)
            y0 = y - (h // 2 if datum // 3 == 1 else h if datum // 3 == 2 else 0)
            if x0 < 0 or y0 < 0 or x0 + w > 320 or y0 + h > 240: warn.append(f"OFF-SCREEN text {text!r} box=({x0},{y0},{w},{h})")
            if fg != bg: d.rectangle([x0, y0, x0 + w - 1, y0 + h - 1], fill=rgb(bg))
            for i, ch in enumerate(text):
                d.text((x0 + i * 6 * size, y0 + 7 * size), ch, font=font(size), fill=rgb(fg), anchor="ls")
        elif k == "S":
            name = a[1]; names.append(name)
            img.resize((320 * scale, 240 * scale), Image.NEAREST).save(os.path.join(outdir, name + ".png"))
    for w in sorted(set(warn)): print("WARN", w)
    return names

if __name__ == "__main__":
    print(" ".join(run(sys.argv[1], sys.argv[2])))
