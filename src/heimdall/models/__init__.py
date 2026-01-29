"""
Heimdall Models - Shared Pydantic models.

Input/output models for the Heimdall agent.
"""

from typing import Literal

from pydantic import BaseModel, Field

# ===== Input Models =====


class TaskInput(BaseModel):
    """Input for a single task."""

    task: str
    url: str | None = None
    timeout: float = 300.0
    max_steps: int = 50


class TestInput(BaseModel):
    """Input for a test with multiple steps."""

    name: str
    description: str = ""
    base_url: str
    steps: list[str] = Field(default_factory=list)

    timeout_per_step: float = 60.0
    max_retries: int = 3


# ===== Output Models =====


class SelectorSet(BaseModel):
    """Set of selectors for an element."""

    css: str | None = None
    xpath: str | None = None
    testid: str | None = None
    aria: str | None = None
    text: str | None = None
    placeholder: str | None = None
    name: str | None = None

    def best(self) -> str | None:
        """Return the most reliable selector."""
        # Priority order
        for sel in [self.testid, self.aria, self.css, self.xpath, self.text]:
            if sel:
                return sel
        return None


class ElementInfo(BaseModel):
    """Information about an element."""

    backend_node_id: int = 0
    tag: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)
    selectors: SelectorSet = Field(default_factory=SelectorSet)
    text: str = ""
    bounding_box: dict[str, float] | None = None


class ActionOutput(BaseModel):
    """Output for a single action."""

    tool: str
    params: dict = Field(default_factory=dict)
    success: bool = True
    message: str = ""
    error: str | None = None
    element: ElementInfo | None = None


class StepOutput(BaseModel):
    """Output for a test step."""

    id: str
    instruction: str
    status: Literal["passed", "failed", "skipped"] = "passed"

    actions: list[ActionOutput] = Field(default_factory=list)

    screenshots: dict[str, str] = Field(default_factory=dict)  # before/after paths
    network: list[dict] = Field(default_factory=list)

    duration_ms: float = 0
    error: str | None = None


class TestOutput(BaseModel):
    """Complete test output."""

    name: str
    status: Literal["passed", "failed", "error"] = "passed"

    steps: list[StepOutput] = Field(default_factory=list)

    duration_ms: float = 0
    start_time: str = ""
    end_time: str = ""

    error: str | None = None
    metadata: dict = Field(default_factory=dict)


# ===== Config Models =====


class BrowserOptions(BaseModel):
    """Browser configuration options."""

    headless: bool = True
    executable_path: str | None = None
    user_data_dir: str | None = None
    window_width: int = 1280
    window_height: int = 800

    timeout_navigation: float = 30.0
    timeout_action: float = 10.0


class LLMOptions(BaseModel):
    """LLM configuration options."""

    provider: Literal["openai", "anthropic", "openrouter", "groq"] = "openai"
    model: str = "gpt-4"
    api_key: str | None = None

    temperature: float = 0.0
    max_tokens: int = 4096


class HeimdallConfig(BaseModel):
    """Complete Heimdall configuration."""

    browser: BrowserOptions = Field(default_factory=BrowserOptions)
    llm: LLMOptions = Field(default_factory=LLMOptions)

    output_dir: str = "./output"
    capture_screenshots: bool = True
    capture_network: bool = True

    demo_mode: bool = False
    verbose: bool = False
