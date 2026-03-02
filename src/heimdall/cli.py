"""
Heimdall CLI - Command line interface wrapper.

This wrapper ensures that if the required CLI dependencies are missing,
a clean error message is displayed to the user without a traceback.
"""

import sys


def app(*args, **kwargs):
    """CLI entry point wrapper that handles missing optional dependencies."""
    try:
        from heimdall.cli_impl import app as cli_app

        return cli_app(*args, **kwargs)
    except ImportError as e:
        # Check if the missing module is one of our strict CLI dependencies
        missing_module = str(e)
        if "typer" in missing_module or "dotenv" in missing_module or "rich" in missing_module:
            print(
                "ERROR: Heimdall CLI requires extra dependencies to run commands.", file=sys.stderr
            )
            print("Install them with: pip install 'heimdall[cli]'", file=sys.stderr)
            sys.exit(1)
        raise


def main() -> None:
    """Main entry point."""
    app()


if __name__ == "__main__":
    main()
