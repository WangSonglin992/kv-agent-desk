from copy import deepcopy
import json
import unittest

from PIL import Image, ImageChops

from agent_dashboard.render import font
from agent_dashboard.ui_scene import SceneCanvas


class SceneCanvasTests(unittest.TestCase):
    @staticmethod
    def draw_sample(profile=None):
        image = Image.new("RGB", (240, 160), "#090F16")
        canvas = SceneCanvas(image, profile)
        with canvas.group("agent.codex", (10, 10, 230, 150)):
            canvas.rectangle((10, 10, 230, 150), fill="#101A25", element="panel")
            canvas.text((24, 26), "Synthetic value", font=font(18), fill="#78E7C2", element="plan_name")
            canvas.text((24, 90), "Static label", font=font(16), fill="#FFFFFF", element="task_label")
        return image, {node["id"]: node for node in canvas.manifest()}

    def test_element_translation_does_not_move_its_siblings_or_mutate_profile(self):
        profile = {"elements": {"agent.codex.plan_name": {"dx": 12, "dy": 7}}}
        before = deepcopy(profile)
        original, nodes = self.draw_sample()
        moved, changed = self.draw_sample(profile)
        first = nodes["agent.codex.plan_name"]["bbox"]
        self.assertEqual(changed["agent.codex.plan_name"]["bbox"],
                         [first[0] + 12, first[1] + 7, first[2] + 12, first[3] + 7])
        self.assertEqual(nodes["agent.codex.task_label"], changed["agent.codex.task_label"])
        self.assertIsNone(ImageChops.difference(original.crop((0, 85, 240, 160)), moved.crop((0, 85, 240, 160))).getbbox())
        self.assertEqual(profile, before)

    def test_group_and_child_offsets_compose_once(self):
        _, base = self.draw_sample()
        _, moved = self.draw_sample({"elements": {"agent.codex": {"dx": 5, "dy": 4},
                                                 "agent.codex.plan_name": {"dx": 7, "dy": 2}}})
        for identity, delta in (("agent.codex", (5, 4)), ("agent.codex.panel", (5, 4)),
                                ("agent.codex.plan_name", (12, 6)), ("agent.codex.task_label", (5, 4))):
            bounds = base[identity]["bbox"]
            dx, dy = delta
            self.assertEqual(moved[identity]["bbox"], [bounds[0] + dx, bounds[1] + dy, bounds[2] + dx, bounds[3] + dy])

    def test_individual_text_styles_change_glyphs_without_editing_content(self):
        source, defaults = self.draw_sample()
        styled, nodes = self.draw_sample({"elements": {"agent.codex.plan_name": {
            "font_size": 26, "color": "#FF4433", "bold": True, "italic": True}}})
        node = nodes["agent.codex.plan_name"]
        self.assertEqual(node["font_size"], 26)
        self.assertEqual(node["color"], "#FF4433")
        self.assertTrue(node["bold"])
        self.assertTrue(node["italic"])
        self.assertEqual(node["bbox"][:2], defaults["agent.codex.plan_name"]["bbox"][:2])
        self.assertIsNotNone(ImageChops.difference(source, styled).getbbox())
        encoded = json.dumps(nodes)
        self.assertNotIn("Synthetic value", encoded)
        self.assertNotIn("Static label", encoded)
        self.assertNotIn("font_path", encoded)

    def test_hidden_element_can_be_selected_from_catalogue_without_painting(self):
        image = Image.new("RGB", (240, 160), "#090F16")
        original = image.copy()
        canvas = SceneCanvas(image, prefix="agent.codex")
        canvas.text((20, 20), "Never exported", font=font(16), fill="#FFFFFF", element="stale_note", visible=False)
        self.assertIsNone(ImageChops.difference(original, image).getbbox())
        node = canvas.manifest()[0]
        self.assertEqual(node["id"], "agent.codex.stale_note")
        self.assertFalse(node["visible"])
        self.assertNotIn("Never exported", json.dumps(node))


if __name__ == "__main__":
    unittest.main()
