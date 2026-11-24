"""Regenerate Nudge's icons in src/nudge/assets (needs Pillow: `uv run --with pillow python tools/make_icons.py`).

The mark is an open ring with a dot in its gap: a reminder that's "almost due", with a dot like a
notification badge. It sits on a dark, softly lit tile to match the desktop app. The ring's colour
changes with the state: blue for normal, amber for still waiting, red for urgent and green for done.
"""

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parents[1] / "src" / "nudge" / "assets"
S = 1024  # draw big, downsample for smooth edges
MARGIN = 56  # transparent border around the tile
RADIUS = 236  # tile corner radius (Windows 11 / macOS-like squircle feel)

# Ring geometry (angles in degrees, clockwise from 12 o'clock).
CENTRE = S // 2
RING_R = 300
RING_W = 112
GAP_FROM, GAP_TO = 8, 92  # the opening, top-right
DOT_ANGLE = 50
DOT_R = 76

VARIANTS = {
    # name: (ring start colour, ring end colour, dot colour)
    "nudge": ((108, 132, 255), (120, 214, 255), (245, 247, 255)),
    "nudge-nag": ((255, 160, 70), (255, 214, 120), (255, 246, 232)),
    "nudge-urgent": ((255, 77, 98), (255, 140, 120), (255, 240, 240)),
    "nudge-done": ((52, 199, 120), (140, 236, 170), (238, 255, 244)),
}


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b, strict=True))


def tile() -> Image.Image:
    """Dark rounded tile with a gentle top-to-bottom gradient and a faint top highlight."""
    grad = Image.new("RGBA", (S, S))
    draw = ImageDraw.Draw(grad)
    top, bottom = (44, 44, 50), (16, 16, 19)
    for y in range(S):
        draw.line([(0, y), (S, y)], fill=(*lerp(top, bottom, y / (S - 1)), 255))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((MARGIN, MARGIN, S - MARGIN, S - MARGIN), RADIUS, fill=255)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)

    # 1px-ish inner rim: brighter at the top, fading out, like light on glass
    rim = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(rim)
    d.rounded_rectangle((MARGIN, MARGIN, S - MARGIN, S - MARGIN), RADIUS, outline=255, width=6)
    fade = Image.new("L", (S, S), 0)
    fd = ImageDraw.Draw(fade)
    for y in range(S):
        fd.line([(0, y), (S, y)], fill=max(0, int(70 * (1 - y / (S * 0.75)))))
    rim = ImageChops.multiply(rim, fade)
    out = Image.alpha_composite(out, Image.merge("RGBA", (*[Image.new("L", (S, S), 255)] * 3, rim)))
    return out


def conic(start, end) -> Image.Image:
    """A colour sweep around the centre, start colour at the ring's beginning and end colour at its end."""
    img = Image.new("RGBA", (S, S))
    d = ImageDraw.Draw(img)
    sweep = 360 - (GAP_TO - GAP_FROM)
    for i in range(360):
        # i is degrees clockwise from 12 o'clock; the ring starts just after the gap
        t = ((i - GAP_TO) % 360) / sweep
        if t > 1:  # inside the gap (only the round caps reach here): match the nearer end
            t = 0.0 if (i - GAP_FROM) % 360 > (GAP_TO - GAP_FROM) / 2 else 1.0
        colour = lerp(start, end, t)
        # Pillow measures angles clockwise from 3 o'clock
        d.pieslice((0, 0, S, S), i - 90, i - 89 + 0.6, fill=(*colour, 255))
    return img


def at(angle: float, r: float) -> tuple[float, float]:
    from math import cos, radians, sin

    a = radians(angle - 90)
    return CENTRE + r * cos(a), CENTRE + r * sin(a)


def ring_mask() -> Image.Image:
    m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(m)
    box = (CENTRE - RING_R, CENTRE - RING_R, CENTRE + RING_R, CENTRE + RING_R)
    d.arc(box, GAP_TO - 90, GAP_FROM - 90 + 360, fill=255, width=RING_W)
    # round caps
    for angle in (GAP_TO, GAP_FROM):
        x, y = at(angle, RING_R - RING_W / 2)
        d.ellipse((x - RING_W / 2, y - RING_W / 2, x + RING_W / 2, y + RING_W / 2), fill=255)
    return m


def dot_mask() -> Image.Image:
    m = Image.new("L", (S, S), 0)
    x, y = at(DOT_ANGLE, RING_R - RING_W / 2)
    ImageDraw.Draw(m).ellipse((x - DOT_R, y - DOT_R, x + DOT_R, y + DOT_R), fill=255)
    return m


def make(name: str, start, end, dot) -> Image.Image:
    base = tile()
    ring = ring_mask()
    colours = conic(start, end)

    # soft glow in the ring's colour
    glow = Image.new("RGBA", (S, S), (*lerp(start, end, 0.5), 0))
    glow.putalpha(
        ImageChops.lighter(ring, dot_mask()).filter(ImageFilter.GaussianBlur(40)).point(lambda v: v * 0.45)
    )
    base = Image.alpha_composite(base, glow)

    coloured_ring = colours.copy()
    coloured_ring.putalpha(ring)
    base = Image.alpha_composite(base, coloured_ring)

    dot_layer = Image.new("RGBA", (S, S), (*dot, 0))
    dot_layer.putalpha(dot_mask())
    return Image.alpha_composite(base, dot_layer)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (start, end, dot) in VARIANTS.items():
        big = make(name, start, end, dot)
        small = big.resize((256, 256), Image.LANCZOS)
        small.save(OUT / f"{name}.png")
        if name == "nudge":
            # Each .ico size is downsampled from the big drawing, so small sizes stay crisp.
            sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
            frames = [big.resize((n, n), Image.LANCZOS) for n in sizes]
            frames[-1].save(OUT / "nudge.ico", sizes=[(n, n) for n in sizes], append_images=frames[:-1])
            big.resize((512, 512), Image.LANCZOS).save(OUT / "nudge-512.png")
    print("wrote", sorted(p.name for p in OUT.iterdir() if p.is_file()))


if __name__ == "__main__":
    main()
