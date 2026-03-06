"""Unit tests for browser actions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from heimdall.tools import actions


def _dom_state(selector_map: dict[int, dict] | None = None, scroll_info: dict | None = None):
    return SimpleNamespace(
        selector_map=selector_map or {},
        scroll_info=scroll_info or {},
    )


def _session(layout_metrics: dict | None = None):
    dispatch_mouse_event = AsyncMock()
    get_layout_metrics = AsyncMock(
        return_value=layout_metrics
        or {
            "layoutViewport": {"clientWidth": 800, "clientHeight": 600},
            "visualViewport": {
                "clientWidth": 800,
                "clientHeight": 600,
                "scale": 1,
                "offsetX": 0,
                "offsetY": 0,
            },
        }
    )
    send = SimpleNamespace(
        Page=SimpleNamespace(getLayoutMetrics=get_layout_metrics),
        Input=SimpleNamespace(dispatchMouseEvent=dispatch_mouse_event),
    )
    session = SimpleNamespace(
        cdp_client=SimpleNamespace(send=send),
        session_id="session-1",
    )
    return session, dispatch_mouse_event, get_layout_metrics



def _extract_session(
    url: str = "https://example.com",
    title: str = "Example Page",
    page_text: str = "Acme Corp ships widgets worldwide.",
    links: list[dict] | None = None,
):
    session = SimpleNamespace(
        get_url=AsyncMock(return_value=url),
        get_title=AsyncMock(return_value=title),
        execute_js=AsyncMock(side_effect=[page_text, links or []]),
    )
    return session


class TestClickAction:
    @pytest.mark.asyncio
    async def test_click_by_index_uses_element_click(self, monkeypatch):
        from heimdall.browser import element as element_module

        click_calls: list[tuple[str, int]] = []

        class FakeElement:
            def __init__(self, session, backend_node_id):
                click_calls.append(("init", backend_node_id))

            async def click(self):
                click_calls.append(("click", -1))

        monkeypatch.setattr(element_module, "Element", FakeElement)

        result = await actions.click(
            session=object(),
            dom_state=_dom_state(
                selector_map={3: {"backend_node_id": 91, "tag": "BUTTON", "text": "Submit"}}
            ),
            index=3,
        )

        assert result.success is True
        assert result.message == "Clicked element 3"
        assert result.data["element"]["backend_node_id"] == 91
        assert click_calls == [("init", 91), ("click", -1)]

    @pytest.mark.asyncio
    async def test_click_by_coordinates_dispatches_mouse_events(self):
        session, dispatch_mouse_event, _ = _session()

        result = await actions.click(
            session=session,
            dom_state=_dom_state(scroll_info={"width": 800, "height": 600}),
            x=125,
            y=240,
        )

        assert result.success is True
        assert result.message == "Clicked viewport coordinates (125, 240)"
        assert result.data["viewport_coordinates"] == {"x": 125, "y": 240}
        assert result.data["layout_coordinates"] == {"x": 125, "y": 240}
        assert dispatch_mouse_event.await_count == 3
        assert dispatch_mouse_event.await_args_list[0].args[0] == {
            "type": "mouseMoved",
            "x": 125,
            "y": 240,
        }
        assert dispatch_mouse_event.await_args_list[1].args[0]["type"] == "mousePressed"
        assert dispatch_mouse_event.await_args_list[2].args[0]["type"] == "mouseReleased"

    @pytest.mark.asyncio
    async def test_click_by_coordinates_converts_scaled_viewport(self):
        session, dispatch_mouse_event, _ = _session(
            {
                "layoutViewport": {"clientWidth": 500, "clientHeight": 400},
                "visualViewport": {
                    "clientWidth": 250,
                    "clientHeight": 200,
                    "scale": 2,
                    "offsetX": 10,
                    "offsetY": 20,
                },
            }
        )

        result = await actions.click(
            session=session,
            dom_state=_dom_state(scroll_info={"width": 500, "height": 400}),
            x=100,
            y=80,
        )

        assert result.success is True
        assert result.data["layout_coordinates"] == {"x": 60, "y": 60}
        assert result.data["conversion"] == {"scale": 2.0, "offset_x": 10.0, "offset_y": 20.0}
        assert dispatch_mouse_event.await_args_list[0].args[0]["x"] == 60
        assert dispatch_mouse_event.await_args_list[0].args[0]["y"] == 60

    @pytest.mark.asyncio
    async def test_click_rejects_mixed_index_and_coordinates(self):
        session, _, _ = _session()

        result = await actions.click(
            session=session,
            dom_state=_dom_state(selector_map={1: {"backend_node_id": 5, "tag": "BUTTON"}}),
            index=1,
            x=10,
            y=20,
        )

        assert result.success is False
        assert result.error == "Click accepts either an index or x/y coordinates, not both"

    @pytest.mark.asyncio
    async def test_click_requires_complete_coordinate_pair(self):
        session, _, _ = _session()

        result = await actions.click(
            session=session,
            dom_state=_dom_state(),
            x=10,
        )

        assert result.success is False
        assert result.error == "Click requires either an index or both x and y viewport coordinates"


class TestExtractAction:
    @pytest.mark.asyncio
    async def test_extract_uses_response_schema_when_supported(self):
        llm = SimpleNamespace(
            supports_response_schema=True,
            chat_completion=AsyncMock(return_value={"content": '{"company": "Acme Corp"}'}),
        )
        session = _extract_session(
            page_text="Acme Corp is based in San Francisco.",
            links=[{"text": "About", "href": "https://example.com/about"}],
        )

        schema = {
            "type": "object",
            "properties": {"company": {"type": "string"}},
            "required": ["company"],
        }

        result = await actions.extract(
            goal="Find the company name",
            json_schema=schema,
            session=session,
            dom_state=_dom_state(),
            llm=llm,
        )

        assert result.success is True
        assert result.data["extracted"] == {"company": "Acme Corp"}
        assert '"company": "Acme Corp"' in result.message
        assert llm.chat_completion.await_args.kwargs["response_schema"] == schema

    @pytest.mark.asyncio
    async def test_extract_parses_markdown_wrapped_json_without_schema_support(self):
        llm = SimpleNamespace(
            chat_completion=AsyncMock(
                return_value={"content": '```json\n{"company": "Acme Corp"}\n```'}
            ),
        )
        session = _extract_session(page_text="Acme Corp builds browsers.")

        result = await actions.extract(
            goal="Return the company name as JSON",
            json_schema={"type": "object"},
            session=session,
            dom_state=_dom_state(),
            llm=llm,
        )

        assert result.success is True
        assert result.data["extracted"] == {"company": "Acme Corp"}
        assert "response_schema" not in llm.chat_completion.await_args.kwargs

    @pytest.mark.asyncio
    async def test_extract_rejects_invalid_schema_json(self):
        session = _extract_session()
        llm = SimpleNamespace(chat_completion=AsyncMock())

        result = await actions.extract(
            goal="Find the company name",
            json_schema='{bad json}',
            session=session,
            dom_state=_dom_state(),
            llm=llm,
        )

        assert result.success is False
        assert result.error == (
            "Invalid extraction schema: schema must be valid JSON: "
            "Expecting property name enclosed in double quotes"
        )

    @pytest.mark.asyncio
    async def test_extract_requires_llm_context(self):
        session = _extract_session()

        result = await actions.extract(
            goal="Find the company name",
            session=session,
            dom_state=_dom_state(),
            llm=None,
        )

        assert result.success is False
        assert result.error == "LLM client not initialized in context"
