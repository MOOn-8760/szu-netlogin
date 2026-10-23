"""生成 szu-netlogin 图标：校徽感的深蓝紫徽章 + Wi-Fi + 金色节点。"""
import io
import math
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
(ROOT / "assets").mkdir(exist_ok=True)
(ROOT / "docs").mkdir(exist_ok=True)

TOP = (18, 58, 140)
BOTTOM = (58, 30, 110)
GOLD = (242, 193, 78)
WHITE = (255, 255, 255)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\seguisb.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
]


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def load_font(size):
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def make(size: int) -> Image.Image:
    ss = 4
    S = size * ss
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    grad = Image.new("RGB", (1, S))
    for y in range(S):
        grad.putpixel((0, y), lerp(TOP, BOTTOM, y / max(1, S - 1)))
    grad = grad.resize((S, S))

    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.23), fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)

    # 徽章圆环：小尺寸会变细到看不清，只在 48 以上绘制
    if size >= 48:
        ring = S * 0.404
        d.ellipse([S / 2 - ring, S / 2 - ring, S / 2 + ring, S / 2 + ring],
                  outline=(255, 255, 255, 235), width=max(2, int(S * 0.016)))

    cx, cy = S * 0.5, S * 0.61
    # 小尺寸单独加粗，保证缩到 16px 仍然是一眼能认的 Wi-Fi
    stroke = max(size * (0.082 if size <= 32 else 0.066), 1.6) * ss
    cap = stroke / 2
    for r in (S * 0.10, S * 0.20, S * 0.30):
        d.arc([cx - r, cy - r, cx + r, cy + r], start=210, end=330,
              fill=WHITE, width=int(stroke))
        if size > 32:  # 极小尺寸下圆头会粘连，直接不画
            for deg in (210, 330):
                rad = math.radians(deg)
                px, py = cx + r * math.cos(rad), cy + r * math.sin(rad)
                d.ellipse([px - cap, py - cap, px + cap, py + cap], fill=WHITE)

    dr = S * (0.048 if size <= 32 else 0.052)
    d.ellipse([cx - dr, cy - dr, cx + dr, cy + dr], fill=GOLD)

    # 校名缩写：64 下会糊成一团，只在 128 以上绘制
    if size >= 128:
        font = load_font(int(S * 0.078))
        text = "SZU"
        bbox = d.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text((S / 2 - tw / 2 - bbox[0], S * 0.785 - th / 2 - bbox[1]),
               text, font=font, fill=(255, 255, 255, 235))

    return img.resize((size, size), Image.LANCZOS)


def write_ico(path: Path, images):
    images = sorted(images, key=lambda im: im.width)
    payloads = []
    for im in images:
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        payloads.append(buf.getvalue())

    with open(path, "wb") as fh:
        fh.write(struct.pack("<HHH", 0, 1, len(images)))
        offset = 6 + 16 * len(images)
        for im, data in zip(images, payloads):
            w = 0 if im.width >= 256 else im.width
            h = 0 if im.height >= 256 else im.height
            fh.write(struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(data), offset))
            offset += len(data)
        for data in payloads:
            fh.write(data)


sizes = [16, 24, 32, 48, 64, 128, 256]
images = [make(s) for s in sizes]
write_ico(ROOT / "assets" / "icon.ico", images)
images[-1].save(ROOT / "docs" / "icon.png")
make(64).resize((128, 128), Image.LANCZOS).save(ROOT / "docs" / "icon64.png")
make(32).resize((128, 128), Image.LANCZOS).save(ROOT / "docs" / "icon32.png")
print("ok")
