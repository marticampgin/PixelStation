"""Narrow Word form checkbox support; drawing shapes and ink are never inferred."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CHECK_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
W = "{" + WORD_NS + "}"
C = "{" + CHECK_NS + "}"


def _state(node: Any | None, namespace: str = W, default: bool = False) -> bool:
    if node is None:
        return default
    value = node.get(namespace + "val", "true")
    if value in {"1", "true", "on"}:
        return True
    if value in {"0", "false", "off"}:
        return False
    raise ValueError("Unsupported checkbox state")


def _paragraph(element: Any) -> Any | None:
    return next((parent for parent in element.iterancestors() if parent.tag == W + "p"), None)


def _label(element: Any, preferred: str | None, index: int) -> str:
    if preferred and preferred.strip():
        return preferred.strip()[:200]
    paragraph = _paragraph(element)
    text = "" if paragraph is None else "".join(node.text or "" for node in paragraph.iter(W + "t"))
    text = text.replace("☐", "").replace("☑", "").replace("☒", "").strip()
    return text[:200] or f"Checkbox {index + 1}"


def _glyph(checkbox: Any, checked: bool) -> tuple[str, str | None]:
    definition = checkbox.find(C + ("checkedState" if checked else "uncheckedState"))
    value = (definition.get(C + "val") if definition is not None else None) or ("2612" if checked else "2610")
    number = int(value, 16)
    if not 32 <= number <= 0x10FFFF or 0xD800 <= number <= 0xDFFF:
        raise ValueError("Unsupported checkbox symbol")
    return chr(number), definition.get(C + "font") if definition is not None else "MS Gothic"


@dataclass
class DocxCheckbox:
    location: str
    label: str
    kind: str
    checked: bool
    element: Any
    glyph: Any | None = None

    def public(self) -> dict:
        return {"location": self.location, "label": self.label,
                "kind": self.kind, "checked": self.checked}

    def set_checked(self, checked: bool) -> None:
        from lxml import etree

        namespace = C if self.kind == "content_control" else W
        node = self.element.find(namespace + "checked")
        if node is None:
            node = etree.SubElement(self.element, namespace + "checked")
        node.set(namespace + "val", "1" if checked else "0")
        if self.kind == "content_control":
            if self.glyph is None:
                raise ValueError("Checkbox symbol is unavailable")
            value, font = _glyph(self.element, checked)
            self.glyph.text = value
            if font:
                run = self.glyph.getparent()
                properties = run.find(W + "rPr")
                if properties is None:
                    properties = etree.Element(W + "rPr")
                    run.insert(0, properties)
                fonts = properties.find(W + "rFonts")
                if fonts is None:
                    fonts = etree.SubElement(properties, W + "rFonts")
                for key in ("ascii", "hAnsi", "eastAsia", "cs"):
                    fonts.set(W + key, font)
        elif self.glyph is not None:
            self.glyph.text = "☒" if checked else "☐"
        self.checked = checked


def docx_checkboxes(parts: dict[str, Any]) -> tuple[dict[str, DocxCheckbox], int]:
    """Expose only controls whose current state and visible result are consistent.

    Legacy fields must be self-contained in one paragraph. Modern controls must
    contain one plain symbol run. Nested/locked/tracked controls remain untouched.
    """
    controls: dict[str, DocxCheckbox] = {}
    unsupported = 0
    for part, root in parts.items():
        for index, sdt in enumerate(root.iter(W + "sdt")):
            properties = sdt.find(W + "sdtPr")
            checkbox = properties.find(C + "checkbox") if properties is not None else None
            if checkbox is None:
                continue
            try:
                if any(parent.tag in {W + "sdt", W + "ins", W + "del"}
                       for parent in sdt.iterancestors()):
                    raise ValueError("Nested or tracked control")
                lock = properties.find(W + "lock")
                if lock is not None and lock.get(W + "val") not in {None, "unlocked", "sdtLocked"}:
                    raise ValueError("Locked content")
                checked = _state(checkbox.find(C + "checked"), C)
                content = sdt.find(W + "sdtContent")
                if content is None or len(content) != 1 or content[0].tag != W + "r":
                    raise ValueError("Unsupported symbol content")
                run = content[0]
                glyphs = run.findall(W + "t")
                if len(glyphs) != 1 or any(node.tag not in {W + "rPr", W + "t"} for node in run):
                    raise ValueError("Unsupported symbol run")
                _glyph(checkbox, not checked)
                if glyphs[0].text != _glyph(checkbox, checked)[0]:
                    raise ValueError("Visible symbol disagrees with state")
                alias = properties.find(W + "alias")
                label = _label(sdt, alias.get(W + "val") if alias is not None else None, index)
                location = f"{part}:checkbox:content:{index}"
                controls[location] = DocxCheckbox(location, label, "content_control", checked, checkbox, glyphs[0])
            except (ValueError, TypeError):
                unsupported += 1
        for index, checkbox in enumerate(root.iter(W + "checkBox")):
            try:
                data = checkbox.getparent()
                begin = data.getparent()
                if data.tag != W + "ffData" or begin.tag != W + "fldChar" or begin.get(W + "fldCharType") != "begin":
                    raise ValueError("Unsupported field structure")
                paragraph = _paragraph(checkbox)
                if paragraph is None or any(parent.tag in {W + "ins", W + "del", W + "sdt"}
                                            for parent in checkbox.iterancestors()):
                    raise ValueError("Nested or tracked field")
                elements = list(paragraph.iter())
                start = elements.index(begin)
                end = next((number for number in range(start + 1, len(elements))
                            if elements[number].tag == W + "fldChar"), None)
                if end is None or elements[end].get(W + "fldCharType") not in {"separate", "end"}:
                    raise ValueError("Unclosed or nested field")
                if elements[end].get(W + "fldCharType") == "separate":
                    result_start = end
                    end = next((number for number in range(end + 1, len(elements))
                                if elements[number].tag == W + "fldChar"), None)
                    if end is None or elements[end].get(W + "fldCharType") != "end":
                        raise ValueError("Unclosed result field")
                    result = [node for node in elements[result_start + 1:end] if node.tag in {W + "t", W + "sym", W + "drawing", W + "pict"}]
                    if len(result) > 1 or any(node.tag != W + "t" or node.text not in {"☐", "☑", "☒"} for node in result):
                        raise ValueError("Unsupported field result")
                    glyph = result[0] if result else None
                else:
                    glyph = None
                checked = _state(checkbox.find(W + "checked"), default=_state(checkbox.find(W + "default")))
                if glyph is not None and (glyph.text in {"☑", "☒"}) != checked:
                    raise ValueError("Visible result disagrees with state")
                name = data.find(W + "name")
                label = _label(checkbox, name.get(W + "val") if name is not None else None, index)
                location = f"{part}:checkbox:legacy:{index}"
                controls[location] = DocxCheckbox(location, label, "legacy_field", checked, checkbox, glyph)
            except (ValueError, TypeError):
                unsupported += 1
    return controls, unsupported
