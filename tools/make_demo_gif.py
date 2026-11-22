"""Assemble captured application frames into the README demo GIF."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageOps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frames", type=Path, help="Recording manifest JSON or folder with ordered PNG/BMP frames.")
    parser.add_argument("--durations", type=Path, help="JSON map from PNG filename to milliseconds.")
    parser.add_argument("--output", type=Path, default=Path("assets/demo.gif"))
    args = parser.parse_args()

    if args.frames.is_file():
        manifest = json.loads(args.frames.read_text(encoding="utf-8"))
        entries = manifest.get("frames")
        if not isinstance(entries, list) or not entries:
            parser.error(f"Manifest has no frames: {args.frames}")
        paths = [(args.frames.parent / item["path"], int(item["duration_ms"])) for item in entries]
        if any(delay <= 0 for _, delay in paths):
            parser.error("Frame durations must be positive milliseconds.")
    else:
        durations = json.loads(args.durations.read_text(encoding="utf-8")) if args.durations else {}
        files = sorted([*args.frames.glob("*.png"), *args.frames.glob("*.bmp")])
        paths = [(path, int(durations.get(path.name, 150))) for path in files]
    if not paths:
        parser.error(f"No PNG frames found in {args.frames}")
    sources, delays = [], []
    for path, delay in paths:
        with Image.open(path) as source:
            frame = source.convert("RGB")
            if frame.width > 1280:
                height = round(frame.height * 1280 / frame.width)
                frame = frame.resize((1280, height), Image.Resampling.LANCZOS)
            frame = ImageOps.pad(frame, (1280, 720), color="#ffffff", centering=(0.5, 0.5))
            sources.append(frame)
        delays.append(delay)

    sample_indices = sorted({0, len(sources) // 3, 2 * len(sources) // 3, len(sources) - 1})
    palette_source = Image.new("RGB", (1280, 720 * len(sample_indices)))
    for row, index in enumerate(sample_indices):
        palette_source.paste(sources[index], (0, row * 720))
    palette = palette_source.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    frames = [source.quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG) for source in sources]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        args.output, save_all=True, append_images=frames[1:], duration=delays,
        loop=0, optimize=True, disposal=2,
    )
    print(f"Wrote {args.output} from {len(frames)} frames ({sum(delays)} ms).")


if __name__ == "__main__":
    main()
