import unittest

from PIL import ImageFont

from agent_dashboard.ui_fonts import font_catalogue, resolve_font


def _default_font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


class UIFontsTests(unittest.TestCase):
    def test_catalogue_contains_only_safe_opaque_ids_and_names(self):
        catalogue = font_catalogue()
        self.assertEqual(catalogue[:2], [
            {"id": "ui", "name": "默认 UI 字体"},
            {"id": "mono", "name": "等宽字体"},
        ])
        self.assertEqual(catalogue, font_catalogue())
        for item in catalogue:
            self.assertEqual(set(item), {"id", "name"})
            self.assertRegex(item["id"], r"\A[a-z0-9_-]{1,64}\Z")
            self.assertTrue(item["name"])
            self.assertNotRegex(item["name"], r"[\\/:]")
            self.assertNotIn(".ttf", item["name"].casefold())
            self.assertNotIn(".ttc", item["name"].casefold())

    def test_no_style_or_family_override_preserves_default_font(self):
        original = _default_font(16)
        resolved, synthetic_bold, synthetic_italic = resolve_font(original, 16)
        self.assertIs(resolved, original)
        self.assertFalse(synthetic_bold)
        self.assertFalse(synthetic_italic)

    def test_size_only_override_keeps_family_and_style_without_synthetic_flags(self):
        original = _default_font(16)
        resolved, synthetic_bold, synthetic_italic = resolve_font(original, 27)
        self.assertEqual(getattr(resolved, "size", 27), 27)
        self.assertFalse(synthetic_bold)
        self.assertFalse(synthetic_italic)
        getname = getattr(original, "getname", None)
        resolved_getname = getattr(resolved, "getname", None)
        if callable(getname) and callable(resolved_getname):
            self.assertEqual(resolved.getname(), original.getname())

    def test_unknown_family_falls_back_and_preserves_requested_size_and_style(self):
        original = _default_font(16)
        unknown_id = "f-" + "0" * 24
        resolved, synthetic_bold, synthetic_italic = resolve_font(
            original, 29, font_family=unknown_id, bold=True, italic=True)
        self.assertEqual(getattr(resolved, "size", 29), 29)
        self.assertTrue(synthetic_bold)
        self.assertTrue(synthetic_italic)
        if callable(getattr(original, "getname", None)) and callable(getattr(resolved, "getname", None)):
            self.assertEqual(resolved.getname()[0], original.getname()[0])

    def test_enumerated_family_can_be_resolved_without_exposing_its_path(self):
        catalogue = font_catalogue()
        installed = next((item for item in catalogue[2:] if item["id"].startswith("f-")), None)
        if installed is None:
            self.skipTest("No installed Windows font families were enumerated")
        original = _default_font(16)
        resolved, synthetic_bold, synthetic_italic = resolve_font(
            original, 23, font_family=installed["id"], bold=True, italic=True)
        self.assertEqual(getattr(resolved, "size", 23), 23)
        self.assertEqual(resolved.getname()[0], installed["name"])
        self.assertIs(type(synthetic_bold), bool)
        self.assertIs(type(synthetic_italic), bool)


if __name__ == "__main__":
    unittest.main()
