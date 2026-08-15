#!/usr/bin/env python3
"""Build transparent monochrome splash assets without altering source logos."""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("saaf_holdings_group.png", "saaf_ventures.png", "mantis_darpa.png")


def source_for(name: str) -> Path:
    for directory in (ROOT / "assets" / "branding", ROOT / "assests" / "branding"):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(name)


def process(source: Path, destination: Path, target_long_edge: int = 1500) -> None:
    original = Image.open(source).convert("RGB")
    # Some supplied exports include a one-pixel dark canvas outline. It is not
    # part of the logo and would otherwise become an opaque rectangle edge.
    if original.width > 8 and original.height > 8:
        original = original.crop((2, 2, original.width - 2, original.height - 2))
    scale = max(1, min(8, round(target_long_edge / max(original.size))))
    if scale > 1:
        original = original.resize(
            (original.width * scale, original.height * scale), Image.Resampling.LANCZOS)

    # Distance from white becomes alpha. The 8-level dead zone removes JPEG/
    # PNG near-white matte noise; the 170-level soft ramp retains antialiased
    # edges and light-gray globe linework rather than making a hard cutout.
    r, g, b = original.split()
    minimum = ImageChops.darker(ImageChops.darker(r, g), b)
    alpha = minimum.point(lambda value: max(0, min(255, round((247 - value) * 255 / 170))))

    bbox = alpha.getbbox()
    if bbox is None:
        raise ValueError(f"no non-white artwork detected in {source}")
    pad = max(8, round(max(original.size) * 0.025))
    left = max(0, bbox[0] - pad); top = max(0, bbox[1] - pad)
    right = min(original.width, bbox[2] + pad); bottom = min(original.height, bbox[3] + pad)
    alpha = alpha.crop((left, top, right, bottom))

    # A uniform cool silver avoids retaining source colors while the alpha
    # channel carries every edge and interior line of the authoritative mark.
    result = Image.new("RGBA", alpha.size, (228, 234, 237, 0))
    result.putalpha(alpha)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result.save(destination, format="PNG", optimize=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "assets" / "branding" / "processed")
    args = parser.parse_args()
    for name in NAMES:
        source = source_for(name)
        destination = args.output / name
        process(source, destination)
        print(f"{source.relative_to(ROOT)} -> {destination.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
