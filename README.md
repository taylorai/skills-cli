# skills-cli

A CLI tool for managing [Agent Skills](https://agentskills.io/) - the open format for giving AI agents new capabilities.

## Installation

```bash
pip install skills-cli
```

Or install from source:

```bash
git clone https://github.com/your-username/skills-cli
cd skills-cli
pip install -e .
```

## What are Agent Skills?

Agent Skills are folders containing a `SKILL.md` file with instructions, scripts, and resources that agents can discover and use. They allow you to package domain expertise into reusable capabilities for AI agents.

A skill directory looks like:

```
my-skill/
├── SKILL.md          # Required: instructions + metadata
├── scripts/          # Optional: executable code
├── references/       # Optional: documentation
└── assets/           # Optional: templates, resources
```

The `SKILL.md` file must contain YAML frontmatter with at least `name` and `description`:

```markdown
---
name: my-skill
description: What this skill does and when to use it.
---

# My Skill

Instructions for the agent...
```

## Commands

### `skills create`

Create a new skill scaffold with the required structure.

```bash
skills create my-skill
skills create my-skill --path /path/to/directory
```

This creates:
- `SKILL.md` with template frontmatter
- `scripts/`, `references/`, and `assets/` directories

### `skills validate`

Validate a skill directory against the Agent Skills specification.

```bash
skills validate ./my-skill
skills validate ./my-skill/SKILL.md  # Also accepts SKILL.md directly
```

Checks for:
- Valid YAML frontmatter
- Required fields (`name`, `description`)
- Name format (lowercase, hyphens only, no consecutive hyphens)
- Name matches directory name
- No unexpected frontmatter fields
- Field length limits

### `skills read-properties`

Read skill properties and output as JSON.

```bash
skills read-properties ./my-skill
```

Output:
```json
{
  "name": "my-skill",
  "description": "What this skill does.",
  "license": "MIT",
  "metadata": {
    "author": "example"
  }
}
```

### `skills to-prompt`

Generate available skills block for agent system prompts. Supports XML (default), YAML, and JSON formats.

```bash
skills to-prompt ./skill-a ./skill-b
skills to-prompt ./skill-a --format yaml
skills to-prompt ./skill-a -f json
```

**XML output (default):**
```xml
<available_skills>
<skill>
<name>
skill-a
</name>
<description>
Description of skill A.
</description>
<location>
/path/to/skill-a/SKILL.md
</location>
</skill>
</available_skills>
```

**YAML output:**
```yaml
available_skills:
- name: skill-a
  description: Description of skill A.
  location: /path/to/skill-a/SKILL.md
```

**JSON output:**
```json
{
  "available_skills": [
    {
      "name": "skill-a",
      "description": "Description of skill A.",
      "location": "/path/to/skill-a/SKILL.md"
    }
  ]
}
```

### `skills install`

Install skills from a local directory, zip file, or GitHub repository.

```bash
# From local directory
skills install ./my-skill

# From zip file
skills install ./my-skill.zip

# From GitHub (whole repo)
skills install https://github.com/anthropics/skills

# From GitHub (specific path in repo)
skills install https://github.com/owner/repo/tree/main/skills/my-skill
skills install https://github.com/owner/repo --subpath skills/my-skill

# Specify destination (default: ~/.claude/skills or ~/.codex/skills)
skills install ./my-skill --dest ./local-skills
```

The install command:
- Validates skills before installing
- Searches up to 2 levels deep for skills in directories/repos
- Overwrites existing skills with the same name

### `skills list`

List installed skills.

```bash
skills list
skills list --path ./my-skills-dir
```

Output:
```
/Users/you/.claude/skills:
  my-skill: Description of my skill...
  another-skill: Another skill description...
```

### `skills zip`

Package a skill into a zip file for distribution.

```bash
skills zip ./my-skill
skills zip ./my-skill --output ./dist/my-skill.zip
```

### `skills push`

Upload a skill to your Anthropic account via the API.

```bash
# Requires ANTHROPIC_API_KEY environment variable
export ANTHROPIC_API_KEY=sk-ant-...

skills push ./my-skill
skills push ./my-skill --update  # Update existing skill
```

## Skill Name Requirements

Skill names must:
- Be lowercase
- Contain only letters, numbers, and hyphens
- Not start or end with a hyphen
- Not contain consecutive hyphens (`--`)
- Be 64 characters or less
- Match the directory name

Valid: `my-skill`, `data-analysis`, `pdf-reader`
Invalid: `My-Skill`, `-skill`, `skill-`, `my--skill`

## Default Skill Directories

The CLI looks for installed skills in these locations (in order):
1. `~/.claude/skills`
2. `~/.codex/skills`

## Environment Variables

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Required for `push` command |
| `ANTHROPIC_API_URL` | API base URL (default: `https://api.anthropic.com`) |

## Running Tests

```bash
uv run python tests/test_skills.py
```

## License

MIT

## Attribution

Validation and prompt generation logic adapted from [skills-ref](https://github.com/agentskills/agentskills), licensed under Apache 2.0. Copyright Anthropic, PBC.
