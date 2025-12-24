"""
Heimdall CLI - Command line interface.

Usage:
    heimdall run "Login and check dashboard" --url https://example.com
    heimdall run task.yaml --output ./results
"""

import asyncio
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console

# Load .env file if present
load_dotenv()

app = typer.Typer(
    name="heimdall",
    help="LLM-powered browser automation agent",
    add_completion=False,
)

console = Console()


@app.command()
def run(
    task: str = typer.Argument(..., help="Task description or path to YAML file"),
    url: str | None = typer.Option(None, "--url", "-u", help="Starting URL"),
    output: str = typer.Option("./output", "--output", "-o", help="Output directory"),
    headed: bool = typer.Option(False, "--headed", help="Run with visible browser"),
    llm: str = typer.Option(
        "openrouter", "--llm", "-l", help="LLM provider (openrouter/openai/anthropic)"
    ),
    model: str = typer.Option(None, "--model", "-m", help="LLM model name"),
    demo: bool = typer.Option(False, "--demo", help="Enable demo mode with visual feedback"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
) -> None:
    """Run browser automation task."""
    from heimdall.logging import setup_logging

    # Setup logging
    level = "DEBUG" if verbose else "INFO"
    setup_logging(level=level)

    console.print("[bold]Heimdall[/bold] - Browser Automation Agent")
    console.print(f"Task: {task[:80]}{'...' if len(task) > 80 else ''}")

    if url:
        console.print(f"URL: {url}")

    # Check if task is a file
    task_path = Path(task)
    if task_path.exists() and task_path.suffix in [".yaml", ".yml", ".json"]:
        task_content = _load_task_file(task_path)
        console.print(f"Loaded task from: {task_path}")
    else:
        task_content = task

    # Run agent
    try:
        result = asyncio.run(
            _run_agent(
                task=task_content,
                url=url,
                output_dir=output,
                headless=not headed,
                llm_provider=llm,
                model=model,
                demo_mode=demo,
            )
        )

        if result.success:
            console.print("[green]✓ Task completed successfully[/green]")
        else:
            console.print(f"[red]✗ Task failed: {result.error}[/red]")
            raise typer.Exit(1)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


async def _run_agent(
    task: str,
    url: str | None,
    output_dir: str,
    headless: bool,
    llm_provider: str,
    model: str | None,
    demo_mode: bool,
):
    """Run the agent with given configuration."""
    from heimdall.agent import Agent, AgentConfig
    from heimdall.agent.llm import AnthropicLLM, OpenAILLM
    from heimdall.browser import BrowserConfig, BrowserSession
    from heimdall.dom import DomService
    from heimdall.tools import registry

    # Explicitly import actions to register them with the registry
    from heimdall.tools import actions as _  # noqa: F401

    print(f"Registered {len(registry.schema())} actions")  # Debug

    # Setup LLM
    if llm_provider == "anthropic":
        llm = AnthropicLLM(model=model or "claude-3-5-sonnet-20241022")
    elif llm_provider == "openrouter":
        from heimdall.agent.llm import OpenRouterLLM

        llm = OpenRouterLLM(model=model or "anthropic/claude-3.5-sonnet")
    else:
        llm = OpenAILLM(model=model or "gpt-4")

    # Setup browser with temp profile to avoid conflicts with running Chrome
    import tempfile

    temp_dir = tempfile.mkdtemp(prefix="heimdall_chrome_")
    config = BrowserConfig(headless=headless, user_data_dir=temp_dir)
    session = BrowserSession(config=config)

    try:
        await session.start()

        # Navigate to URL if provided
        if url:
            await session.navigate(url)

        # Setup agent
        dom_service = DomService(session)
        agent = Agent(
            session=session,
            dom_service=dom_service,
            registry=registry,
            llm_client=llm,
            config=AgentConfig(),
        )

        # Run task
        result = await agent.run(task)

        # Export results
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        # Note: In real usage, we'd integrate Collector during execution

        return result

    finally:
        await session.stop()
        await llm.close()


def _load_task_file(path: Path) -> str:
    """Load task from YAML or JSON file."""
    import json

    import yaml

    content = path.read_text()

    data = yaml.safe_load(content) if path.suffix in [".yaml", ".yml"] else json.loads(content)

    # Extract task description
    if isinstance(data, dict):
        return data.get("task", data.get("description", str(data)))
    elif isinstance(data, list):
        return "\n".join(str(item) for item in data)
    else:
        return str(data)


@app.command()
def version() -> None:
    """Show version information."""
    console.print("Heimdall v0.1.0")


@app.command()
def init(
    directory: str = typer.Argument(".", help="Directory to initialize"),
) -> None:
    """Initialize Heimdall workspace."""
    workspace = Path(directory)
    workspace.mkdir(parents=True, exist_ok=True)

    # Create sample config
    config_path = workspace / "heimdall.yaml"
    if not config_path.exists():
        config_path.write_text("""# Heimdall Configuration
browser:
  headless: true
  timeout: 30

llm:
  provider: openai
  model: gpt-4

output:
  screenshots: true
  network: true
""")
        console.print(f"Created: {config_path}")

    # Create sample task
    task_path = workspace / "task.yaml"
    if not task_path.exists():
        task_path.write_text("""# Sample Heimdall Task
name: Example Login
url: https://example.com/login

steps:
  - Enter email into the email field
  - Enter password into the password field
  - Click the login button
  - Verify dashboard is displayed
""")
        console.print(f"Created: {task_path}")

    console.print("[green]✓ Workspace initialized[/green]")


def main() -> None:
    """Main entry point."""
    app()


if __name__ == "__main__":
    main()
