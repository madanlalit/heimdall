"""
DOM Service - DOM extraction and serialization for Heimdall.

Orchestrates parallel CDP calls to extract DOM, accessibility tree,
and layout information, then serializes for LLM consumption.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from heimdall.browser.session import BrowserSession

logger = logging.getLogger(__name__)


class DomService:
    """
    Extracts and serializes DOM state.

    Uses parallel CDP calls for performance:
    - DOMSnapshot.captureSnapshot - DOM tree with styles
    - Accessibility.getFullAXTree - Accessibility info
    - Page.getLayoutMetrics - Viewport info
    """

    def __init__(self, session: "BrowserSession"):
        self._session = session
        self._serializer = DOMSerializer()
        self._selector_generator = SelectorGenerator()

    async def get_state(self) -> "SerializedDOM":
        """
        Get current DOM state serialized for LLM.

        Returns:
            SerializedDOM with element tree and selector map
        """
        # Parallel CDP calls
        snapshot, ax_tree, layout = await asyncio.gather(
            self._get_snapshot(),
            self._get_accessibility_tree(),
            self._get_layout_metrics(),
        )

        # Build enhanced tree
        tree = self._build_tree(snapshot, ax_tree, layout)

        # Serialize for LLM
        return self._serializer.serialize(tree, self._selector_generator)

    async def _get_snapshot(self) -> dict:
        """Capture DOM snapshot with styles."""
        try:
            result = await self._session.cdp_client.send.DOMSnapshot.captureSnapshot(
                {
                    "computedStyles": ["visibility", "display", "opacity"],
                    "includeDOMRects": True,
                    "includePaintOrder": True,
                },
                session_id=self._session.session_id,
            )
            return result
        except Exception as e:
            logger.error(f"DOM snapshot failed: {e}")
            return {}

    async def _get_accessibility_tree(self) -> dict:
        """Get accessibility tree."""
        try:
            result = await self._session.cdp_client.send.Accessibility.getFullAXTree(
                session_id=self._session.session_id,
            )
            return result
        except Exception as e:
            logger.debug(f"AX tree failed: {e}")
            return {}

    async def _get_layout_metrics(self) -> dict:
        """Get layout/viewport metrics."""
        try:
            result = await self._session.cdp_client.send.Page.getLayoutMetrics(
                session_id=self._session.session_id,
            )
            return result
        except Exception as e:
            logger.debug(f"Layout metrics failed: {e}")
            return {}

    def _build_tree(
        self,
        snapshot: dict,
        ax_tree: dict,
        layout: dict,
    ) -> list["DOMNode"]:
        """Build enhanced DOM tree from CDP data."""
        nodes = []

        documents = snapshot.get("documents", [])
        if not documents:
            return nodes

        doc = documents[0]
        node_names = doc.get("nodes", {}).get("nodeName", [])
        backend_ids = doc.get("nodes", {}).get("backendNodeId", [])
        parent_indices = doc.get("nodes", {}).get("parentIndex", [])
        attributes = doc.get("nodes", {}).get("attributes", [])
        layout_info = doc.get("layout", {})

        # Build AX node map
        ax_nodes = {
            node.get("backendDOMNodeId"): node
            for node in ax_tree.get("nodes", [])
            if node.get("backendDOMNodeId")
        }

        strings = snapshot.get("strings", [])

        for i, backend_id in enumerate(backend_ids):
            if backend_id == 0:
                continue

            # Get node name
            name_idx = node_names[i] if i < len(node_names) else -1
            node_name = strings[name_idx] if 0 <= name_idx < len(strings) else ""

            # Skip non-interactive nodes
            if node_name.upper() in [
                "#text",
                "#comment",
                "SCRIPT",
                "STYLE",
                "META",
                "LINK",
                "HEAD",
            ]:
                continue

            # Get attributes
            attr_dict = {}
            if i < len(attributes):
                attr_list = attributes[i]
                for j in range(0, len(attr_list), 2):
                    name_idx = attr_list[j]
                    val_idx = attr_list[j + 1]
                    attr_name = strings[name_idx] if 0 <= name_idx < len(strings) else ""
                    attr_val = strings[val_idx] if 0 <= val_idx < len(strings) else ""
                    attr_dict[attr_name] = attr_val

            # Get bounding box from layout
            bbox = None
            layout_indices = layout_info.get("nodeIndex", [])
            layout_bounds = layout_info.get("bounds", [])
            if i in layout_indices:
                layout_idx = layout_indices.index(i)
                if layout_idx < len(layout_bounds):
                    b = layout_bounds[layout_idx]
                    if len(b) >= 4:
                        bbox = {"x": b[0], "y": b[1], "width": b[2], "height": b[3]}

            # Get AX info
            ax_node = ax_nodes.get(backend_id, {})
            ax_name = ax_node.get("name", {}).get("value", "")
            ax_role = ax_node.get("role", {}).get("value", "")

            node = DOMNode(
                backend_node_id=backend_id,
                node_name=node_name,
                attributes=attr_dict,
                bounding_box=bbox,
                ax_name=ax_name,
                ax_role=ax_role,
                parent_index=parent_indices[i] if i < len(parent_indices) else -1,
            )
            nodes.append(node)

        return nodes


class DOMNode(BaseModel):
    """Enhanced DOM node with AX and layout info."""

    backend_node_id: int
    node_name: str
    attributes: dict[str, str] = Field(default_factory=dict)
    bounding_box: dict[str, float] | None = None
    ax_name: str = ""
    ax_role: str = ""
    parent_index: int = -1

    @property
    def is_interactive(self) -> bool:
        """Check if node is interactive."""
        interactive_tags = {"A", "BUTTON", "INPUT", "SELECT", "TEXTAREA", "LABEL"}
        clickable_roles = {"button", "link", "checkbox", "radio", "menuitem", "tab"}

        if self.node_name.upper() in interactive_tags:
            return True
        if self.ax_role in clickable_roles:
            return True
        return bool(self.attributes.get("onclick") or self.attributes.get("role") in clickable_roles)

    @property
    def is_visible(self) -> bool:
        """Check if node is visible."""
        if not self.bounding_box:
            return False

        bbox = self.bounding_box
        return bbox.get("width", 0) > 0 and bbox.get("height", 0) > 0


class SelectorGenerator:
    """Generates multiple selector strategies for elements."""

    def generate(self, node: DOMNode) -> dict[str, str]:
        """Generate selectors for a node."""
        selectors = {}

        # CSS by ID
        if node.attributes.get("id"):
            selectors["css_id"] = f"#{node.attributes['id']}"

        # data-testid
        if node.attributes.get("data-testid"):
            selectors["testid"] = f'[data-testid="{node.attributes["data-testid"]}"]'

        # aria-label
        if node.attributes.get("aria-label"):
            selectors["aria"] = f'[aria-label="{node.attributes["aria-label"]}"]'

        # placeholder for inputs
        if node.attributes.get("placeholder"):
            selectors["placeholder"] = f'[placeholder="{node.attributes["placeholder"]}"]'

        # name attribute
        if node.attributes.get("name"):
            selectors["name"] = f'[name="{node.attributes["name"]}"]'

        # Text content (for buttons/links)
        if node.ax_name:
            selectors["text"] = node.ax_name

        # XPath by attributes
        attrs = []
        for key in ["id", "name", "data-testid"]:
            if node.attributes.get(key):
                attrs.append(f"@{key}='{node.attributes[key]}'")

        if attrs:
            selectors["xpath"] = f"//{node.node_name.lower()}[{' and '.join(attrs)}]"

        return selectors


class DOMSerializer:
    """Serializes DOM tree for LLM consumption."""

    def serialize(
        self,
        nodes: list[DOMNode],
        selector_gen: SelectorGenerator,
    ) -> "SerializedDOM":
        """
        Serialize DOM nodes with indexing.

        Returns:
            SerializedDOM with LLM-friendly text and selector map
        """
        # Filter to visible, interactive elements
        interactive_nodes = [n for n in nodes if n.is_visible and n.is_interactive]

        # Build indexed representation
        lines = []
        selector_map = {}

        for idx, node in enumerate(interactive_nodes):
            selectors = selector_gen.generate(node)
            selector_map[idx] = {
                "backend_node_id": node.backend_node_id,
                "selectors": selectors,
                "tag": node.node_name,
                "attributes": node.attributes,
            }

            # Build LLM-friendly line
            desc = self._describe_node(node)
            lines.append(f"[{idx}] {desc}")

        return SerializedDOM(
            text="\n".join(lines),
            selector_map=selector_map,
            element_count=len(interactive_nodes),
        )

    def _describe_node(self, node: DOMNode) -> str:
        """Create human-readable description."""
        parts = [node.node_name.lower()]

        if node.ax_name:
            parts.append(f'"{node.ax_name}"')

        if node.attributes.get("type"):
            parts.append(f"type={node.attributes['type']}")

        if node.attributes.get("placeholder"):
            parts.append(f"placeholder={node.attributes['placeholder']}")

        return " ".join(parts)


class SerializedDOM(BaseModel):
    """Serialized DOM state for LLM."""

    text: str = ""
    selector_map: dict[int, dict] = Field(default_factory=dict)
    element_count: int = 0
