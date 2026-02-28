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
        serialized = self._serializer.serialize(tree, self._selector_generator)

        # Add scroll/viewport info
        # getLayoutMetrics returns layoutViewport and visualViewport
        if layout:
            viewport = layout.get("visualViewport", {}) or layout.get("layoutViewport", {})
            serialized.scroll_info = {
                "x": viewport.get("pageX", 0),
                "y": viewport.get("pageY", 0),
                "width": viewport.get("clientWidth", 0),
                "height": viewport.get("clientHeight", 0),
            }

        return serialized

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
        nodes: list[DOMNode] = []

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
        """
        Check if node is interactive.

        Uses comprehensive detection matching browser-use:
        - Interactive HTML tags
        - ARIA roles (including textbox, combobox for divs like ChatGPT input)
        - Event handlers (onclick, etc.)
        - Contenteditable attribute
        - Tabindex attribute
        - Cursor style (pointer)
        """
        # Interactive HTML tags (including form elements, links, etc.)
        interactive_tags = {
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
        }

        # Interactive ARIA roles (covers divs with role="textbox", etc.)
        interactive_roles = {
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
        }

        # Check by tag name
        if self.node_name.upper() in interactive_tags:
            return True

        # Check by accessibility role (from AX tree)
        if self.ax_role in interactive_roles:
            return True

        # Check attributes for interactivity
        if self.attributes:
            # ARIA role attribute (for divs with role="textbox" like ChatGPT)
            role = self.attributes.get("role", "")
            if role in interactive_roles:
                return True

            # Contenteditable (rich text editors, ChatGPT input, etc.)
            contenteditable = self.attributes.get("contenteditable", "")
            if contenteditable and contenteditable.lower() not in ("false", ""):
                return True

            # Event handlers
            event_handlers = {"onclick", "onmousedown", "onmouseup", "onkeydown", "onkeyup"}
            if any(attr in self.attributes for attr in event_handlers):
                return True

            # Tabindex (explicitly focusable)
            if "tabindex" in self.attributes:
                return True

            # Cursor pointer in style (indicates clickable)
            style = self.attributes.get("style", "")
            if "cursor" in style and "pointer" in style:
                return True

        return False

    @property
    def is_visible(self) -> bool:
        """Check if node is visible."""
        if not self.bounding_box:
            return False

        bbox = self.bounding_box
        return bbox.get("width", 0) > 0 and bbox.get("height", 0) > 0

    @property
    def stable_hash(self) -> int:
        """
        Compute stable hash for element identification across sessions.

        Filters out dynamic CSS classes (hover, focus, active, loading, etc.)
        to provide consistent identification even when element state changes.
        """
        # Dynamic class patterns to filter out
        dynamic_patterns = frozenset(
            {
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
            }
        )

        # Filter class attribute
        class_str = self.attributes.get("class", "")
        if class_str:
            classes = class_str.split()
            stable_classes = [
                c for c in classes if not any(pattern in c.lower() for pattern in dynamic_patterns)
            ]
            stable_class_str = " ".join(sorted(stable_classes))
        else:
            stable_class_str = ""

        # Build stable attributes (exclude dynamic ones)
        stable_attrs = {}
        for key, val in self.attributes.items():
            if key == "class":
                stable_attrs["class"] = stable_class_str
            elif key not in ("style",):  # Exclude style as it can be dynamic
                stable_attrs[key] = val

        # Compute hash from stable components
        import hashlib

        hash_components = (
            self.node_name,
            tuple(sorted(stable_attrs.items())),
            self.ax_role,
        )
        return int(hashlib.sha256(str(hash_components).encode("utf-8")).hexdigest(), 16)


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

        # href for anchor tags — most stable selector for links (contains path/ASIN/slug)
        # Strip query params so the selector stays reusable across sessions
        if node.node_name.upper() == "A" and node.attributes.get("href"):
            href = node.attributes["href"].split("?")[0].rstrip("/")
            if href:
                selectors["href"] = f'a[href*="{href}"]'
                selectors["href_xpath"] = f"//a[contains(@href, '{href}')]"

        # Text content (for buttons/links)
        if node.ax_name:
            selectors["text"] = node.ax_name

        # XPath by attributes (id / name / data-testid)
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
            scroll_info={},  # Placeholder, will be populated by service
        )

    def _describe_node(self, node: DOMNode) -> str:
        """Create human-readable description with rich context for LLM."""
        parts = [node.node_name.lower()]

        # Show accessibility name (button text, link text, etc.)
        if node.ax_name:
            parts.append(f'"{node.ax_name}"')

        # Show ARIA role for divs with textbox role (like ChatGPT input)
        role = node.attributes.get("role", "") or node.ax_role
        if role and node.node_name.upper() == "DIV":
            parts.append(f"role={role}")

        # Show contenteditable (rich text editors, chat inputs)
        if node.attributes.get("contenteditable"):
            parts.append("contenteditable")

        # Show type for inputs
        if node.attributes.get("type"):
            parts.append(f"type={node.attributes['type']}")

        # Show placeholder (standard and data-placeholder for contenteditable)
        placeholder = node.attributes.get("placeholder") or node.attributes.get("data-placeholder")
        if placeholder:
            parts.append(f'placeholder="{placeholder[:30]}"')

        # Show aria-label if no ax_name
        if not node.ax_name and node.attributes.get("aria-label"):
            parts.append(f'"{node.attributes["aria-label"]}"')

        # === ENHANCED ATTRIBUTES FOR LLM CONTEXT ===

        # Validation hints (help LLM avoid brute force attempts)
        if node.attributes.get("required"):
            parts.append("required")
        if node.attributes.get("pattern"):
            parts.append(f'pattern="{node.attributes["pattern"][:20]}..."')
        if node.attributes.get("min"):
            parts.append(f"min={node.attributes['min']}")
        if node.attributes.get("max"):
            parts.append(f"max={node.attributes['max']}")
        if node.attributes.get("minlength"):
            parts.append(f"minlen={node.attributes['minlength']}")
        if node.attributes.get("maxlength"):
            parts.append(f"maxlen={node.attributes['maxlength']}")
        if node.attributes.get("step"):
            parts.append(f"step={node.attributes['step']}")

        # Input modes (virtual keyboard hints)
        if node.attributes.get("inputmode"):
            parts.append(f"inputmode={node.attributes['inputmode']}")
        if node.attributes.get("autocomplete"):
            ac = node.attributes["autocomplete"]
            if ac not in ("on", "off"):  # Only show meaningful values
                parts.append(f"autocomplete={ac}")

        # File input types
        if node.attributes.get("accept"):
            parts.append(f"accept={node.attributes['accept'][:20]}")
        if node.attributes.get("multiple"):
            parts.append("multiple")

        # Test identifiers (useful for debugging and recognition)
        for test_attr in ["data-testid", "data-cy", "data-test", "data-selenium"]:
            if node.attributes.get(test_attr):
                parts.append(f'{test_attr}="{node.attributes[test_attr]}"')
                break  # Only show first found

        # Disabled/readonly state
        if node.attributes.get("disabled") is not None:
            parts.append("disabled")
        if node.attributes.get("readonly") is not None:
            parts.append("readonly")

        return " ".join(parts)


class SerializedDOM(BaseModel):
    """Serialized DOM state for LLM."""

    text: str = ""
    selector_map: dict[int, dict] = Field(default_factory=dict)
    element_count: int = 0
    scroll_info: dict[str, float] = Field(default_factory=dict)
