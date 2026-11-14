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

# Windows setup wizard images (Inno Setup wants 24-bit BMPs).
def wizard_bmp(w, h, icon_size, name):
    bg = Image.new("RGB", (w, h), (88, 101, 242))
    ic = img.resize((icon_size, icon_size), Image.LANCZOS)
    flat = Image.new("RGB", ic.size, (88, 101, 242))
    flat.paste(ic, mask=ic.split()[3])
    bg.paste(flat, ((w - icon_size) // 2, (h - icon_size) // 2))
    bg.save(HERE / name)


wizard_bmp(164, 314, 128, "studiocord-wizard.bmp")   # welcome / finish page
wizard_bmp(55, 58, 52, "studiocord-small.bmp")       # top-right on the other pages
print("wrote wizard images")
