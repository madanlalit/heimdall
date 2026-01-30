"""
Heimdall CLI - Command line interface.

Usage:
    heimdall run "Login and check dashboard" --url https://example.com
    heimdall run task.yaml --output ./results
"""

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console

from heimdall.config import LLMProvider

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
    task: Annotated[str, typer.Argument(help="Task description or path to YAML file")],
    url: Annotated[str | None, typer.Option("--url", "-u", help="Starting URL")] = None,
    output: Annotated[str, typer.Option("--output", "-o", help="Output directory")] = "./output",
    headed: Annotated[bool, typer.Option("--headed", help="Run with visible browser")] = False,
    llm: Annotated[
        LLMProvider,
        typer.Option(
            "--llm",
            "-l",
            help="LLM provider (openrouter/openai/anthropic/groq)",
        ),
    ] = "openrouter",
    model: Annotated[str | None, typer.Option("--model", "-m", help="LLM model name")] = None,
    demo: Annotated[
        bool, typer.Option("--demo", help="Enable demo mode with visual feedback")
    ] = False,
    vision: Annotated[
        bool, typer.Option("--vision", help="Send screenshots to LLM for better context")
    ] = False,
    user_data_dir: Annotated[
        str | None,
        typer.Option(
            "--user-data-dir",
            help="Chrome user data directory (e.g., ~/Library/Application Support/Google/Chrome)",
        ),
    ] = None,
    profile_directory: Annotated[
        str,
        typer.Option(
            "--profile-directory",
            help="Chrome profile name (Default, Profile 1, etc.)",
        ),
    ] = "Default",
    instructions: Annotated[
        str | None,
        typer.Option(
            "--instructions",
            "-i",
            help="Path to file with custom instructions to extend the system prompt",
        ),
    ] = None,
    save_trace: Annotated[
        str | None,
        typer.Option(
            "--save-trace",
            help="Save execution trace to JSON file (e.g., trace.json)",
        ),
    ] = None,
    capture_screenshots: Annotated[
        bool,
        typer.Option(
            "--capture-screenshots",
            help="Capture screenshots at each step (requires --save-trace)",
        ),
    ] = False,
    collector: Annotated[
        bool,
        typer.Option(
            "--collector",
            help="Enable detailed step collector for export (requires --save-trace)",
        ),
    ] = False,
    run_id: Annotated[
        str | None,
        typer.Option(
            "--run-id",
            help=(
                "Resume from a specific paused run ID. Use without this flag to start a fresh run."
            ),
        ),
    ] = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Enable verbose logging")
    ] = False,
) -> None:
    """Run browser automation task."""
    from typing import Literal

    from heimdall.logging import setup_logging

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "DEBUG" if verbose else "INFO"
    setup_logging(level=level)

    console.print("[bold]Heimdall[/bold] - Browser Automation Agent")
    console.print(f"Task: {task[:80]}{'...' if len(task) > 80 else ''}")

    if url:
        console.print(f"URL: {url}")

    task_path = Path(task)
    if task_path.exists() and task_path.suffix in [".yaml", ".yml", ".json"]:
        task_content = _load_task_file(task_path)
        console.print(f"Loaded task from: {task_path}")
    else:
        task_content = task

    extend_system_prompt = None
    if instructions:
        instructions_path = Path(instructions)
        if instructions_path.exists():
            extend_system_prompt = instructions_path.read_text()
            console.print(f"Loaded instructions from: {instructions_path}")
        else:
            console.print(f"[yellow]Warning: Instructions file not found: {instructions}[/yellow]")

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
                use_vision=vision,
                user_data_dir=user_data_dir,
                profile_directory=profile_directory,
                extend_system_prompt=extend_system_prompt,
                save_trace=save_trace,
                capture_screenshots=capture_screenshots,
                use_collector=collector,
                run_id=run_id,
            )
        )

        if result.is_successful():
            console.print("[green]✓ Task completed successfully[/green]")
            console.print(f"Steps: {len(result)}")
            console.print(f"Duration: {result.total_duration_seconds():.2f}s")
        else:
            console.print("[red]✗ Task failed[/red]")
            if result.history and result.history[-1].results:
                error = result.history[-1].results[-1].error
                if error:
                    console.print(f"Error: {error}")
            raise typer.Exit(1)

    except KeyboardInterrupt:
        console.print("\n[yellow]Execution interrupted by user[/yellow]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None


async def _run_agent(
    task: str,
    url: str | None,
    output_dir: str,
    headless: bool,
    llm_provider: LLMProvider,
    model: str | None,
    demo_mode: bool,
    use_vision: bool = False,
    user_data_dir: str | None = None,
    profile_directory: str = "Default",
    extend_system_prompt: str | None = None,
    save_trace: str | None = None,
    capture_screenshots: bool = False,
    use_collector: bool = False,
    run_id: str | None = None,
):
    """Run the agent with given configuration."""
    from heimdall.agent import Agent, AgentConfig
    from heimdall.browser import BrowserConfig, BrowserSession
    from heimdall.dom import DomService
    from heimdall.logging import logger

    # Explicitly import actions to register them with the registry
    from heimdall.tools import actions as _  # noqa: F401
    from heimdall.tools import registry

    print(f"Registered {len(registry.schema())} actions")  # Debug

    from heimdall.agent.factory import create_llm_client

    llm = create_llm_client(provider=llm_provider, model=model)

    if user_data_dir:
        # Use existing Chrome profile with cookies
        expanded_dir = str(Path(user_data_dir).expanduser())
        config = BrowserConfig(
            headless=headless,
            user_data_dir=expanded_dir,
            profile_directory=profile_directory,
            disable_extensions=False,  # Keep extensions when using existing profile
        )
        logger.info(f"Using Chrome profile: {expanded_dir}/{profile_directory}")
    else:
        # Create temp profile to avoid conflicts with running Chrome
        import tempfile

        temp_dir = tempfile.mkdtemp(prefix="heimdall_chrome_")
        config = BrowserConfig(headless=headless, user_data_dir=temp_dir)
        logger.info(f"Using temp profile: {temp_dir}")

    session = BrowserSession(config=config)

    try:
        await session.start()

        if url:
            await session.navigate(url)

        # Extract allowed domains from URL (restrict to starting domain)
        allowed_domains: list[str] = []
        if url:
            from heimdall.utils.domain import extract_domain_from_url

            domain = extract_domain_from_url(url)
            if domain:
                allowed_domains = [domain, f"*.{domain}"]
                logger.info(f"Domain restriction: {allowed_domains}")

        dom_service = DomService(session)
        agent = Agent(
            session=session,
            dom_service=dom_service,
            registry=registry,
            llm_client=llm,
            config=AgentConfig(
                use_vision=use_vision,
                demo_mode=demo_mode,
                allowed_domains=allowed_domains,
                extend_system_prompt=extend_system_prompt,
                save_trace_path=save_trace,
                capture_screenshots=capture_screenshots,
                use_collector=use_collector,
                workspace_path=output_dir,
                enable_persistence=True,
                run_id=run_id,
            ),
        )

        result = await agent.run(task)

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

    if isinstance(data, dict):
        task_val = data.get("task") or data.get("description")
        return str(task_val) if task_val is not None else str(data)
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
    directory: Annotated[str, typer.Argument(help="Directory to initialize")] = ".",
) -> None:
    """Initialize Heimdall workspace."""
    workspace = Path(directory)
    workspace.mkdir(parents=True, exist_ok=True)

    workspace = Path(directory)
    workspace.mkdir(parents=True, exist_ok=True)

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

        console.print(f"Created: {config_path}")

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
