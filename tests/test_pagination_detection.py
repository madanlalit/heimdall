"""Unit tests for DomService.detect_pagination_buttons."""

from typing import Any, cast

from heimdall.dom.service import DOMNode, DomService


def _service() -> DomService:
    return DomService(session=cast(Any, object()))


def _node(
    backend_node_id: int,
    *,
    tag: str = "BUTTON",
    ax_name: str = "",
    attributes: dict[str, str] | None = None,
    role: str = "",
) -> DOMNode:
    node_attributes = attributes or {}
    if role:
        node_attributes["role"] = role
    return DOMNode(
        backend_node_id=backend_node_id,
        node_name=tag,
        attributes=node_attributes,
        ax_name=ax_name,
        bounding_box={"x": 0, "y": 0, "width": 120, "height": 40},
    )


def test_detects_next_prev_and_page_number():
    service = _service()
    nodes = [
        _node(1, ax_name="Previous"),
        _node(2, ax_name="2", attributes={"class": "pagination page-link"}),
        _node(3, ax_name="Next"),
    ]

    pagination = service.detect_pagination_buttons(nodes)

    assert pagination["prev_button"] is not None
    assert pagination["prev_button"]["backend_node_id"] == 1
    assert pagination["next_button"] is not None
    assert pagination["next_button"]["backend_node_id"] == 3
    assert [button["backend_node_id"] for button in pagination["page_buttons"]] == [2]


def test_does_not_match_substring_false_positives():
    service = _service()
    nodes = [
        _node(1, ax_name="Next Monday"),
        _node(2, ax_name="Preview"),
        _node(3, ax_name="Prevent"),
    ]

    pagination = service.detect_pagination_buttons(nodes)

    assert pagination["next_button"] is None
    assert pagination["prev_button"] is None
    assert pagination["page_buttons"] == []


def test_does_not_use_node_name_as_text_fallback():
    service = _service()
    nodes = [
        _node(1, tag="NEXT-PAGINATION", role="button"),
    ]

    pagination = service.detect_pagination_buttons(nodes)

    assert pagination["next_button"] is None
    assert pagination["prev_button"] is None


def test_requires_context_for_unicode_arrows():
    service = _service()
    nodes = [
        _node(1, ax_name="»", attributes={"class": "breadcrumb-item"}),
        _node(2, ax_name="»", attributes={"class": "pagination next"}),
    ]

    pagination = service.detect_pagination_buttons(nodes)

    assert pagination["next_button"] is not None
    assert pagination["next_button"]["backend_node_id"] == 2


def test_rejects_year_numbers_as_page_buttons():
    service = _service()
    nodes = [
        _node(1, ax_name="2024", attributes={"class": "pagination page-link"}),
        _node(2, ax_name="12", attributes={"class": "pagination page-link"}),
    ]

    pagination = service.detect_pagination_buttons(nodes)

    assert [button["backend_node_id"] for button in pagination["page_buttons"]] == [2]
