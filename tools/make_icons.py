"""Regenerate the notification icons in src/nudge/assets (needs Pillow: `uv run --with pillow`)."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parents[1] / "src" / "nudge" / "assets"
S = 1024  # draw big, downsample for smooth edges

VARIANTS = {
    # name: (top colour, bottom colour)
    "nudge": ((99, 102, 241), (14, 165, 233)),  # indigo -> sky: normal reminders
    "nudge-nag": ((251, 146, 60), (234, 88, 12)),  # amber: still waiting / overdue
    "nudge-urgent": ((244, 63, 94), (190, 18, 60)),  # rose: urgent
    "nudge-done": ((34, 197, 94), (21, 128, 61)),  # green: app icon for "done" toasts
}


def gradient(top, bottom):
    img = Image.new("RGB", (S, S))
    px = img.load()
    for y in range(S):
        t = y / (S - 1)
        c = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom, strict=True))
        for x in range(S):
            px[x, y] = c
    return img


def bell(draw: ImageDraw.ImageDraw, fill=(255, 255, 255, 255)):
    cx = S // 2
    # dome
    draw.ellipse((cx - 250, 210, cx + 250, 690), fill=fill)
    # body flaring out to the rim
    draw.polygon([(cx - 250, 450), (cx + 250, 450), (cx + 330, 720), (cx - 330, 720)], fill=fill)
    # rim
    draw.rounded_rectangle((cx - 360, 690, cx + 360, 770), radius=40, fill=fill)
    # clapper
    draw.ellipse((cx - 85, 780, cx + 85, 900), fill=fill)
    # top knob
    draw.ellipse((cx - 55, 160, cx + 55, 270), fill=fill)


def make(name, colours):
    base = gradient(*colours).convert("RGBA")
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, S - 1, S - 1), radius=230, fill=255)
    tile = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    tile.paste(base, (0, 0), mask)

    # soft shadow under the bell, then the bell, both shrunk a little to leave breathing room
    def layer(fill):
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        bell(ImageDraw.Draw(img), fill=fill)
        inner = img.resize((int(S * 0.74), int(S * 0.74)), Image.LANCZOS)
        out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        off = (S - inner.width) // 2
        out.paste(inner, (off, off + 10), inner)
        return out

    shadow = layer((0, 0, 0, 80))
    shadow = shadow.transform(shadow.size, Image.AFFINE, (1, 0, 0, 0, 1, -22)).filter(
        ImageFilter.GaussianBlur(26)
    )
    tile = Image.alpha_composite(tile, shadow)
    tile = Image.alpha_composite(tile, layer((255, 255, 255, 255)))

    small = tile.resize((256, 256), Image.LANCZOS)
    small.save(OUT / f"{name}.png")
    if name == "nudge":
        small.save(
            OUT / "nudge.ico",
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for n, c in VARIANTS.items():
        make(n, c)
    print("wrote", sorted(p.name for p in OUT.iterdir()))
