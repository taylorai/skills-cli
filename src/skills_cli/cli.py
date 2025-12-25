"""Command-line interface for skills-cli."""

import argparse

from skills_cli import __version__


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="skills",
        description="A CLI tool for managing skills.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    args = parser.parse_args()

    # TODO: Implement CLI commands
    print("Hello from skills-cli!")


if __name__ == "__main__":
    main()
