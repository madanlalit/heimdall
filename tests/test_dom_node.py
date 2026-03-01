"""Unit tests for DOMNode properties: is_interactive, is_visible, stable_hash."""

import pytest

from heimdall.dom.service import DOMNode


# ── Helpers ──────────────────────────────────────────────────────────────────


def _node(
    tag: str = "DIV",
    attributes: dict | None = None,
    ax_role: str = "",
    ax_name: str = "",
    bounding_box: dict | None = None,
) -> DOMNode:
    return DOMNode(
        backend_node_id=1,
        node_name=tag,
        attributes=attributes or {},
        ax_role=ax_role,
        ax_name=ax_name,
        bounding_box=bounding_box,
    )


# ── is_interactive ─────────────────────────────────────────────────────────────


class TestIsInteractive:
    """DOMNode.is_interactive should return True for any interactive element."""

    @pytest.mark.parametrize(
        "tag",
        [
            "A",
            "BUTTON",
            "INPUT",
            "SELECT",
            "TEXTAREA",
            "LABEL",
            "DETAILS",
            "SUMMARY",
            "OPTION",
            "OPTGROUP",
        ],
    )
    def test_interactive_html_tags(self, tag: str):
        assert _node(tag=tag).is_interactive is True

    def test_interactive_tag_case_insensitive(self):
        """Node name comparison should be case-insensitive."""
        assert _node(tag="button").is_interactive is True
        assert _node(tag="Input").is_interactive is True

    @pytest.mark.parametrize(
        "ax_role",
        [
            "button",
            "link",
            "menuitem",
            "option",
            "radio",
            "checkbox",
            "tab",
            "textbox",
            "combobox",
            "slider",
            "spinbutton",
            "search",
            "searchbox",
            "listbox",
            "switch",
            "treeitem",
        ],
    )
    def test_interactive_ax_roles(self, ax_role: str):
        """Elements with interactive ARIA roles via AX tree are interactive."""
        assert _node(ax_role=ax_role).is_interactive is True

    @pytest.mark.parametrize(
        "role",
        ["button", "link", "textbox", "combobox"],
    )
    def test_interactive_role_attribute(self, role: str):
        """Elements with role= attribute are interactive."""
        assert _node(attributes={"role": role}).is_interactive is True

    def test_contenteditable_true(self):
        assert _node(attributes={"contenteditable": "true"}).is_interactive is True

    def test_contenteditable_plaintext_only(self):
        assert _node(attributes={"contenteditable": "plaintext-only"}).is_interactive is True

    def test_contenteditable_false_not_interactive(self):
        assert _node(attributes={"contenteditable": "false"}).is_interactive is False

    @pytest.mark.parametrize(
        "handler",
        ["onclick", "onmousedown", "onmouseup", "onkeydown", "onkeyup"],
    )
    def test_event_handler_attributes(self, handler: str):
        assert _node(attributes={handler: "doSomething()"}).is_interactive is True

    def test_tabindex_attribute(self):
        assert _node(attributes={"tabindex": "0"}).is_interactive is True

    def test_cursor_pointer_style(self):
        assert _node(attributes={"style": "cursor: pointer; color: red"}).is_interactive is True

    def test_plain_div_not_interactive(self):
        assert _node(tag="DIV").is_interactive is False

    def test_span_with_no_interactive_attributes(self):
        assert _node(tag="SPAN", attributes={"class": "icon"}).is_interactive is False


# ── is_visible ────────────────────────────────────────────────────────────────


class TestIsVisible:
    """DOMNode.is_visible requires a bounding box with positive dimensions."""

    def test_visible_with_positive_dimensions(self):
        node = _node(bounding_box={"x": 0, "y": 0, "width": 100, "height": 50})
        assert node.is_visible is True

    def test_not_visible_without_bounding_box(self):
        assert _node(bounding_box=None).is_visible is False

    def test_not_visible_zero_width(self):
        node = _node(bounding_box={"x": 0, "y": 0, "width": 0, "height": 50})
        assert node.is_visible is False

    def test_not_visible_zero_height(self):
        node = _node(bounding_box={"x": 0, "y": 0, "width": 100, "height": 0})
        assert node.is_visible is False

    def test_not_visible_zero_both(self):
        node = _node(bounding_box={"x": 0, "y": 0, "width": 0, "height": 0})
        assert node.is_visible is False

    def test_visible_small_dimensions(self):
        node = _node(bounding_box={"x": 10, "y": 10, "width": 1, "height": 1})
        assert node.is_visible is True


# ── stable_hash ───────────────────────────────────────────────────────────────


class TestStableHash:
    """DOMNode.stable_hash should be stable and filter dynamic CSS classes."""

    def test_same_element_same_hash(self):
        node = _node(tag="BUTTON", attributes={"id": "submit", "class": "btn primary"})
        assert node.stable_hash == node.stable_hash

    def test_different_id_different_hash(self):
        a = _node(tag="BUTTON", attributes={"id": "btn-a"})
        b = _node(tag="BUTTON", attributes={"id": "btn-b"})
        assert a.stable_hash != b.stable_hash

    def test_different_tag_different_hash(self):
        a = _node(tag="BUTTON", attributes={"id": "x"})
        b = _node(tag="A", attributes={"id": "x"})
        assert a.stable_hash != b.stable_hash

    @pytest.mark.parametrize(
        "dynamic_class",
        [
            "focus",
            "hover",
            "active",
            "selected",
            "disabled",
            "animation",
            "transition",
            "loading",
            "open",
            "closed",
            "expanded",
            "collapsed",
            "visible",
            "hidden",
            "pressed",
            "checked",
            "highlighted",
            "current",
            "entering",
            "leaving",
        ],
    )
    def test_dynamic_classes_filtered(self, dynamic_class: str):
        """Elements differing only in dynamic state classes share a hash."""
        base = _node(tag="BUTTON", attributes={"id": "x", "class": "btn"})
        with_dynamic = _node(
            tag="BUTTON",
            attributes={"id": "x", "class": f"btn {dynamic_class}"},
        )
        assert base.stable_hash == with_dynamic.stable_hash

    def test_stable_class_differences_produce_different_hashes(self):
        a = _node(tag="BUTTON", attributes={"class": "btn primary"})
        b = _node(tag="BUTTON", attributes={"class": "btn secondary"})
        assert a.stable_hash != b.stable_hash

    def test_style_attribute_excluded(self):
        """Inline styles should not affect the hash."""
        a = _node(tag="DIV", attributes={"id": "x", "style": "color: red"})
        b = _node(tag="DIV", attributes={"id": "x", "style": "color: blue"})
        assert a.stable_hash == b.stable_hash

    def test_hash_is_integer(self):
        node = _node(tag="A", attributes={"href": "/path"})
        assert isinstance(node.stable_hash, int)
