# PR Comment Handling

## Proactive Check After Push

After pushing changes to a PR, check for review comments:

```bash
gh api repos/SouthwestCCDC/magpie/pulls/{N}/comments --jq '.[].body' | head -20
```

## Response Workflow

When review comments exist:

1. **Read and understand** each comment
2. **Address the issue** in your worktree
3. **Commit and push** the fix
4. **Reply to the comment** (see below)
5. **Re-check** for new comments

Work is not complete until all comments are addressed and replied to.

## Replying to Comments

Every addressed comment needs an inline reply with AI disclosure:

```bash
gh api repos/SouthwestCCDC/magpie/pulls/{N}/comments/{comment_id}/replies \
  -f body="Fixed in {SHA}. {brief explanation}

(AI-generated via Claude Code w/ {Model})"
```

## Comment Categories

When triaging comments:

| Category | Meaning | Action |
|----------|---------|--------|
| **A: Fix Now** | Valid issue for this PR | Implement fix, reply with commit SHA |
| **B: Tech Debt** | Valid but scope creep | Create follow-up issue, reply with issue link |
| **C: Larger Issue** | Reveals systemic problem | Discuss before proceeding |
| **D: Disagree** | Misunderstanding or opinion | Reply respectfully explaining position |

## Creating Follow-up Issues

For scope creep items, create an issue and link it:

```bash
gh issue create --repo SouthwestCCDC/magpie \
  --title "{description}" \
  --body "Follow-up from PR #{N} review.

{details}

Original comment: https://github.com/SouthwestCCDC/magpie/pull/{N}#discussion_r{comment_id}

(AI-generated via Claude Code w/ {Model})"
```

Then reply to the original comment with the issue link.
