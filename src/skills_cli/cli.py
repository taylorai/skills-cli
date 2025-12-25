"""Command-line interface for skills-cli."""

import argparse
import io
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import yaml

from skills_cli import __version__

DEFAULT_SKILLS_DIRS = [
    Path.home() / ".claude" / "skills",
    Path.home() / ".codex" / "skills",
]


def validate_skill_name(name: str) -> tuple[bool, str | None]:
    """Validate a skill name according to the spec."""
    if not name:
        return False, "Name cannot be empty"
    if len(name) > 64:
        return False, "Name must be 64 characters or less"
    if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", name):
        return False, "Name must be lowercase alphanumeric with single hyphens, not starting/ending with hyphen"
    if "--" in name:
        return False, "Name cannot contain consecutive hyphens"
    return True, None


def parse_skill_md(path: Path) -> dict | None:
    """Parse SKILL.md and return frontmatter as dict."""
    if not path.exists():
        return None
    content = path.read_text()
    if not content.startswith("---"):
        return None
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        return yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None


def validate_skill_dir(skill_path: Path) -> tuple[bool, str | None]:
    """Validate a skill directory."""
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return False, f"SKILL.md not found in {skill_path}"

    frontmatter = parse_skill_md(skill_md)
    if frontmatter is None:
        return False, "Invalid or missing YAML frontmatter in SKILL.md"

    if "name" not in frontmatter:
        return False, "Missing required 'name' field in frontmatter"
    if "description" not in frontmatter:
        return False, "Missing required 'description' field in frontmatter"

    valid, err = validate_skill_name(frontmatter["name"])
    if not valid:
        return False, f"Invalid skill name: {err}"

    # Check that name matches directory name
    if frontmatter["name"] != skill_path.name:
        return False, f"Skill name '{frontmatter['name']}' must match directory name '{skill_path.name}'"

    return True, None


# ============================================================================
# CREATE command
# ============================================================================


def cmd_create(args: argparse.Namespace) -> int:
    """Create a new skill scaffold."""
    name = args.name
    valid, err = validate_skill_name(name)
    if not valid:
        print(f"Error: {err}", file=sys.stderr)
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

# {name.replace('-', ' ').title()}

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
    if (path / "SKILL.md").exists():
        skills.append(path)
    # Check subdirectories (up to 2 levels deep)
    for subdir in path.iterdir():
        if subdir.is_dir() and (subdir / "SKILL.md").exists():
            skills.append(subdir)
        elif subdir.is_dir():
            for subsubdir in subdir.iterdir():
                if subsubdir.is_dir() and (subsubdir / "SKILL.md").exists():
                    skills.append(subsubdir)
    return skills


def install_skill(skill_path: Path, dest_dir: Path) -> tuple[bool, str]:
    """Install a single skill to destination."""
    valid, err = validate_skill_dir(skill_path)
    if not valid:
        return False, err

    frontmatter = parse_skill_md(skill_path / "SKILL.md")
    skill_name = frontmatter["name"]
    target = dest_dir / skill_name

    if target.exists():
        shutil.rmtree(target)

    shutil.copytree(skill_path, target)
    return True, f"Installed {skill_name} to {target}"


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
    if source_path.suffix == ".zip" or (source_path.is_file() and zipfile.is_zipfile(source_path)):
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
        print(f"Error: Source must be a directory or zip file: {source_path}", file=sys.stderr)
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
        print("Error: Invalid GitHub URL. Expected format: github.com/owner/repo", file=sys.stderr)
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
        with urllib.request.urlopen(zip_url) as response:
            zip_data = response.read()
    except urllib.error.HTTPError as e:
        # Try 'master' branch if 'main' fails
        if branch == "main":
            zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/master.zip"
            try:
                with urllib.request.urlopen(zip_url) as response:
                    zip_data = response.read()
                branch = "master"
            except urllib.error.HTTPError:
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
            print(f"Error: No skills found in repository", file=sys.stderr)
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

    valid, err = validate_skill_dir(skill_path)
    if not valid:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    frontmatter = parse_skill_md(skill_path / "SKILL.md")
    skill_name = frontmatter["name"]

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = Path.cwd() / f"{skill_name}.zip"

    # Create zip file
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in skill_path.rglob("*"):
            if file.is_file():
                arcname = f"{skill_name}/{file.relative_to(skill_path)}"
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

    valid, err = validate_skill_dir(skill_path)
    if not valid:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    frontmatter = parse_skill_md(skill_path / "SKILL.md")
    skill_name = frontmatter["name"]

    # Read SKILL.md content
    skill_md_content = (skill_path / "SKILL.md").read_text()

    # Collect all files in the skill
    files = {}
    for file in skill_path.rglob("*"):
        if file.is_file():
            rel_path = str(file.relative_to(skill_path))
            try:
                files[rel_path] = file.read_text()
            except UnicodeDecodeError:
                # Binary file - base64 encode it
                import base64

                files[rel_path] = f"base64:{base64.b64encode(file.read_bytes()).decode()}"

    # Build payload
    payload = {
        "name": skill_name,
        "description": frontmatter.get("description", ""),
        "content": skill_md_content,
        "files": files,
    }

    # Check if skill exists first (GET request)
    base_url = os.environ.get("ANTHROPIC_API_URL", "https://api.anthropic.com")
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    # Try to create or update
    url = f"{base_url}/v1/skills"
    method = "POST"

    if args.update:
        url = f"{base_url}/v1/skills/{skill_name}"
        method = "PUT"

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode())
            action = "Updated" if args.update else "Created"
            print(f"{action} skill: {skill_name}")
            if "id" in result:
                print(f"Skill ID: {result['id']}")
            return 0
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        try:
            error_json = json.loads(error_body)
            error_msg = error_json.get("error", {}).get("message", error_body)
        except json.JSONDecodeError:
            error_msg = error_body

        if e.code == 409 and not args.update:
            print(f"Skill '{skill_name}' already exists. Use --update to update it.", file=sys.stderr)
        else:
            print(f"Error: {e.code} - {error_msg}", file=sys.stderr)
        return 1


# ============================================================================
# VALIDATE command
# ============================================================================


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate a skill directory."""
    skill_path = Path(args.skill_path).resolve()

    valid, err = validate_skill_dir(skill_path)
    if not valid:
        print(f"Invalid: {err}", file=sys.stderr)
        return 1

    frontmatter = parse_skill_md(skill_path / "SKILL.md")
    print(f"Valid skill: {frontmatter['name']}")
    print(f"Description: {frontmatter['description'][:100]}...")
    return 0


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

        skills = [d for d in skills_dir.iterdir() if d.is_dir() and (d / "SKILL.md").exists()]
        if not skills:
            continue

        found_any = True
        print(f"\n{skills_dir}:")
        for skill in sorted(skills):
            frontmatter = parse_skill_md(skill / "SKILL.md")
            if frontmatter:
                name = frontmatter.get("name", skill.name)
                desc = frontmatter.get("description", "No description")[:60]
                print(f"  {name}: {desc}")

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
    create_parser.add_argument("name", help="Name of the skill (lowercase, hyphens allowed)")
    create_parser.add_argument("--path", "-p", help="Directory to create skill in (default: current directory)")

    # install
    install_parser = subparsers.add_parser("install", help="Install a skill from local path, zip, or GitHub")
    install_parser.add_argument("source", help="Local path, zip file, or GitHub URL")
    install_parser.add_argument("--dest", "-d", help="Destination directory (default: ~/.claude/skills)")
    install_parser.add_argument("--subpath", "-s", help="Subpath within repo to search for skills")

    # zip
    zip_parser = subparsers.add_parser("zip", help="Package a skill into a zip file")
    zip_parser.add_argument("skill_path", help="Path to the skill directory")
    zip_parser.add_argument("--output", "-o", help="Output zip file path (default: <skill-name>.zip)")

    # push
    push_parser = subparsers.add_parser("push", help="Push a skill to Anthropic API")
    push_parser.add_argument("skill_path", help="Path to the skill directory")
    push_parser.add_argument("--update", "-u", action="store_true", help="Update existing skill instead of creating")

    # validate
    validate_parser = subparsers.add_parser("validate", help="Validate a skill directory")
    validate_parser.add_argument("skill_path", help="Path to the skill directory")

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
        "list": cmd_list,
    }

    sys.exit(commands[args.command](args))


if __name__ == "__main__":
    main()
