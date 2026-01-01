"""Command-line interface for skills-cli.

Validation and prompt generation logic adapted from skills-ref
(https://github.com/agentskills/agentskills), licensed under Apache 2.0.
Copyright Anthropic, PBC.
"""

import argparse
import base64
import html
import io
import json
import os
import shutil
import sys
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx  # type: ignore
import yaml  # type: ignore

from skills_cli import __version__

DEFAULT_SKILLS_DIRS = [
    Path.home() / ".claude" / "skills",
    Path.home() / ".codex" / "skills",
]

# Validation constants per Agent Skills Spec
MAX_SKILL_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_COMPATIBILITY_LENGTH = 500

ALLOWED_FIELDS = {
    "name",
    "description",
    "license",
    "allowed-tools",
    "metadata",
    "compatibility",
}


# ============================================================================
# Data Models
# ============================================================================


@dataclass
class SkillProperties:
    """Properties parsed from a skill's SKILL.md frontmatter."""

    name: str
    description: str
    license: Optional[str] = None
    compatibility: Optional[str] = None
    allowed_tools: Optional[str] = None
    metadata: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary, excluding None values."""
        result: dict[str, Any] = {"name": self.name, "description": self.description}
        if self.license is not None:
            result["license"] = self.license
        if self.compatibility is not None:
            result["compatibility"] = self.compatibility
        if self.allowed_tools is not None:
            result["allowed-tools"] = self.allowed_tools
        if self.metadata:
            result["metadata"] = self.metadata
        return result


# ============================================================================
# Parsing
# ============================================================================


def find_skill_md(skill_dir: Path) -> Path | None:
    """Find SKILL.md file in a skill directory (case-insensitive)."""
    for name in ("SKILL.md", "skill.md"):
        path = skill_dir / name
        if path.exists():
            return path
    return None


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from SKILL.md content.

    Returns:
        Tuple of (metadata dict, markdown body)

    Raises:
        ValueError: If frontmatter is missing or invalid
    """
    if not content.startswith("---"):
        raise ValueError("SKILL.md must start with YAML frontmatter (---)")

    parts = content.split("---", 2)
    if len(parts) < 3:
        raise ValueError("SKILL.md frontmatter not properly closed with ---")

    frontmatter_str = parts[1]
    body = parts[2].strip()

    try:
        metadata = yaml.safe_load(frontmatter_str)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in frontmatter: {e}")

    if not isinstance(metadata, dict):
        raise ValueError("SKILL.md frontmatter must be a YAML mapping")

    # Ensure metadata values are strings
    if "metadata" in metadata and isinstance(metadata["metadata"], dict):
        metadata["metadata"] = {str(k): str(v) for k, v in metadata["metadata"].items()}

    return metadata, body


def read_properties(skill_dir: Path) -> SkillProperties:
    """Read skill properties from SKILL.md frontmatter.

    Args:
        skill_dir: Path to the skill directory

    Returns:
        SkillProperties with parsed metadata

    Raises:
        ValueError: If SKILL.md is missing or has invalid content
    """
    skill_md = find_skill_md(skill_dir)
    if skill_md is None:
        raise ValueError(f"SKILL.md not found in {skill_dir}")

    content = skill_md.read_text()
    metadata, _ = parse_frontmatter(content)

    if "name" not in metadata:
        raise ValueError("Missing required field in frontmatter: name")
    if "description" not in metadata:
        raise ValueError("Missing required field in frontmatter: description")

    name = metadata["name"]
    description = metadata["description"]

    if not isinstance(name, str) or not name.strip():
        raise ValueError("Field 'name' must be a non-empty string")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("Field 'description' must be a non-empty string")

    return SkillProperties(
        name=name.strip(),
        description=description.strip(),
        license=metadata.get("license"),
        compatibility=metadata.get("compatibility"),
        allowed_tools=metadata.get("allowed-tools"),
        metadata=metadata.get("metadata") or {},
    )


# ============================================================================
# Validation
# ============================================================================


def _validate_name(name: str, skill_dir: Path | None) -> list[str]:
    """Validate skill name format and directory match."""
    errors = []

    if not name or not isinstance(name, str) or not name.strip():
        errors.append("Field 'name' must be a non-empty string")
        return errors

    name = unicodedata.normalize("NFKC", name.strip())

    if len(name) > MAX_SKILL_NAME_LENGTH:
        errors.append(
            f"Skill name '{name}' exceeds {MAX_SKILL_NAME_LENGTH} character limit "
            f"({len(name)} chars)"
        )

    if name != name.lower():
        errors.append(f"Skill name '{name}' must be lowercase")

    if name.startswith("-") or name.endswith("-"):
        errors.append("Skill name cannot start or end with a hyphen")

    if "--" in name:
        errors.append("Skill name cannot contain consecutive hyphens")

    if not all(c.isalnum() or c == "-" for c in name):
        errors.append(
            f"Skill name '{name}' contains invalid characters. "
            "Only letters, digits, and hyphens are allowed."
        )

    if skill_dir:
        dir_name = unicodedata.normalize("NFKC", skill_dir.name)
        if dir_name != name:
            errors.append(
                f"Directory name '{skill_dir.name}' must match skill name '{name}'"
            )

    return errors


def _validate_description(description: str) -> list[str]:
    """Validate description format."""
    errors = []

    if not description or not isinstance(description, str) or not description.strip():
        errors.append("Field 'description' must be a non-empty string")
        return errors

    if len(description) > MAX_DESCRIPTION_LENGTH:
        errors.append(
            f"Description exceeds {MAX_DESCRIPTION_LENGTH} character limit "
            f"({len(description)} chars)"
        )

    return errors


def _validate_compatibility(compatibility: str) -> list[str]:
    """Validate compatibility format."""
    errors = []

    if not isinstance(compatibility, str):
        errors.append("Field 'compatibility' must be a string")
        return errors

    if len(compatibility) > MAX_COMPATIBILITY_LENGTH:
        errors.append(
            f"Compatibility exceeds {MAX_COMPATIBILITY_LENGTH} character limit "
            f"({len(compatibility)} chars)"
        )

    return errors


def _validate_allowed_fields(metadata: dict) -> list[str]:
    """Validate that only allowed fields are present."""
    errors = []
    extra_fields = set(metadata.keys()) - ALLOWED_FIELDS
    if extra_fields:
        errors.append(
            f"Unexpected fields in frontmatter: {', '.join(sorted(extra_fields))}. "
            f"Only {sorted(ALLOWED_FIELDS)} are allowed."
        )
    return errors


def validate(skill_dir: Path) -> list[str]:
    """Validate a skill directory.

    Args:
        skill_dir: Path to the skill directory

    Returns:
        List of validation error messages. Empty list means valid.
    """
    skill_dir = Path(skill_dir)

    if not skill_dir.exists():
        return [f"Path does not exist: {skill_dir}"]

    if not skill_dir.is_dir():
        return [f"Not a directory: {skill_dir}"]

    skill_md = find_skill_md(skill_dir)
    if skill_md is None:
        return ["Missing required file: SKILL.md"]

    try:
        content = skill_md.read_text()
        metadata, _ = parse_frontmatter(content)
    except ValueError as e:
        return [str(e)]

    errors = []
    errors.extend(_validate_allowed_fields(metadata))

    if "name" not in metadata:
        errors.append("Missing required field in frontmatter: name")
    else:
        errors.extend(_validate_name(metadata["name"], skill_dir))

    if "description" not in metadata:
        errors.append("Missing required field in frontmatter: description")
    else:
        errors.extend(_validate_description(metadata["description"]))

    if "compatibility" in metadata:
        errors.extend(_validate_compatibility(metadata["compatibility"]))

    return errors


# ============================================================================
# Prompt Generation
# ============================================================================


def _build_skill_data(skill_dirs: list[Path]) -> list[dict[str, str]]:
    """Build skill data list for prompt generation."""
    skills = []
    for skill_dir in skill_dirs:
        skill_dir = Path(skill_dir).resolve()
        props = read_properties(skill_dir)
        skill_md_path = find_skill_md(skill_dir)
        skills.append(
            {
                "name": props.name,
                "description": props.description,
                "location": str(skill_md_path),
            }
        )
    return skills


def to_prompt(skill_dirs: list[Path], fmt: str = "xml") -> str:
    """Generate available skills block for agent prompts.

    Args:
        skill_dirs: List of paths to skill directories
        fmt: Output format - "xml", "yaml", or "json"

    Returns:
        Formatted string with available skills
    """
    skills = _build_skill_data(skill_dirs)

    if fmt == "json":
        return json.dumps({"available_skills": skills}, indent=2)

    if fmt == "yaml":
        return yaml.dump(
            {"available_skills": skills}, default_flow_style=False, sort_keys=False
        )

    # Default: XML format
    if not skills:
        return "<available_skills>\n</available_skills>"

    lines = ["<available_skills>"]
    for skill in skills:
        lines.append("<skill>")
        lines.append("<name>")
        lines.append(html.escape(skill["name"]))
        lines.append("</name>")
        lines.append("<description>")
        lines.append(html.escape(skill["description"]))
        lines.append("</description>")
        lines.append("<location>")
        lines.append(skill["location"])
        lines.append("</location>")
        lines.append("</skill>")
    lines.append("</available_skills>")

    return "\n".join(lines)


# ============================================================================
# CREATE command
# ============================================================================


def cmd_create(args: argparse.Namespace) -> int:
    """Create a new skill scaffold."""
    name = args.name
    errors = _validate_name(name, None)
    if errors:
        print(f"Error: {errors[0]}", file=sys.stderr)
        return 1

    dest = Path(args.path) if args.path else Path.cwd()
    skill_dir = dest / name

    if skill_dir.exists():
        print(f"Error: Directory already exists: {skill_dir}", file=sys.stderr)
        return 1

    skill_dir.mkdir(parents=True)

    # Create SKILL.md
    skill_md_content = f"""---
name: {name}
description: TODO - describe what this skill does and when to use it.
---

# {name.replace("-", " ").title()}

## When to use this skill

TODO: Describe when an agent should use this skill.

## Instructions

TODO: Add step-by-step instructions for the agent.
"""
    (skill_dir / "SKILL.md").write_text(skill_md_content)

    # Create optional directories
    (skill_dir / "scripts").mkdir()
    (skill_dir / "references").mkdir()
    (skill_dir / "assets").mkdir()

    print(f"Created skill scaffold: {skill_dir}")
    return 0


# ============================================================================
# INSTALL command
# ============================================================================


def find_skills_in_dir(path: Path) -> list[Path]:
    """Find all skill directories (containing SKILL.md) in a path."""
    skills = []
    # Check if this is a skill itself
    if find_skill_md(path):
        skills.append(path)
    # Check subdirectories (up to 2 levels deep)
    for subdir in path.iterdir():
        if subdir.is_dir() and find_skill_md(subdir):
            skills.append(subdir)
        elif subdir.is_dir():
            for subsubdir in subdir.iterdir():
                if subsubdir.is_dir() and find_skill_md(subsubdir):
                    skills.append(subsubdir)
    return skills


def install_skill(skill_path: Path, dest_dir: Path) -> tuple[bool, str]:
    """Install a single skill to destination."""
    errors = validate(skill_path)
    if errors:
        return False, errors[0]

    props = read_properties(skill_path)
    target = dest_dir / props.name

    if target.exists():
        shutil.rmtree(target)

    shutil.copytree(skill_path, target)
    return True, f"Installed {props.name} to {target}"


def cmd_install(args: argparse.Namespace) -> int:
    """Install a skill from local path, zip, or GitHub URL."""
    source = args.source

    # Determine destination
    if args.dest:
        dest_dir = Path(args.dest)
    else:
        # Use first default that exists or create ~/.claude/skills
        dest_dir = None
        for d in DEFAULT_SKILLS_DIRS:
            if d.exists():
                dest_dir = d
                break
        if dest_dir is None:
            dest_dir = DEFAULT_SKILLS_DIRS[0]

    dest_dir.mkdir(parents=True, exist_ok=True)

    # Handle GitHub URL
    if source.startswith("https://github.com/") or source.startswith("github.com/"):
        return install_from_github(source, dest_dir, args.subpath)

    source_path = Path(source)

    # Handle zip file
    if source_path.suffix == ".zip" or (
        source_path.is_file() and zipfile.is_zipfile(source_path)
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            with zipfile.ZipFile(source_path, "r") as zf:
                zf.extractall(tmppath)

            skills = find_skills_in_dir(tmppath)
            if not skills:
                print("Error: No skills found in zip file", file=sys.stderr)
                return 1

            for skill in skills:
                ok, msg = install_skill(skill, dest_dir)
                if ok:
                    print(msg)
                else:
                    print(f"Error: {msg}", file=sys.stderr)
            return 0

    # Handle local directory
    if not source_path.exists():
        print(f"Error: Source not found: {source_path}", file=sys.stderr)
        return 1

    if not source_path.is_dir():
        print(
            f"Error: Source must be a directory or zip file: {source_path}",
            file=sys.stderr,
        )
        return 1

    skills = find_skills_in_dir(source_path)
    if not skills:
        print(f"Error: No skills found in {source_path}", file=sys.stderr)
        return 1

    for skill in skills:
        ok, msg = install_skill(skill, dest_dir)
        if ok:
            print(msg)
        else:
            print(f"Error: {msg}", file=sys.stderr)

    return 0


def install_from_github(url: str, dest_dir: Path, subpath: str | None) -> int:
    """Install skill(s) from a GitHub repository."""
    # Parse GitHub URL
    url = url.replace("github.com/", "").replace("https://", "").replace("http://", "")
    parts = url.rstrip("/").split("/")

    if len(parts) < 2:
        print(
            "Error: Invalid GitHub URL. Expected format: github.com/owner/repo",
            file=sys.stderr,
        )
        return 1

    owner, repo = parts[0], parts[1]

    # Handle tree/branch/path in URL
    branch = "main"
    url_subpath = None
    if len(parts) > 2 and parts[2] == "tree":
        branch = parts[3] if len(parts) > 3 else "main"
        if len(parts) > 4:
            url_subpath = "/".join(parts[4:])

    # Use subpath from arg or URL
    effective_subpath = subpath or url_subpath

    # Download repo as zip
    zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
    print(f"Downloading {owner}/{repo}...")

    try:
        response = httpx.get(zip_url, follow_redirects=True)
        response.raise_for_status()
        zip_data = response.content
    except httpx.HTTPStatusError as e:
        # Try 'master' branch if 'main' fails
        if branch == "main":
            zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/master.zip"
            try:
                response = httpx.get(zip_url, follow_redirects=True)
                response.raise_for_status()
                zip_data = response.content
                branch = "master"
            except httpx.HTTPStatusError:
                print(f"Error: Could not download repository: {e}", file=sys.stderr)
                return 1
        else:
            print(f"Error: Could not download repository: {e}", file=sys.stderr)
            return 1

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            zf.extractall(tmppath)

        # GitHub zips extract to repo-branch/ directory
        extracted_dir = tmppath / f"{repo}-{branch}"
        if not extracted_dir.exists():
            # Try to find the extracted directory
            dirs = [d for d in tmppath.iterdir() if d.is_dir()]
            if dirs:
                extracted_dir = dirs[0]
            else:
                print("Error: Could not find extracted repository", file=sys.stderr)
                return 1

        # Apply subpath if specified
        search_dir = extracted_dir
        if effective_subpath:
            search_dir = extracted_dir / effective_subpath
            if not search_dir.exists():
                print(f"Error: Subpath not found: {effective_subpath}", file=sys.stderr)
                return 1

        skills = find_skills_in_dir(search_dir)
        if not skills:
            print("Error: No skills found in repository", file=sys.stderr)
            return 1

        for skill in skills:
            ok, msg = install_skill(skill, dest_dir)
            if ok:
                print(msg)
            else:
                print(f"Error: {msg}", file=sys.stderr)

    return 0


# ============================================================================
# ZIP command
# ============================================================================


def cmd_zip(args: argparse.Namespace) -> int:
    """Package a skill into a zip file."""
    skill_path = Path(args.skill_path).resolve()

    errors = validate(skill_path)
    if errors:
        print(f"Error: {errors[0]}", file=sys.stderr)
        return 1

    props = read_properties(skill_path)

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = Path.cwd() / f"{props.name}.zip"

    # Create zip file
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in skill_path.rglob("*"):
            if file.is_file():
                arcname = f"{props.name}/{file.relative_to(skill_path)}"
                zf.write(file, arcname)

    print(f"Created: {output_path}")
    return 0


# ============================================================================
# PUSH command
# ============================================================================


def cmd_push(args: argparse.Namespace) -> int:
    """Push a skill to Anthropic's /skills endpoint."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY environment variable not set", file=sys.stderr)
        return 1

    skill_path = Path(args.skill_path).resolve()

    errors = validate(skill_path)
    if errors:
        print(f"Error: {errors[0]}", file=sys.stderr)
        return 1

    props = read_properties(skill_path)
    skill_md = find_skill_md(skill_path)

    # Read SKILL.md content
    assert skill_md is not None, "skill not found"
    skill_md_content = skill_md.read_text()

    # Collect all files in the skill
    files = {}
    for file in skill_path.rglob("*"):
        if file.is_file():
            rel_path = str(file.relative_to(skill_path))
            try:
                files[rel_path] = file.read_text()
            except UnicodeDecodeError:
                # Binary file - base64 encode it
                files[rel_path] = (
                    f"base64:{base64.b64encode(file.read_bytes()).decode()}"
                )

    # Build payload
    payload = {
        "name": props.name,
        "description": props.description,
        "content": skill_md_content,
        "files": files,
    }

    # Build request
    base_url = os.environ.get("ANTHROPIC_API_URL", "https://api.anthropic.com")
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "skills-2025-10-02",
        "content-type": "application/json",
    }

    # Try to create or update
    url = f"{base_url}/v1/skills"
    if args.update:
        url = f"{base_url}/v1/skills/{props.name}"

    try:
        if args.update:
            response = httpx.put(url, json=payload, headers=headers)
        else:
            response = httpx.post(url, json=payload, headers=headers)

        response.raise_for_status()
        result = response.json()
        action = "Updated" if args.update else "Created"
        print(f"{action} skill: {props.name}")
        if "id" in result:
            print(f"Skill ID: {result['id']}")
        return 0
    except httpx.HTTPStatusError as e:
        try:
            error_json = e.response.json()
            error_msg = error_json.get("error", {}).get("message", e.response.text)
        except (json.JSONDecodeError, ValueError):
            error_msg = e.response.text

        if e.response.status_code == 409 and not args.update:
            print(
                f"Skill '{props.name}' already exists. Use --update to update it.",
                file=sys.stderr,
            )
        else:
            print(f"Error: {e.response.status_code} - {error_msg}", file=sys.stderr)
        return 1


# ============================================================================
# VALIDATE command
# ============================================================================


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate a skill directory."""
    skill_path = Path(args.skill_path).resolve()

    # Handle if user passes SKILL.md directly
    if skill_path.is_file() and skill_path.name.lower() == "skill.md":
        skill_path = skill_path.parent

    errors = validate(skill_path)

    if errors:
        print(f"Validation failed for {skill_path}:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"Valid skill: {skill_path}")
    return 0


# ============================================================================
# READ-PROPERTIES command
# ============================================================================


def cmd_read_properties(args: argparse.Namespace) -> int:
    """Read and print skill properties as JSON."""
    skill_path = Path(args.skill_path).resolve()

    # Handle if user passes SKILL.md directly
    if skill_path.is_file() and skill_path.name.lower() == "skill.md":
        skill_path = skill_path.parent

    try:
        props = read_properties(skill_path)
        print(json.dumps(props.to_dict(), indent=2))
        return 0
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


# ============================================================================
# TO-PROMPT command
# ============================================================================


def cmd_to_prompt(args: argparse.Namespace) -> int:
    """Generate available skills for agent prompts."""
    skill_paths = []
    for path_str in args.skill_paths:
        path = Path(path_str).resolve()
        # Handle if user passes SKILL.md directly
        if path.is_file() and path.name.lower() == "skill.md":
            path = path.parent
        skill_paths.append(path)

    fmt = getattr(args, "format", "xml")

    try:
        output = to_prompt(skill_paths, fmt=fmt)
        print(output)
        return 0
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


# ============================================================================
# LIST command
# ============================================================================


def cmd_list(args: argparse.Namespace) -> int:
    """List installed skills."""
    found_any = False

    dirs_to_check = DEFAULT_SKILLS_DIRS
    if args.path:
        dirs_to_check = [Path(args.path)]

    for skills_dir in dirs_to_check:
        if not skills_dir.exists():
            continue

        skills = [d for d in skills_dir.iterdir() if d.is_dir() and find_skill_md(d)]
        if not skills:
            continue

        found_any = True
        print(f"\n{skills_dir}:")
        for skill in sorted(skills):
            try:
                props = read_properties(skill)
                desc = props.description[:60]
                print(f"  {props.name}: {desc}")
            except ValueError:
                print(f"  {skill.name}: (invalid skill)")

    if not found_any:
        print("No skills installed.")

    return 0


# ============================================================================
# Main CLI
# ============================================================================


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="skills",
        description="CLI tool for managing Agent Skills.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # create
    create_parser = subparsers.add_parser("create", help="Create a new skill scaffold")
    create_parser.add_argument(
        "name", help="Name of the skill (lowercase, hyphens allowed)"
    )
    create_parser.add_argument(
        "--path", "-p", help="Directory to create skill in (default: current directory)"
    )

    # install
    install_parser = subparsers.add_parser(
        "install", help="Install a skill from local path, zip, or GitHub"
    )
    install_parser.add_argument("source", help="Local path, zip file, or GitHub URL")
    install_parser.add_argument(
        "--dest", "-d", help="Destination directory (default: ~/.claude/skills)"
    )
    install_parser.add_argument(
        "--subpath", "-s", help="Subpath within repo to search for skills"
    )

    # zip
    zip_parser = subparsers.add_parser("zip", help="Package a skill into a zip file")
    zip_parser.add_argument("skill_path", help="Path to the skill directory")
    zip_parser.add_argument(
        "--output", "-o", help="Output zip file path (default: <skill-name>.zip)"
    )

    # push
    push_parser = subparsers.add_parser("push", help="Push a skill to Anthropic API")
    push_parser.add_argument("skill_path", help="Path to the skill directory")
    push_parser.add_argument(
        "--update",
        "-u",
        action="store_true",
        help="Update existing skill instead of creating",
    )

    # validate
    validate_parser = subparsers.add_parser(
        "validate", help="Validate a skill directory"
    )
    validate_parser.add_argument(
        "skill_path", help="Path to the skill directory or SKILL.md file"
    )

    # read-properties
    read_props_parser = subparsers.add_parser(
        "read-properties", help="Read skill properties as JSON"
    )
    read_props_parser.add_argument(
        "skill_path", help="Path to the skill directory or SKILL.md file"
    )

    # to-prompt
    to_prompt_parser = subparsers.add_parser(
        "to-prompt", help="Generate available skills for agent prompts"
    )
    to_prompt_parser.add_argument(
        "skill_paths", nargs="+", help="Paths to skill directories"
    )
    to_prompt_parser.add_argument(
        "--format",
        "-f",
        choices=["xml", "yaml", "json"],
        default="xml",
        help="Output format (default: xml)",
    )

    # list
    list_parser = subparsers.add_parser("list", help="List installed skills")
    list_parser.add_argument("--path", "-p", help="Specific skills directory to list")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    commands = {
        "create": cmd_create,
        "install": cmd_install,
        "zip": cmd_zip,
        "push": cmd_push,
        "validate": cmd_validate,
        "read-properties": cmd_read_properties,
        "to-prompt": cmd_to_prompt,
        "list": cmd_list,
    }

    sys.exit(commands[args.command](args))


if __name__ == "__main__":
    main()
