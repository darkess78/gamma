from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from PIL.PngImagePlugin import PngInfo


MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "crop_v1.py"
SPEC = importlib.util.spec_from_file_location("crop_v1", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
crop_v1 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(crop_v1)


class CropV1Test(unittest.TestCase):
    def test_constants_and_geometry(self) -> None:
        self.assertEqual(crop_v1.EXPECTED_SOURCE_WIDTH, 2560)
        self.assertEqual(crop_v1.EXPECTED_SOURCE_HEIGHT, 1440)
        self.assertEqual(crop_v1.CROP_BOX, (0, 23, 2287, 1392))
        self.assertEqual(crop_v1.EXPECTED_OUTPUT_WIDTH, 2287)
        self.assertEqual(crop_v1.EXPECTED_OUTPUT_HEIGHT, 1369)
        self.assertEqual(crop_v1.CROP_POLICY, "fixed_game_canvas_v1")
        self.assertEqual(
            crop_v1.CROP_RIGHT - crop_v1.CROP_LEFT,
            crop_v1.EXPECTED_OUTPUT_WIDTH,
        )
        self.assertEqual(
            crop_v1.CROP_BOTTOM - crop_v1.CROP_TOP,
            crop_v1.EXPECTED_OUTPUT_HEIGHT,
        )

    def test_successful_rgb_crop_preserves_mode_and_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.png"
            output = root / "output.png"
            self._save_source(source, "RGB", (10, 20, 30))

            summary = crop_v1.crop_image(source, output)

            with Image.open(output) as image:
                image.load()
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.mode, "RGB")
                self.assertEqual(image.size, (2287, 1369))
            self.assertEqual(summary["output_dimensions"], [2287, 1369])
            self.assertEqual(summary["output_mode"], "RGB")

    def test_successful_rgba_crop_preserves_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.PNG"
            output = root / "output.PNG"
            self._save_source(source, "RGBA", (10, 20, 30, 40))

            crop_v1.crop_image(source, output)

            with Image.open(output) as image:
                image.load()
                self.assertEqual(image.mode, "RGBA")
                self.assertEqual(image.size, (2287, 1369))

    def test_crop_boundaries_are_exact_and_excluded_regions_are_absent(self) -> None:
        retained = (1, 2, 3)
        title = (201, 1, 1)
        sidebar = (1, 201, 1)
        taskbar = (1, 1, 201)
        top_edge = (11, 12, 13)
        right_edge = (21, 22, 23)
        bottom_edge = (31, 32, 33)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.png"
            output = root / "output.png"
            image = Image.new("RGB", (2560, 1440), retained)
            image.paste(title, (0, 0, 2560, 23))
            image.paste(sidebar, (2287, 23, 2560, 1392))
            image.paste(taskbar, (0, 1392, 2560, 1440))
            image.putpixel((0, 23), top_edge)
            image.putpixel((2286, 23), right_edge)
            image.putpixel((0, 1391), bottom_edge)
            image.save(source, format="PNG")
            image.close()

            crop_v1.crop_image(source, output)

            with Image.open(output) as cropped:
                cropped.load()
                self.assertEqual(cropped.getpixel((0, 0)), top_edge)
                self.assertEqual(cropped.getpixel((2286, 0)), right_edge)
                self.assertEqual(cropped.getpixel((0, 1368)), bottom_edge)
                colors = {color for _, color in cropped.getcolors(maxcolors=10) or []}
                self.assertNotIn(title, colors)
                self.assertNotIn(sidebar, colors)
                self.assertNotIn(taskbar, colors)

    def test_crop_does_not_resize_or_interpolate(self) -> None:
        points = {
            (0, 23): (255, 0, 0),
            (1, 23): (0, 255, 0),
            (1000, 700): (0, 0, 255),
            (2286, 1391): (255, 255, 0),
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.png"
            output = root / "output.png"
            image = Image.new("RGB", (2560, 1440), (0, 0, 0))
            for coordinate, color in points.items():
                image.putpixel(coordinate, color)
            image.save(source, format="PNG")
            image.close()

            crop_v1.crop_image(source, output)

            with Image.open(output) as cropped:
                cropped.load()
                for (x, y), color in points.items():
                    self.assertEqual(cropped.getpixel((x, y - 23)), color)
                self.assertEqual(
                    {color for _, color in cropped.getcolors(maxcolors=10) or []},
                    {(0, 0, 0), *points.values()},
                )

    def test_wrong_source_width_is_rejected(self) -> None:
        self._assert_invalid_size_leaves_no_output((2559, 1440))

    def test_wrong_source_height_is_rejected(self) -> None:
        self._assert_invalid_size_leaves_no_output((2560, 1439))

    def test_unsupported_color_mode_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.png"
            output = root / "output.png"
            Image.new("L", (2560, 1440), 1).save(source, format="PNG")

            with self.assertRaisesRegex(crop_v1.CropError, "mode must be RGB or RGBA"):
                crop_v1.crop_image(source, output)

            self.assertFalse(output.exists())

    def test_non_png_content_with_png_suffix_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.png"
            output = root / "output.png"
            Image.new("RGB", (2560, 1440)).save(source, format="JPEG")

            with self.assertRaisesRegex(crop_v1.CropError, "format must be PNG"):
                crop_v1.crop_image(source, output)

            self.assertFalse(output.exists())

    def test_missing_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(crop_v1.CropError, "source does not exist"):
                crop_v1.crop_image(root / "missing.png", root / "output.png")

    def test_source_and_destination_resolving_to_same_path_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.png"
            self._save_source(source, "RGB", (1, 2, 3))

            with self.assertRaisesRegex(crop_v1.CropError, "must be different"):
                crop_v1.crop_image(source, source.parent / "." / source.name)

    def test_existing_output_is_unchanged_when_overwrite_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.png"
            output = root / "output.png"
            original_bytes = b"existing output sentinel\n"
            self._save_source(source, "RGB", (1, 2, 3))
            output.write_bytes(original_bytes)

            with self.assertRaisesRegex(crop_v1.CropError, "output already exists"):
                crop_v1.crop_image(source, output)

            self.assertEqual(output.read_bytes(), original_bytes)

    def test_successful_overwrite_replaces_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.png"
            output = root / "output.png"
            self._save_source(source, "RGB", (7, 8, 9))
            output.write_bytes(b"existing output sentinel\n")

            crop_v1.crop_image(source, output, overwrite=True)

            with Image.open(output) as image:
                image.load()
                self.assertEqual(image.size, (2287, 1369))
                self.assertEqual(image.getpixel((0, 0)), (7, 8, 9))

    def test_source_png_text_metadata_is_not_copied(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.png"
            output = root / "output.png"
            metadata = PngInfo()
            metadata.add_text("SyntheticField", "harmless synthetic value")
            image = Image.new("RGB", (2560, 1440), (7, 8, 9))
            image.save(source, format="PNG", pnginfo=metadata)
            image.close()

            crop_v1.crop_image(source, output)

            with Image.open(output) as cropped:
                cropped.load()
                self.assertNotIn("SyntheticField", cropped.info)
                self.assertEqual(cropped.info, {})

    def test_failed_operation_leaves_existing_output_unchanged_with_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.png"
            output = root / "output.png"
            original_bytes = b"existing output sentinel\n"
            Image.new("RGB", (2560, 1439)).save(source, format="PNG")
            output.write_bytes(original_bytes)

            with self.assertRaises(crop_v1.CropError):
                crop_v1.crop_image(source, output, overwrite=True)

            self.assertEqual(output.read_bytes(), original_bytes)
            self.assertEqual(sorted(root.iterdir()), [output, source])

    def test_import_has_no_cli_or_filesystem_side_effects(self) -> None:
        code = (
            "import importlib.util; "
            f"p={str(MODULE_PATH)!r}; "
            "s=importlib.util.spec_from_file_location('crop_v1_import_check', p); "
            "m=importlib.util.module_from_spec(s); "
            "s.loader.exec_module(m)"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=temp_dir,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")
            self.assertEqual(list(Path(temp_dir).iterdir()), [])

    def _assert_invalid_size_leaves_no_output(self, size: tuple[int, int]) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.png"
            output = root / "output.png"
            Image.new("RGB", size).save(source, format="PNG")

            with self.assertRaisesRegex(crop_v1.CropError, "source dimensions must be"):
                crop_v1.crop_image(source, output)

            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(".*.tmp.png")), [])

    @staticmethod
    def _save_source(path: Path, mode: str, color: tuple[int, ...]) -> None:
        image = Image.new(mode, (2560, 1440), color)
        image.save(path, format="PNG")
        image.close()


if __name__ == "__main__":
    unittest.main()
