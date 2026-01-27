# Documentation Writing Guidelines

## Input Requirements

Before writing documentation, you MUST have one of:

1. **Fact inventory from developer agent** (preferred) - Structured list of verified claims with file/symbol citations
2. **Explicit "trivial update" permission** - For typo fixes, formatting, or minor clarifications only

If you have neither, STOP and request a fact inventory first.

## Hard Boundaries

1. **No unverified claims** - If it's not in your fact inventory, do not include it
2. **No code exploration for new content** - Request a fact inventory instead
3. **Shorter accurate > longer hallucinated** - Incomplete docs are better than wrong docs
4. **When uncertain, mark it** - Write `<!-- TODO: verify -->` rather than guessing

## Citation Rules

- Preserve citations from fact inventory: `(see [config.py](../../src/magpie/config.py))`
- Do NOT cite line numbers (they drift)
- For user guides: keep prose clean, citations optional
- For technical docs: include file/function references from inventory

## Style Guidelines

- **Be concise** - Users want answers, not prose
- **Use examples** - Show, don't tell
- **Active voice** - "Run the command" not "The command should be run"
- **Include error cases** - What can go wrong and how to fix it

## Key Files

| File | Purpose |
|------|---------|
| `docs/user-guide.md` | CLI usage, workflows, admin ops |
| `docs/index.md` | Documentation overview and references |
| `.github/copilot-instructions.md` | AI context |

## Workflow

1. **Verify input** - Confirm you have a fact inventory or trivial-update permission
2. **Outline phase** - List sections you CAN document based on provided facts
3. **Write phase** - Write prose ONLY from your fact inventory; omit what's not covered
4. **Mark gaps** - Use `<!-- TODO: need fact inventory for X -->` for missing coverage
5. **Commit** with AI disclosure
