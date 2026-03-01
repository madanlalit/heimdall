"""Unit tests for DOMSerializer: serialize() and _describe_node()."""

from heimdall.dom.service import DOMNode, DOMSerializer, SelectorGenerator


# ── Helpers ──────────────────────────────────────────────────────────────────


def _node(
    backend_node_id: int = 1,
    tag: str = "BUTTON",
    attributes: dict | None = None,
    ax_name: str = "",
    ax_role: str = "",
    bounding_box: dict | None = None,
) -> DOMNode:
    bbox = (
        bounding_box if bounding_box is not None else {"x": 0, "y": 0, "width": 100, "height": 40}
    )
    return DOMNode(
        backend_node_id=backend_node_id,
        node_name=tag,
        attributes=attributes or {},
        ax_name=ax_name,
        ax_role=ax_role,
        bounding_box=bbox,
    )


def _invisible_node(**kwargs) -> DOMNode:
    # Zero-dimension bbox makes is_visible return False (width=0, height=0)
    return _node(bounding_box={"x": 0, "y": 0, "width": 0, "height": 0}, **kwargs)


_serializer = DOMSerializer()
_selector_gen = SelectorGenerator()


# ── serialize ─────────────────────────────────────────────────────────────────


class TestSerialize:
    """DOMSerializer.serialize should filter and index visible interactive nodes."""

    def test_empty_tree_produces_empty_output(self):
        result = _serializer.serialize([], _selector_gen)
        assert result.text == ""
        assert result.selector_map == {}
        assert result.element_count == 0

    def test_non_interactive_nodes_excluded(self):
        """A plain visible <DIV> is not interactive and should be excluded."""
        div = _node(tag="DIV", bounding_box={"x": 0, "y": 0, "width": 100, "height": 40})
        result = _serializer.serialize([div], _selector_gen)
        assert result.element_count == 0
        assert result.text == ""

    def test_invisible_nodes_excluded(self):
        """An invisible <BUTTON> (no bbox) should be excluded."""
        btn = _invisible_node(tag="BUTTON")
        result = _serializer.serialize([btn], _selector_gen)
        assert result.element_count == 0

    def test_visible_interactive_node_included(self):
        btn = _node(tag="BUTTON", ax_name="Submit", attributes={"id": "submit-btn"})
        result = _serializer.serialize([btn], _selector_gen)
        assert result.element_count == 1
        assert "[0]" in result.text

    def test_multiple_nodes_indexed_sequentially(self):
        nodes = [
            _node(backend_node_id=1, tag="BUTTON", ax_name="First"),
            _node(backend_node_id=2, tag="INPUT", attributes={"type": "text"}),
            _node(backend_node_id=3, tag="A", attributes={"href": "/home"}, ax_name="Home"),
        ]
        result = _serializer.serialize(nodes, _selector_gen)
        assert result.element_count == 3
        assert "[0]" in result.text
        assert "[1]" in result.text
        assert "[2]" in result.text

    def test_selector_map_keys_match_indices(self):
        nodes = [
            _node(backend_node_id=10, tag="BUTTON", ax_name="A"),
            _node(backend_node_id=20, tag="INPUT", attributes={"type": "text"}),
        ]
        result = _serializer.serialize(nodes, _selector_gen)
        assert set(result.selector_map.keys()) == {0, 1}

    def test_selector_map_contains_backend_node_id(self):
        btn = _node(backend_node_id=42, tag="BUTTON", ax_name="Go")
        result = _serializer.serialize([btn], _selector_gen)
        assert result.selector_map[0]["backend_node_id"] == 42

    def test_selector_map_contains_tag(self):
        btn = _node(tag="BUTTON", ax_name="Go")
        result = _serializer.serialize([btn], _selector_gen)
        assert result.selector_map[0]["tag"] == "BUTTON"

    def test_non_interactive_mixed_with_interactive(self):
        """Only interactive+visible nodes are counted; others silently dropped."""
        nodes = [
            _node(tag="DIV"),  # not interactive
            _node(tag="BUTTON", ax_name="Click me"),  # interactive + visible ✓
            _node(tag="A", bounding_box={"x": 0, "y": 0, "width": 0, "height": 0}),  # not visible
            _node(tag="INPUT", attributes={"type": "checkbox"}),  # interactive + visible ✓
        ]
        result = _serializer.serialize(nodes, _selector_gen)
        assert result.element_count == 2


# ── _describe_node ─────────────────────────────────────────────────────────────


class TestDescribeNode:
    """DOMSerializer._describe_node should produce human-readable descriptions."""

    def _describe(self, **kwargs) -> str:
        node = _node(**kwargs)
        return _serializer._describe_node(node)

    def test_tag_always_shown(self):
        desc = self._describe(tag="BUTTON")
        assert "button" in desc

    def test_ax_name_shown_in_quotes(self):
        desc = self._describe(tag="BUTTON", ax_name="Submit")
        assert '"Submit"' in desc

    def test_input_type_shown(self):
        desc = self._describe(tag="INPUT", attributes={"type": "email"})
        assert "type=email" in desc

    def test_placeholder_shown(self):
        desc = self._describe(tag="INPUT", attributes={"placeholder": "Search here"})
        assert 'placeholder="Search here"' in desc

    def test_placeholder_truncated_at_30_chars(self):
        long_placeholder = "A" * 50
        desc = self._describe(tag="INPUT", attributes={"placeholder": long_placeholder})
        assert 'placeholder="' + "A" * 30 + '"' in desc

    def test_div_role_shown(self):
        desc = self._describe(tag="DIV", ax_role="textbox")
        assert "role=textbox" in desc

    def test_div_role_attribute_shown(self):
        desc = self._describe(tag="DIV", attributes={"role": "combobox"})
        assert "role=combobox" in desc

    def test_contenteditable_shown(self):
        desc = self._describe(tag="DIV", attributes={"contenteditable": "true"})
        assert "contenteditable" in desc

    def test_required_shown(self):
        # The source uses `node.attributes.get("required")` which is truthy only for
        # non-empty values. Use "required" as the attribute value (standard HTML boolean).
        desc = self._describe(tag="INPUT", attributes={"required": "required", "type": "text"})
        assert "required" in desc

    def test_min_max_shown(self):
        desc = self._describe(tag="INPUT", attributes={"type": "number", "min": "1", "max": "100"})
        assert "min=1" in desc
        assert "max=100" in desc

    def test_maxlength_shown(self):
        desc = self._describe(tag="INPUT", attributes={"type": "text", "maxlength": "255"})
        assert "maxlen=255" in desc

    def test_data_testid_shown(self):
        desc = self._describe(tag="BUTTON", attributes={"data-testid": "submit-btn"})
        assert 'data-testid="submit-btn"' in desc

    def test_disabled_shown(self):
        desc = self._describe(tag="BUTTON", attributes={"disabled": ""})
        assert "disabled" in desc

    def test_readonly_shown(self):
        desc = self._describe(tag="INPUT", attributes={"type": "text", "readonly": ""})
        assert "readonly" in desc

    def test_aria_label_shown_when_no_ax_name(self):
        desc = self._describe(
            tag="BUTTON",
            ax_name="",
            attributes={"aria-label": "Close dialog"},
        )
        assert '"Close dialog"' in desc

    def test_aria_label_suppressed_when_ax_name_present(self):
        """ax_name takes priority — aria-label should not appear alongside it."""
        desc = self._describe(
            tag="BUTTON",
            ax_name="Close",
            attributes={"aria-label": "Close dialog"},
        )
        # ax_name shown
        assert '"Close"' in desc
        # aria-label NOT shown again (it would be redundant)
        assert '"Close dialog"' not in desc
