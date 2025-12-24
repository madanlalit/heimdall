# Heimdall

This codebase is a browser automation agent that uses CDP (Chrome DevTools Protocol) 
to execute natural language tasks and collect context for automation script generation.

## Project Structure

```
src/heimdall/          # Main package
├── browser/         # CDP/browser management
├── dom/             # DOM extraction and serialization  
├── agent/           # LLM agent loop
├── tools/           # Browser actions (click, type, etc.)
├── collector/       # Context collection
└── models/          # Pydantic models
```

## Key Commands

```bash
# Install dependencies
uv sync

# Run CLI
uv run heimdall --help

# Run tests
uv run pytest

# Lint
uv run ruff check src/
```

## Tech Stack

- **cdp-use** - Type-safe CDP client
- **openai/anthropic** - LLM providers
- **pydantic** - Data models
- **typer** - CLI
