"""Step 4 — a procedural thumbnail, seeded by the act itself.

The seed is the act's own reference ("SR 231.1"), so the image is a fingerprint of
that act: the same act always draws the same picture, a different act never draws
the same one, and nothing is stored beyond the seed. Greyscale throughout, with a
single Swiss-red accent always drawn last, on top of everything else.

Requires Pillow. It is the only stage that does — census, fetch and parse are
stdlib-only, so the pipeline still runs without it.

Usage:
    python src/thumbnail.py SR [SR ...]          # one per slate, into assets/
    python src/thumbnail.py --seed "231.1 Art. 13a" --out /tmp/art.png
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import random
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from parse import slate_path  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
CENSUS_PATH = ROOT / "data" / "census.json"

# 512 is plenty for a thumbnail and a third of the weight of 1024. The seed decides
# the composition in relative units, so changing this re-renders the same picture
# at a different resolution rather than drawing a different one.
SIZE = 512
SUPER = 2  # drawn at 2x and reduced, which is the cheapest anti-aliasing there is

PAPER = (242, 240, 236)
INK = (26, 26, 26)
SWISS_RED = (218, 41, 28)  # Pantone 485, the red of the federal flag


def grey(rng: random.Random, low: int = 40, high: int = 205) -> tuple[int, int, int]:
    """A neutral with a trace of warmth, so the greys sit on paper rather than glass."""
    value = rng.randint(low, high)
    return (value, value, max(0, value - 2))


def strata(draw: ImageDraw.ImageDraw, rng: random.Random, size: int) -> None:
    """Horizontal bands of uneven height — sediment, or a stack of provisions."""
    y = 0
    while y < size:
        height = rng.randint(size // 40, size // 7)
        if rng.random() < 0.72:
            draw.rectangle([0, y, size, y + height], fill=grey(rng))
            # Some bands break into vertical ticks, like an index down the margin.
            if rng.random() < 0.35:
                x = rng.randint(0, size // 3)
                while x < size:
                    width = rng.randint(size // 160, size // 40)
                    if rng.random() < 0.5:
                        draw.rectangle([x, y, x + width, y + height], fill=grey(rng, 20, 90))
                    x += width + rng.randint(size // 200, size // 30)
        y += height


def rings(draw: ImageDraw.ImageDraw, rng: random.Random, size: int) -> None:
    """Concentric arcs with seeded gaps — a seal pressed into the page."""
    cx = rng.randint(size // 3, size * 2 // 3)
    cy = rng.randint(size // 3, size * 2 // 3)
    radius = size // 12
    while radius < size:
        width = rng.randint(size // 300, size // 60)
        start = rng.randint(0, 359)
        extent = rng.choice([90, 140, 200, 260, 300, 360])
        draw.arc(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            start, start + extent, fill=grey(rng), width=width,
        )
        radius += rng.randint(size // 40, size // 12)


def grid(draw: ImageDraw.ImageDraw, rng: random.Random, size: int) -> None:
    """A modular grid at seeded densities — the shape of a classification."""
    columns = rng.choice([6, 8, 9, 12])
    cell = size / columns
    for row in range(columns):
        for column in range(columns):
            chance = rng.random()
            box = [column * cell, row * cell, (column + 1) * cell, (row + 1) * cell]
            if chance < 0.30:
                draw.rectangle(box, fill=grey(rng))
            elif chance < 0.44:
                # hatched cell
                step = max(3, int(cell / rng.randint(3, 7)))
                offset = 0
                while offset < cell * 2:
                    draw.line(
                        [box[0] + offset, box[1], box[0], box[1] + offset],
                        fill=grey(rng, 60, 150), width=max(1, int(cell / 40)),
                    )
                    offset += step
            elif chance < 0.52:
                inset = cell * 0.18
                draw.rectangle(
                    [box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset],
                    outline=grey(rng, 30, 120), width=max(2, int(cell / 24)),
                )


def hatch(draw: ImageDraw.ImageDraw, rng: random.Random, size: int) -> None:
    """A diagonal field, cut by a few clear channels."""
    step = rng.randint(size // 90, size // 26)
    angle = rng.choice([1, -1])
    offset = -size
    while offset < size * 2:
        width = rng.randint(1, max(2, size // 150))
        top = (offset, 0)
        bottom = (offset + angle * size, size)
        draw.line([top, bottom], fill=grey(rng, 70, 190), width=width)
        offset += step
    for _ in range(rng.randint(1, 3)):
        band = rng.randint(size // 12, size // 4)
        y = rng.randint(0, size - band)
        draw.rectangle([0, y, size, y + band], fill=PAPER)


FIELDS = (strata, rings, grid, hatch)


def accent(
    draw: ImageDraw.ImageDraw, rng: random.Random, size: int, block: list[int]
) -> None:
    """The red mark, drawn last and placed against the block, never clipped.

    It is one mark, not a scattering: a thumbnail has room for a single accent,
    and a red shape cut off by the canvas edge reads as a mistake rather than
    as a decision.
    """
    left, top, right, bottom = block
    kind = rng.choice(("rule", "bar", "dot", "arc", "underline"))

    if kind == "rule":
        # A full-bleed hairline is the one shape allowed to touch the edges,
        # because a rule that stops short looks broken rather than deliberate.
        thickness = max(2, size // 150)
        if rng.random() < 0.5:
            y = rng.randint(top, bottom)
            draw.rectangle([0, y, size, y + thickness], fill=SWISS_RED)
        else:
            x = rng.randint(left, right)
            draw.rectangle([x, 0, x + thickness, size], fill=SWISS_RED)

    elif kind == "bar":
        width = rng.randint(size // 22, size // 12)
        height = rng.randint((bottom - top) // 3, int((bottom - top) * 0.8))
        x = rng.randint(left, max(left, right - width))
        y = top + (bottom - top - height) // 2
        draw.rectangle([x, y, x + width, y + height], fill=SWISS_RED)

    elif kind == "dot":
        radius = rng.randint(size // 20, size // 11)
        cx = rng.randint(left + radius, max(left + radius, right - radius))
        cy = rng.randint(top + radius, max(top + radius, bottom - radius))
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=SWISS_RED)

    elif kind == "arc":
        radius = rng.randint(size // 5, size // 3)
        cx = rng.randint(left + radius // 2, max(left + radius // 2, right - radius // 2))
        cy = rng.randint(top + radius // 2, max(top + radius // 2, bottom - radius // 2))
        start = rng.randint(0, 359)
        draw.arc(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            start, start + rng.choice([60, 90, 150]),
            fill=SWISS_RED, width=max(3, size // 60),
        )

    else:  # underline — a short heavy bar sitting under the block
        width = rng.randint((right - left) // 3, right - left)
        height = max(3, size // 70)
        x = rng.randint(left, max(left, right - width))
        y = min(size - height * 3, bottom + size // 30)
        draw.rectangle([x, y, x + width, y + height], fill=SWISS_RED)


def compose(seed: str, size: int = SIZE, grain: bool = True) -> Image.Image:
    """The whole picture, decided entirely by the seed."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(digest, "big"))

    canvas = size * SUPER
    image = Image.new("RGB", (canvas, canvas), PAPER)

    # The field is confined to one block on a margin grid rather than bleeding to
    # the edges. The empty paper around it is what makes the mark read at 96px.
    margin = int(canvas * rng.uniform(0.09, 0.16))
    width = int((canvas - 2 * margin) * rng.uniform(0.62, 1.0))
    height = int((canvas - 2 * margin) * rng.uniform(0.58, 1.0))
    left = margin + int((canvas - 2 * margin - width) * rng.choice((0.0, 0.5, 1.0)))
    top = margin + int((canvas - 2 * margin - height) * rng.choice((0.0, 0.5, 1.0)))
    block = [left, top, left + width, top + height]

    # Draw the field full-size, then show only the part inside the block.
    field = Image.new("RGB", (canvas, canvas), PAPER)
    rng.choice(FIELDS)(ImageDraw.Draw(field), rng, canvas)
    mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(mask).rectangle(block, fill=255)
    image = Image.composite(field, image, mask)

    accent(ImageDraw.Draw(image), rng, canvas, block)

    image = image.resize((size, size), Image.LANCZOS)
    if not grain:
        return image

    # Paper grain, so the flat fills do not read as a screenshot of a chart. It is
    # the most expensive thing in the file — noise is exactly what a palette PNG
    # cannot compress — so it roughly triples the weight: 3.5 KB becomes 10 KB.
    noise = Image.effect_noise((size, size), 5).convert("RGB")
    return ImageChops.overlay(image, noise.filter(ImageFilter.GaussianBlur(0.6)))


def save(image: Image.Image, path: pathlib.Path) -> None:
    """Write the PNG down to a 64-colour palette.

    The art is flat greys and one red, so 64 colours is more than it uses and the
    result is visually identical — but it is 32 KB instead of 505 KB. Across a
    corpus of 410 slates that is the difference between 13 MB in git and 200 MB.
    """
    image.quantize(colors=64, method=Image.Quantize.FASTOCTREE).save(path, optimize=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sr", nargs="*", help="SR numbers; default every emitted slate")
    parser.add_argument("--seed", help="draw an arbitrary seed instead of an act")
    parser.add_argument("--out", help="output path, with --seed")
    parser.add_argument("--size", type=int, default=SIZE)
    parser.add_argument(
        "--no-grain", action="store_true", help="drop the paper grain; ~3x lighter"
    )
    args = parser.parse_args()

    if args.seed:
        target = pathlib.Path(args.out or "thumbnail.png")
        save(compose(args.seed, args.size, not args.no_grain), target)
        print(f"  {args.seed!r} → {target}")
        return 0

    census = json.loads(CENSUS_PATH.read_text(encoding="utf-8"))
    by_sr = {a["sr"]: a for a in census["acts"]}
    wanted = args.sr or sorted(
        sr for sr in by_sr if not sr.startswith("0") and slate_path(sr).exists()
    )

    for sr in wanted:
        if sr not in by_sr:
            print(f"  FAIL {sr}: not in the census", file=sys.stderr)
            return 1
        assets = slate_path(sr) / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        save(compose(f"SR {sr}", args.size, not args.no_grain), assets / "cover.png")
        print(f"  {sr:<14} seed 'SR {sr}' → {(assets / 'cover.png').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
