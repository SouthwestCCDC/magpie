# PR Review Feedback Loop

This file provides context for AI agents working within the PR review feedback loop.

## Overview

The feedback loop enables orchestrators to iterate on PR reviews autonomously while maintaining safeguards. This is an **opt-in** mode - humans must explicitly request it.

## Key Parameters

| Parameter | Value |
|-----------|-------|
| Round limit | 4 |
| Scratch notes | `.ai/scratch/issue-{N}/` |
| Escalation marker | `<!-- AWAITING_HUMAN: {reason} -->` |

## Stop Conditions

The loop MUST halt when:

1. **Non-convergence**: Issues not decreasing round-over-round
2. **Circular feedback**: Same issue 3+ times, or A->B->A reversion
3. **Design decision**: Category C items present
4. **Round limit**: 4 rounds completed

## Escalation Format

When stopping, post a PR comment with:

```markdown
<!-- AWAITING_HUMAN: {reason} -->

## Agent Paused - Human Input Required

**Reason**: {explanation}

**Review history**:
| Round | Comments | Addressed | Remaining |
|-------|----------|-----------|-----------|

**Blocking items**:
- {issues}

**What I need**: {question}

(AI-generated via Claude Code w/ {model})
```

## External Reviews

When humans or GitHub Copilot add reviews:
- Treat as new round baseline
- Re-fetch and categorize all comments
- Track external vs agent comments separately

## Scratch Notes Location

```
.ai/scratch/
└── issue-{N}/
    ├── orchestrator/
    ├── developer/
    └── reviewer/
```

See `docs/repo/patterns/ai-scratch-notes.md` for format details.

## References

- Full pattern: `docs/repo/patterns/review-feedback-loop.md`
- GraphQL patterns: `docs/repo/patterns/github-graphql-patterns.md`
- Universal rules: `docs/repo/universal-agent-rules.md` (Section 8)
