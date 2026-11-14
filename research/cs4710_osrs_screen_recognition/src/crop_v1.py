"""Apply the frozen v1 OSRS game-canvas crop to one PNG image."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError


EXPECTED_SOURCE_WIDTH = 2560
EXPECTED_SOURCE_HEIGHT = 1440
CROP_LEFT = 0
CROP_TOP = 23
CROP_RIGHT = 2287
CROP_BOTTOM = 1392
EXPECTED_OUTPUT_WIDTH = 2287
EXPECTED_OUTPUT_HEIGHT = 1369
CROP_POLICY = "fixed_game_canvas_v1"

CROP_BOX = (CROP_LEFT, CROP_TOP, CROP_RIGHT, CROP_BOTTOM)
EXPECTED_SOURCE_SIZE = (EXPECTED_SOURCE_WIDTH, EXPECTED_SOURCE_HEIGHT)
EXPECTED_OUTPUT_SIZE = (EXPECTED_OUTPUT_WIDTH, EXPECTED_OUTPUT_HEIGHT)
SUPPORTED_MODES = {"RGB", "RGBA"}


class CropError(RuntimeError):
    """Raised when an image cannot be safely cropped under the v1 policy."""


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _validate_paths(source: Path, output: Path, *, overwrite: bool) -> None:
    if _resolved(source) == _resolved(output):
        raise CropError("source and output paths must be different")
    if source.suffix.lower() != ".png":
        raise CropError("source path must have a .png extension")
    if output.suffix.lower() != ".png":
        raise CropError("output path must have a .png extension")
    if not source.exists():
        raise CropError(f"source does not exist: {source}")
    if not source.is_file():
        raise CropError(f"source is not a regular file: {source}")
    if not output.parent.is_dir():
        raise CropError(f"output directory does not exist: {output.parent}")
    if output.exists() and not overwrite:
        raise CropError(f"output already exists (use --overwrite to replace it): {output}")


def _load_and_crop(source: Path) -> tuple[Image.Image, str]:
    try:
        with Image.open(source) as image:
            image.load()
            source_format = image.format
            source_size = image.size
            source_mode = image.mode

            if source_format != "PNG":
                raise CropError(f"decoded image format must be PNG, got {source_format or 'unknown'}")
            if source_size != EXPECTED_SOURCE_SIZE:
                raise CropError(
                    "source dimensions must be "
                    f"{EXPECTED_SOURCE_WIDTH}x{EXPECTED_SOURCE_HEIGHT}, "
                    f"got {source_size[0]}x{source_size[1]}"
                )
            if source_mode not in SUPPORTED_MODES:
                raise CropError(
                    f"source mode must be RGB or RGBA, got {source_mode}"
                )

            cropped = image.crop(CROP_BOX)
            # Do not carry source PNG text chunks or unrelated metadata into a
            # reviewed image. Pixel mode and values remain unchanged.
            cropped.info.clear()
    except CropError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise CropError("source could not be decoded as a valid PNG") from exc

    if cropped.size != EXPECTED_OUTPUT_SIZE:
        cropped.close()
        raise CropError(
            "internal crop geometry error: expected "
            f"{EXPECTED_OUTPUT_WIDTH}x{EXPECTED_OUTPUT_HEIGHT}, "
            f"got {cropped.size[0]}x{cropped.size[1]}"
        )
    return cropped, source_mode


def _verify_saved_png(path: Path, expected_mode: str) -> None:
    try:
        with Image.open(path) as image:
            image.load()
            if image.format != "PNG":
                raise CropError("saved output did not decode as PNG")
            if image.size != EXPECTED_OUTPUT_SIZE:
                raise CropError(
                    "saved output dimensions are not "
                    f"{EXPECTED_OUTPUT_WIDTH}x{EXPECTED_OUTPUT_HEIGHT}"
                )
            if image.mode != expected_mode:
                raise CropError(
                    f"saved output mode must be {expected_mode}, got {image.mode}"
                )
    except CropError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise CropError("saved output could not be verified as a valid PNG") from exc


def _install_output(temp_path: Path, output: Path, *, overwrite: bool) -> None:
    try:
        if overwrite:
            os.replace(temp_path, output)
        else:
            os.link(temp_path, output)
            temp_path.unlink()
    except FileExistsError as exc:
        raise CropError(f"output already exists: {output}") from exc
    except OSError as exc:
        raise CropError(f"could not write output: {output}") from exc


def crop_image(
    source_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Validate and crop one source PNG, returning a safe operation summary."""

    source = Path(source_path)
    output = Path(output_path)
    _validate_paths(source, output, overwrite=overwrite)

    cropped, source_mode = _load_and_crop(source)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp.png",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)

        try:
            cropped.save(temp_path, format="PNG")
        except (OSError, ValueError) as exc:
            raise CropError("cropped output could not be encoded as PNG") from exc
        finally:
            cropped.close()

        _verify_saved_png(temp_path, source_mode)
        _install_output(temp_path, output, overwrite=overwrite)
        temp_path = None
    finally:
        cropped.close()
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    return {
        "source_path": str(source),
        "output_path": str(output),
        "source_dimensions": [EXPECTED_SOURCE_WIDTH, EXPECTED_SOURCE_HEIGHT],
        "source_mode": source_mode,
        "crop_policy": CROP_POLICY,
        "crop_coordinates": list(CROP_BOX),
        "output_dimensions": [EXPECTED_OUTPUT_WIDTH, EXPECTED_OUTPUT_HEIGHT],
        "output_mode": source_mode,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply the frozen v1 OSRS game-canvas crop to one PNG."
    )
    parser.add_argument("source_png", type=Path)
    parser.add_argument("output_png", type=Path)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace an existing output only after the new PNG is verified",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        summary = crop_image(
            args.source_png,
            args.output_png,
            overwrite=args.overwrite,
        )
    except CropError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
