# claude/prompt_loader.py — Prompt Template Loader

## Purpose

Loads prompt templates from `prompts/*.md` files and substitutes `{{variable}}` placeholders with runtime values. This keeps all prompt text out of Python code and makes prompts easy to iterate on without touching the codebase.

## Agentic Pattern: Prompt-as-Template

Storing prompts as files rather than hardcoded strings is a key maintainability pattern in agentic systems:

- **Prompt engineers can edit prompts** without reading Python
- **Version control shows prompt diffs clearly** (not buried in code)
- **The same prompt file can be reused** across different agents
- **No string interpolation conflicts** — the loader uses `{{key}}` syntax to avoid clashing with JSON curly braces in prompt examples

## Public Interface

### `PromptLoader(prompts_dir=Path("prompts/"))`

Initialises with the path to the prompts directory. Raises `FileNotFoundError` if the directory does not exist.

### `load(template_name, **variables) → str`

Loads `prompts/<template_name>.md`, substitutes all `{{placeholders}}`, and returns the rendered prompt string.

| Parameter | Type | Purpose |
|---|---|---|
| `template_name` | `str` | Filename without `.md`, e.g. `"score_job"` maps to `prompts/score_job.md` |
| `**variables` | `str` | Key-value pairs matching the `{{placeholders}}` in the template |

**Raises:**
- `FileNotFoundError` — if the `.md` file does not exist
- `KeyError` — if any `{{placeholder}}` in the template was not passed as a variable (fail-fast, no silent blanks)

**Warnings:**
- If you pass a variable that has no matching placeholder in the template, a WARNING is logged (but the call succeeds). This catches typos in variable names.

## Placeholder Syntax

Templates use `{{double_braces}}`:

```
<profile>
{{profile}}
</profile>
```

This was chosen over Python's `str.format()` (`{variable}`) to avoid conflicts with JSON examples that appear in prompt files — JSON uses single curly braces everywhere.

## Available Templates

| File | Variables | Used by |
|---|---|---|
| `prompts/parse_resume.md` | `resume_text` | `ProfileAgent` |
| `prompts/score_job.md` | `profile`, `tracks`, `salary_min`, `salary_currency` | `ScoringAgent` |
| `prompts/tailor_resume.md` | `profile`, `job`, `track` | `TailoringAgent` |

See [prompts/overview.md](../prompts/overview.md) for full prompt documentation.
