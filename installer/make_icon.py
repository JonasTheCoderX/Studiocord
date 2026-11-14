"""Draws installer/studiocord.ico (and a PNG preview). Needs Pillow; run once, output is committed."""
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([40, 40, S - 40, S - 40], radius=230, fill=(88, 101, 242, 255))  # Discord blurple
# White "</>" (code) with a green "online" dot, like a Discord status.
W = (255, 255, 255, 255)
lw = 78
d.line([(400, 330), (250, 500), (400, 670)], fill=W, width=lw, joint="curve")
d.line([(624, 330), (774, 500), (624, 670)], fill=W, width=lw, joint="curve")
d.line([(560, 290), (464, 710)], fill=W, width=lw)
for x, y in [(400, 330), (250, 500), (400, 670), (624, 330), (774, 500), (624, 670), (560, 290), (464, 710)]:
    d.ellipse([x - lw / 2, y - lw / 2, x + lw / 2, y + lw / 2], fill=W)  # round line caps
d.ellipse([700, 700, 960, 960], fill=(88, 101, 242, 255))   # cut-out ring
d.ellipse([740, 740, 920, 920], fill=(35, 165, 90, 255))    # green dot
img.save(HERE / "studiocord.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.resize((256, 256), Image.LANCZOS).save(HERE / "studiocord.png")
print("wrote", HERE / "studiocord.ico")
