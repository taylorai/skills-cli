#!/usr/bin/env python3
"""Tests for skills-cli.

Run with: uv run python tests/test_skills.py
"""

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from skills_cli.cli import (
    SkillProperties,
    find_skill_md,
    find_skills_in_dir,
    parse_frontmatter,
    read_properties,
    to_prompt,
    validate,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestFindSkillMd(unittest.TestCase):
    """Tests for find_skill_md function."""

    def test_finds_uppercase_skill_md(self):
        """Should find SKILL.md (uppercase)."""
        path = find_skill_md(FIXTURES_DIR / "valid-skill")
        self.assertIsNotNone(path)
        assert path is not None
        self.assertEqual(path.name, "SKILL.md")

    def test_finds_lowercase_skill_md(self):
        """Should find skill.md (lowercase)."""
        path = find_skill_md(FIXTURES_DIR / "lowercase-skill-md")
        self.assertIsNotNone(path)
        assert path is not None
        # macOS has case-insensitive filesystem, so just check it's found
        self.assertIn(path.name.lower(), ("skill.md",))

    def test_returns_none_for_missing(self):
        """Should return None if no SKILL.md exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = find_skill_md(Path(tmpdir))
            self.assertIsNone(path)


class TestParseFrontmatter(unittest.TestCase):
    """Tests for parse_frontmatter function."""

    def test_parses_valid_frontmatter(self):
        """Should parse valid YAML frontmatter."""
        content = """---
name: test-skill
description: A test skill.
---

# Body
"""
        metadata, body = parse_frontmatter(content)
        self.assertEqual(metadata["name"], "test-skill")
        self.assertEqual(metadata["description"], "A test skill.")
        self.assertEqual(body, "# Body")

    def test_raises_on_missing_frontmatter(self):
        """Should raise ValueError if frontmatter is missing."""
        content = "# No frontmatter"
        with self.assertRaises(ValueError) as ctx:
            parse_frontmatter(content)
        self.assertIn("must start with YAML frontmatter", str(ctx.exception))

    def test_raises_on_unclosed_frontmatter(self):
        """Should raise ValueError if frontmatter is not closed."""
        content = """---
name: test
"""
        with self.assertRaises(ValueError) as ctx:
            parse_frontmatter(content)
        self.assertIn("not properly closed", str(ctx.exception))

    def test_raises_on_invalid_yaml(self):
        """Should raise ValueError on invalid YAML."""
        content = """---
name: [unclosed
---
"""
        with self.assertRaises(ValueError) as ctx:
            parse_frontmatter(content)
        self.assertIn("Invalid YAML", str(ctx.exception))


class TestReadProperties(unittest.TestCase):
    """Tests for read_properties function."""

    def test_reads_valid_skill(self):
        """Should read properties from valid skill."""
        props = read_properties(FIXTURES_DIR / "valid-skill")
        self.assertEqual(props.name, "valid-skill")
        self.assertEqual(props.description, "A valid test skill for testing the CLI.")
        self.assertEqual(props.license, "MIT")
        self.assertEqual(props.compatibility, "Works everywhere")
        self.assertEqual(props.metadata["author"], "test")

    def test_reads_lowercase_skill_md(self):
        """Should read properties from skill.md (lowercase)."""
        props = read_properties(FIXTURES_DIR / "lowercase-skill-md")
        self.assertEqual(props.name, "lowercase-skill-md")

    def test_raises_on_missing_name(self):
        """Should raise ValueError if name is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_md = Path(tmpdir) / "SKILL.md"
            skill_md.write_text("---\ndescription: test\n---\n")
            with self.assertRaises(ValueError) as ctx:
                read_properties(Path(tmpdir))
            self.assertIn("name", str(ctx.exception))

    def test_raises_on_missing_description(self):
        """Should raise ValueError if description is missing."""
        with self.assertRaises(ValueError) as ctx:
            read_properties(FIXTURES_DIR / "invalid-missing-description")
        self.assertIn("description", str(ctx.exception))


class TestValidate(unittest.TestCase):
    """Tests for validate function."""

    def test_valid_skill_returns_empty_list(self):
        """Valid skill should return empty error list."""
        errors = validate(FIXTURES_DIR / "valid-skill")
        self.assertEqual(errors, [])

    def test_lowercase_skill_md_is_valid(self):
        """Skill with lowercase skill.md should be valid."""
        errors = validate(FIXTURES_DIR / "lowercase-skill-md")
        self.assertEqual(errors, [])

    def test_detects_uppercase_name(self):
        """Should detect uppercase characters in name."""
        errors = validate(FIXTURES_DIR / "invalid-uppercase-name")
        self.assertTrue(any("lowercase" in e for e in errors))

    def test_detects_extra_fields(self):
        """Should detect unexpected frontmatter fields."""
        errors = validate(FIXTURES_DIR / "invalid-extra-field")
        self.assertTrue(any("Unexpected fields" in e for e in errors))

    def test_detects_missing_description(self):
        """Should detect missing description field."""
        errors = validate(FIXTURES_DIR / "invalid-missing-description")
        self.assertTrue(any("description" in e for e in errors))

    def test_detects_name_mismatch(self):
        """Should detect when name doesn't match directory."""
        errors = validate(FIXTURES_DIR / "invalid-name-mismatch")
        self.assertTrue(any("must match" in e for e in errors))

    def test_detects_missing_frontmatter(self):
        """Should detect missing frontmatter."""
        errors = validate(FIXTURES_DIR / "invalid-no-frontmatter")
        self.assertTrue(any("frontmatter" in e for e in errors))

    def test_detects_missing_skill_md(self):
        """Should detect missing SKILL.md file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            errors = validate(Path(tmpdir))
            self.assertTrue(any("SKILL.md" in e for e in errors))

    def test_detects_nonexistent_path(self):
        """Should detect nonexistent path."""
        errors = validate(Path("/nonexistent/path"))
        self.assertTrue(any("does not exist" in e for e in errors))

    def test_validates_name_format(self):
        """Should validate name format rules."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "bad--name"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: bad--name\ndescription: test\n---\n"
            )
            errors = validate(skill_dir)
            self.assertTrue(any("consecutive hyphens" in e for e in errors))


class TestToPrompt(unittest.TestCase):
    """Tests for to_prompt function."""

    def test_generates_xml_for_single_skill(self):
        """Should generate valid XML for a single skill."""
        xml = to_prompt([FIXTURES_DIR / "valid-skill"])
        self.assertIn("<available_skills>", xml)
        self.assertIn("</available_skills>", xml)
        self.assertIn("<name>", xml)
        self.assertIn("valid-skill", xml)
        self.assertIn("<description>", xml)
        self.assertIn("<location>", xml)
        self.assertIn("SKILL.md", xml)

    def test_generates_xml_for_multiple_skills(self):
        """Should generate XML for multiple skills."""
        xml = to_prompt(
            [
                FIXTURES_DIR / "valid-skill",
                FIXTURES_DIR / "lowercase-skill-md",
            ]
        )
        self.assertEqual(xml.count("<skill>"), 2)
        self.assertIn("valid-skill", xml)
        self.assertIn("lowercase-skill-md", xml)

    def test_empty_list_returns_empty_xml(self):
        """Should return empty XML block for empty list."""
        xml = to_prompt([])
        self.assertEqual(xml, "<available_skills>\n</available_skills>")

    def test_escapes_html_entities(self):
        """Should escape HTML entities in name/description."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "test-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                '---\nname: test-skill\ndescription: Test <script> & "quotes"\n---\n'
            )
            xml = to_prompt([skill_dir])
            self.assertIn("&lt;script&gt;", xml)
            self.assertIn("&amp;", xml)

    def test_yaml_format(self):
        """Should generate YAML output."""
        output = to_prompt([FIXTURES_DIR / "valid-skill"], fmt="yaml")
        self.assertIn("available_skills:", output)
        self.assertIn("name: valid-skill", output)
        self.assertIn("description:", output)
        self.assertIn("location:", output)

    def test_json_format(self):
        """Should generate JSON output."""
        output = to_prompt([FIXTURES_DIR / "valid-skill"], fmt="json")
        data = json.loads(output)
        self.assertIn("available_skills", data)
        self.assertEqual(len(data["available_skills"]), 1)
        self.assertEqual(data["available_skills"][0]["name"], "valid-skill")


class TestSkillProperties(unittest.TestCase):
    """Tests for SkillProperties dataclass."""

    def test_to_dict_includes_required_fields(self):
        """Should include name and description."""
        props = SkillProperties(name="test", description="A test")
        d = props.to_dict()
        self.assertEqual(d["name"], "test")
        self.assertEqual(d["description"], "A test")

    def test_to_dict_excludes_none_values(self):
        """Should exclude None optional fields."""
        props = SkillProperties(name="test", description="A test")
        d = props.to_dict()
        self.assertNotIn("license", d)
        self.assertNotIn("compatibility", d)
        self.assertNotIn("allowed-tools", d)

    def test_to_dict_includes_optional_fields_when_set(self):
        """Should include optional fields when set."""
        props = SkillProperties(
            name="test",
            description="A test",
            license="MIT",
            compatibility="Python 3.12+",
            allowed_tools="Bash Read",
            metadata={"author": "test"},
        )
        d = props.to_dict()
        self.assertEqual(d["license"], "MIT")
        self.assertEqual(d["compatibility"], "Python 3.12+")
        self.assertEqual(d["allowed-tools"], "Bash Read")
        self.assertEqual(d["metadata"]["author"], "test")


class TestFindSkillsInDir(unittest.TestCase):
    """Tests for find_skills_in_dir function."""

    def test_finds_skill_in_root(self):
        """Should find skill when SKILL.md is in root."""
        skills = find_skills_in_dir(FIXTURES_DIR / "valid-skill")
        self.assertEqual(len(skills), 1)

    def test_finds_skills_in_subdirectories(self):
        """Should find skills in subdirectories."""
        skills = find_skills_in_dir(FIXTURES_DIR)
        self.assertGreater(len(skills), 1)

    def test_returns_empty_for_no_skills(self):
        """Should return empty list if no skills found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills = find_skills_in_dir(Path(tmpdir))
            self.assertEqual(skills, [])


class TestCLICommands(unittest.TestCase):
    """Integration tests for CLI commands."""

    def run_cli(self, *args) -> subprocess.CompletedProcess:
        """Run the skills CLI with given arguments."""
        return subprocess.run(
            ["uv", "run", "skills", *args],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )

    def test_help(self):
        """Should show help."""
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("CLI tool for managing Agent Skills", result.stdout)

    def test_version(self):
        """Should show version."""
        result = self.run_cli("--version")
        self.assertEqual(result.returncode, 0)
        self.assertIn("0.1.0", result.stdout)

    def test_validate_valid_skill(self):
        """Should validate a valid skill."""
        result = self.run_cli("validate", str(FIXTURES_DIR / "valid-skill"))
        self.assertEqual(result.returncode, 0)
        self.assertIn("Valid skill", result.stdout)

    def test_validate_invalid_skill(self):
        """Should fail on invalid skill."""
        result = self.run_cli("validate", str(FIXTURES_DIR / "invalid-uppercase-name"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Validation failed", result.stderr)

    def test_read_properties(self):
        """Should output JSON properties."""
        result = self.run_cli("read-properties", str(FIXTURES_DIR / "valid-skill"))
        self.assertEqual(result.returncode, 0)
        props = json.loads(result.stdout)
        self.assertEqual(props["name"], "valid-skill")
        self.assertEqual(props["license"], "MIT")

    def test_to_prompt(self):
        """Should generate XML prompt."""
        result = self.run_cli("to-prompt", str(FIXTURES_DIR / "valid-skill"))
        self.assertEqual(result.returncode, 0)
        self.assertIn("<available_skills>", result.stdout)
        self.assertIn("valid-skill", result.stdout)

    def test_create(self):
        """Should create a new skill scaffold."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self.run_cli("create", "my-new-skill", "-p", tmpdir)
            self.assertEqual(result.returncode, 0)
            skill_dir = Path(tmpdir) / "my-new-skill"
            self.assertTrue(skill_dir.exists())
            self.assertTrue((skill_dir / "SKILL.md").exists())
            self.assertTrue((skill_dir / "scripts").exists())
            self.assertTrue((skill_dir / "references").exists())
            self.assertTrue((skill_dir / "assets").exists())

    def test_create_invalid_name(self):
        """Should reject invalid skill names."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self.run_cli("create", "Invalid-Name", "-p", tmpdir)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Error", result.stderr)

    def test_zip(self):
        """Should create a zip file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "skill.zip"
            result = self.run_cli(
                "zip",
                str(FIXTURES_DIR / "valid-skill"),
                "-o",
                str(output_path),
            )
            self.assertEqual(result.returncode, 0)
            self.assertTrue(output_path.exists())

            # Verify zip contents
            with zipfile.ZipFile(output_path, "r") as zf:
                names = zf.namelist()
                self.assertTrue(any("SKILL.md" in n for n in names))

    def test_install_local(self):
        """Should install from local directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "installed"
            result = self.run_cli(
                "install",
                str(FIXTURES_DIR / "valid-skill"),
                "-d",
                str(dest),
            )
            self.assertEqual(result.returncode, 0)
            self.assertTrue((dest / "valid-skill" / "SKILL.md").exists())

    def test_install_from_zip(self):
        """Should install from zip file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # First create a zip
            zip_path = Path(tmpdir) / "skill.zip"
            self.run_cli(
                "zip",
                str(FIXTURES_DIR / "valid-skill"),
                "-o",
                str(zip_path),
            )

            # Then install from it
            dest = Path(tmpdir) / "installed"
            result = self.run_cli("install", str(zip_path), "-d", str(dest))
            self.assertEqual(result.returncode, 0)
            self.assertTrue((dest / "valid-skill" / "SKILL.md").exists())

    def test_list_empty(self):
        """Should handle empty skills directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self.run_cli("list", "-p", tmpdir)
            self.assertEqual(result.returncode, 0)
            self.assertIn("No skills installed", result.stdout)

    def test_list_with_skills(self):
        """Should list installed skills."""
        result = self.run_cli("list", "-p", str(FIXTURES_DIR))
        self.assertEqual(result.returncode, 0)
        self.assertIn("valid-skill", result.stdout)


class TestNameValidation(unittest.TestCase):
    """Tests for skill name validation rules."""

    def test_valid_names(self):
        """Should accept valid skill names."""
        valid_names = [
            "my-skill",
            "skill",
            "my-awesome-skill",
            "a",
            "skill123",
            "123skill",
            "my-skill-2",
        ]
        for name in valid_names:
            with tempfile.TemporaryDirectory() as tmpdir:
                skill_dir = Path(tmpdir) / name
                skill_dir.mkdir()
                (skill_dir / "SKILL.md").write_text(
                    f"---\nname: {name}\ndescription: test\n---\n"
                )
                errors = validate(skill_dir)
                self.assertEqual(errors, [], f"Name '{name}' should be valid")

    def test_invalid_names(self):
        """Should reject invalid skill names."""
        invalid_cases = [
            ("My-Skill", "uppercase"),
            ("-skill", "starts with hyphen"),
            ("skill-", "ends with hyphen"),
            ("my--skill", "consecutive hyphens"),
            ("my_skill", "underscore"),
            ("my skill", "space"),
            ("my.skill", "dot"),
        ]
        for name, reason in invalid_cases:
            with tempfile.TemporaryDirectory() as tmpdir:
                skill_dir = Path(tmpdir) / name.replace(" ", "-").lower()
                skill_dir.mkdir()
                (skill_dir / "SKILL.md").write_text(
                    f"---\nname: {name}\ndescription: test\n---\n"
                )
                errors = validate(skill_dir)
                self.assertGreater(
                    len(errors), 0, f"Name '{name}' should be invalid ({reason})"
                )


if __name__ == "__main__":
    # Run with verbosity
    unittest.main(verbosity=2)
