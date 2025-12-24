"""Entry point for LAN Transfer application."""

import sys


def main() -> int:
    """Main entry point for the application."""
    from lantransfer.app import run_app

    return run_app()


if __name__ == "__main__":
    sys.exit(main())





