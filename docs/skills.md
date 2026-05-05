# Skills

Skills are named prompt templates that route through the LLM. Unlike slash commands (which execute locally), skills expand a short `/skillname` invocation into a detailed LLM prompt and start a full agent turn.

## How skills work

1. User types `/commit` in the chat.
2. `Session.send()` detects it is a skill invocation and calls `skill.get_prompt("")`.
3. The returned prompt replaces the raw text and is sent to the LLM.
4. The agent executes the skill using available tools.

Skills also support **auto-routing**: if you describe what you want in natural language (e.g. "the tests keep failing after my refactor"), openvibe scores all skills by keyword match and may automatically invoke `/debug` without you needing the `/` prefix.

## Built-in skills

### `/simplify`

Review recently changed code for opportunities to simplify, reuse, and improve quality. Fixes issues it finds.

```
/simplify
/simplify focus on the auth module
```

### `/debug`

Diagnose and fix a bug or failing test.

```
/debug
/debug the import error in cli.py
```

### `/plan`

Produce a structured, step-by-step plan before making any changes. Useful before tackling a large feature.

```
/plan
/plan add OAuth login to the API
```

### `/commit` (alias `/gc`)

Stage changed files, generate a conventional commit message from the diff, and commit.

```
/commit
/gc
```

## Listing skills

```
/skills          # full metadata for each skill
/help            # commands + skills combined
```

## Writing a custom skill

Create a Python file in `<project>/skills/`. It is loaded automatically on startup.

```python
# skills/security.py
from openvibe.skill.base import CostTier, SkillDefinition
from openvibe.skill import register_skill


class SecurityReviewSkill(SkillDefinition):
    name = "security"
    description = "Run a security review of the changed files."
    aliases = ["sec"]
    tags = ["security", "vulnerability", "audit", "owasp"]
    capabilities = ["code_review", "security_analysis"]
    cost_estimate = CostTier.MEDIUM
    when_to_use = "After changes that touch auth, input handling, or data storage."
    argument_hint = "[focus area]"

    def get_prompt(self, args: str) -> str:
        focus = f" Focus on: {args}." if args else ""
        return (
            f"Perform a security review of the recently changed files.{focus}\n\n"
            "Check for: SQL injection, XSS, authentication bypasses, "
            "insecure deserialization, path traversal, hardcoded secrets, "
            "and OWASP Top 10 issues.\n\n"
            "For each finding: describe the vulnerability, its severity "
            "(Critical/High/Medium/Low), and provide a concrete fix."
        )


register_skill(SecurityReviewSkill())
```

```
/security
/security focus on the login endpoint
```

## SkillDefinition reference

### Identity

| Attribute | Type | Description |
|-----------|------|-------------|
| `name` | `str` | Primary invocation name (e.g. `"security"` → `/security`) |
| `description` | `str` | One-line description shown in `/skills` |
| `aliases` | `list[str]` | Alternative invocation names |

### Discovery metadata

| Attribute | Type | Description |
|-----------|------|-------------|
| `capabilities` | `list[str]` | e.g. `["code_review", "refactoring"]` |
| `input_types` | `list[str]` | e.g. `["code", "error_message"]` |
| `output_types` | `list[str]` | e.g. `["code_diff", "report"]` |
| `tags` | `list[str]` | Free-form tokens used by keyword auto-routing |
| `cost_estimate` | `CostTier` | `LOW` / `MEDIUM` / `HIGH` |
| `examples` | `list[SkillExample]` | Example inputs and descriptions |

### UX

| Attribute | Type | Description |
|-----------|------|-------------|
| `user_invocable` | `bool` | `True` to show in `/skills` list |
| `when_to_use` | `str` | One-line hint shown in `/skills` |
| `argument_hint` | `str` | Usage hint, e.g. `"[focus area]"` |

### Execution control

| Attribute | Type | Description |
|-----------|------|-------------|
| `max_retries` | `int` | Extra attempts beyond the first (0 = no retry) |
| `fallback_skill` | `str \| None` | Skill to invoke after all retries are exhausted |
| `validators` | `list[SkillValidator]` | Validators that inspect the result and decide if a retry is needed |

### Methods to implement

#### `get_prompt(args: str) → str` (required)

Return the LLM prompt for this invocation. `args` is everything the user typed after the skill name.

#### `match_intent(text: str) → float` (optional)

Return a confidence score [0.0–1.0] that `text` intends to invoke this skill. Used by the auto-router for natural-language invocation. Default implementation does keyword matching against `tags`, `capabilities`, and `aliases`.

#### `extract_args(text: str) → str` (optional)

Extract skill arguments from a natural-language message. Default returns `""`.

#### `get_retry_prompt(args: str, attempt: int, hint: str) → str` (optional)

Return a modified prompt for retry attempt N. Default appends the validator hint to the base prompt.

## Validators

Validators inspect a `SkillResult` and decide if it passes or needs a retry.

```python
from openvibe.skill.base import SkillValidator, ValidationResult, SkillResult

class HasTestsValidator(SkillValidator):
    name = "has_tests"

    def validate(self, result: SkillResult, context: dict) -> ValidationResult:
        if "def test_" in result.output or "it(" in result.output:
            return ValidationResult(passed=True)
        return ValidationResult(
            passed=False,
            reason="No tests found in output.",
            retry_hint="Make sure to write tests alongside any new code.",
        )
```

Attach to a skill:

```python
class MySkill(SkillDefinition):
    max_retries = 2
    validators = [HasTestsValidator()]
    ...
```
