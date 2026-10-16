"""Named raster elements with data-free position and type metadata.

The renderer supplies every string and image. A profile can only translate
registered elements and resize text; it cannot supply drawing content.
"""

from contextlib import contextmanager
from copy import deepcopy
import math

from PIL import Image, ImageDraw, ImageFont

from .ui_elements import ELEMENT_DEFINITIONS


class FittedText(str):
    """Keep the source value for refitting after an element's font is resized."""

    def __new__(cls, value, raw, width):
        item = super().__new__(cls, value)
        item.raw = raw
        item.width = width
        return item


class SceneCanvas:
    def __init__(self, image, profile=None, *, draw=None, prefix="", capture=True):
        self.image = image
        self.draw = draw if draw is not None else ImageDraw.Draw(image)
        self.overrides = (profile or {}).get("elements", {})
        self.prefix = prefix
        self.capture = capture
        self._groups = []
        self._active = []
        self._nodes = {}

    def __getattr__(self, name):
        # Layout measurement retains the original layout's coordinates. Each
        # editable object has its own explicit position, independent of values.
        return getattr(self.draw, name)

    def _name(self, value):
        if value in ELEMENT_DEFINITIONS:
            return value
        prefix = self._groups[-1] if self._groups else self.prefix
        return f"{prefix}.{value}"

    def _offset(self):
        identities = self._groups + self._active[-1:]
        return tuple(sum(self.overrides.get(key, {}).get(axis, 0) for key in identities)
                     for axis in ("dx", "dy"))

    def _record(self, default_bbox, bbox, *, font=None, visible=True):
        if not self.capture or not self._active:
            return
        identity = self._active[-1]
        definition = ELEMENT_DEFINITIONS[identity]
        node = self._nodes.get(identity)
        if node is None:
            node = {"id": identity, **definition, "bbox": list(bbox),
                    "default_bbox": list(default_bbox), "visible": visible}
            self._nodes[identity] = node
        else:
            for key, bounds in (("bbox", bbox), ("default_bbox", default_bbox)):
                old = node[key]
                node[key] = [min(old[0], bounds[0]), min(old[1], bounds[1]),
                             max(old[2], bounds[2]), max(old[3], bounds[3])]
            node["visible"] |= visible
        if font is not None:
            node["default_font_size"] = font.size
            node["font_size"] = self.overrides.get(identity, {}).get("font_size", font.size)

    @contextmanager
    def group(self, identity, frame):
        definition = ELEMENT_DEFINITIONS[identity]
        self._groups.append(identity)
        dx, dy = self._offset()
        if self.capture:
            self._nodes[identity] = {"id": identity, **definition, "default_bbox": list(frame),
                                     "bbox": [frame[0] + dx, frame[1] + dy, frame[2] + dx, frame[3] + dy],
                                     "visible": True}
        try:
            yield self
        finally:
            self._groups.pop()

    @contextmanager
    def element(self, name):
        identity = self._name(name)
        if identity not in ELEMENT_DEFINITIONS:
            raise ValueError("unregistered drawing element")
        self._active.append(identity)
        try:
            yield self
        finally:
            self._active.pop()

    def text(self, xy, text, *, element=None, visible=True, **kwargs):
        if element is not None:
            with self.element(element):
                return self.text(xy, text, visible=visible, **kwargs)
        identity = self._active[-1] if self._active else ""
        style = self.overrides.get(identity, {})
        if not self.capture and not any(key in style for key in ("font_size", "font_family", "bold", "italic")):
            if "color" in style:
                kwargs["fill"] = style["color"]
            dx, dy = self._offset()
            if visible:
                self.draw.text((xy[0] + dx, xy[1] + dy), str(text), **kwargs)
            return
        default_font = kwargs.get("font")
        default_color = kwargs.get("fill")
        measure_keys = {"font", "anchor", "spacing", "align", "direction", "features", "language", "stroke_width", "embedded_color"}
        measured = {key: value for key, value in kwargs.items() if key in measure_keys}
        original = self.draw.textbbox(xy, str(text), **measured)
        synthetic_bold = synthetic_italic = False
        if default_font is not None and any(key in style for key in ("font_size", "font_family", "bold", "italic")):
            from .ui_fonts import resolve_font
            size = style.get("font_size", default_font.size)
            actual_font, synthetic_bold, synthetic_italic = resolve_font(
                default_font, size, font_family=style.get("font_family"),
                bold=style.get("bold"), italic=style.get("italic"))
            kwargs["font"] = actual_font
            measured["font"] = actual_font
            if isinstance(text, FittedText):
                # Custom text may grow beyond the preset's original column.
                # Its value remains supplied by the data module, while the
                # user chooses the space and position that it occupies.
                text = text.raw
            if synthetic_bold:
                kwargs["stroke_width"] = max(1, round(size / 32))
                kwargs["stroke_fill"] = style.get("color", default_color)
                measured["stroke_width"] = kwargs["stroke_width"]
            changed = self.draw.textbbox(xy, str(text), **measured)
            # Resizing keeps the visible top-left stationary; X/Y in the
            # editor consequently describe the actual glyph position.
            xy = (xy[0] + original[0] - changed[0], xy[1] + original[1] - changed[1])
        if "color" in style:
            kwargs["fill"] = style["color"]
        dx, dy = self._offset()
        target = (xy[0] + dx, xy[1] + dy)
        actual = self.draw.textbbox(target, str(text), **measured)
        bitmap = None
        if synthetic_italic and str(text):
            width, height = max(1, actual[2] - actual[0]), max(1, actual[3] - actual[1])
            padding = 4
            bitmap = Image.new("RGBA", (math.ceil(width) + padding * 2, math.ceil(height) + padding * 2))
            painter = ImageDraw.Draw(bitmap)
            painter.text((target[0] - actual[0] + padding, target[1] - actual[1] + padding), str(text), **kwargs)
            shear = 0.22
            bitmap = bitmap.transform((bitmap.width + math.ceil(bitmap.height * shear), bitmap.height),
                                      Image.Transform.AFFINE, (1, shear, -shear * bitmap.height, 0, 1, 0),
                                      resample=Image.Resampling.BICUBIC)
            bounds = bitmap.getbbox()
            if bounds:
                bitmap = bitmap.crop(bounds)
                actual = (actual[0], actual[1], actual[0] + bitmap.width, actual[1] + bitmap.height)
        if not str(text):
            actual = (target[0], target[1], target[0] + 16, target[1] + getattr(default_font, "size", 16))
        self._record(original, actual, font=default_font, visible=visible)
        if identity in self._nodes:
            node = self._nodes[identity]
            if isinstance(default_color, str):
                node.update(default_color=default_color, color=style.get("color", default_color))
            face = default_font.getname()[1].casefold() if hasattr(default_font, "getname") else ""
            default_bold = any(word in face for word in ("bold", "black", "heavy", "demi"))
            default_italic = any(word in face for word in ("italic", "oblique"))
            node.update(default_bold=default_bold, bold=style.get("bold", default_bold),
                        default_italic=default_italic, italic=style.get("italic", default_italic))
            if style.get("font_family"):
                node["font_family"] = style["font_family"]
        if visible:
            if bitmap is not None:
                self.image.paste(bitmap, (math.floor(actual[0]), math.floor(actual[1])), bitmap)
            else:
                self.draw.text(target, str(text), **kwargs)

    def _shape(self, method, xy, *, element=None, **kwargs):
        if element is not None:
            with self.element(element):
                return self._shape(method, xy, **kwargs)
        points = list(xy)
        if points and isinstance(points[0], (tuple, list)):
            flat = [value for point in points for value in point]
            nested = True
        else:
            flat, nested = points, False
        bounds = (min(flat[::2]), min(flat[1::2]), max(flat[::2]), max(flat[1::2]))
        dx, dy = self._offset()
        transformed = [value + (dx if index % 2 == 0 else dy) for index, value in enumerate(flat)]
        if nested:
            transformed = list(zip(transformed[::2], transformed[1::2]))
        self._record(bounds, (bounds[0] + dx, bounds[1] + dy, bounds[2] + dx, bounds[3] + dy))
        return getattr(self.draw, method)(transformed, **kwargs)

    def rectangle(self, xy, **kwargs):
        return self._shape("rectangle", xy, **kwargs)

    def rounded_rectangle(self, xy, **kwargs):
        return self._shape("rounded_rectangle", xy, **kwargs)

    def ellipse(self, xy, **kwargs):
        return self._shape("ellipse", xy, **kwargs)

    def line(self, xy, **kwargs):
        return self._shape("line", xy, **kwargs)

    def paste(self, image, xy, mask=None, *, element=None):
        if element is not None:
            with self.element(element):
                return self.paste(image, xy, mask)
        dx, dy = self._offset()
        bounds = (xy[0], xy[1], xy[0] + image.width, xy[1] + image.height)
        self._record(bounds, (bounds[0] + dx, bounds[1] + dy, bounds[2] + dx, bounds[3] + dy))
        self.image.paste(image, (round(xy[0] + dx), round(xy[1] + dy)), mask)

    def manifest(self):
        result = deepcopy(list(self._nodes.values()))
        for node in result:
            for key in ("bbox", "default_bbox"):
                node[key] = [math.floor(value) if index < 2 else math.ceil(value)
                             for index, value in enumerate(node[key])]
                node[key][2] = max(node[key][0] + 1, node[key][2])
                node[key][3] = max(node[key][1] + 1, node[key][3])
        return result
