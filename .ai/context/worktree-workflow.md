# Worktree Workflow

All implementation work happens in isolated git worktrees, never in the main repository checkout.

## Why Worktrees

- Isolates work-in-progress from the main checkout
- Enables parallel work on multiple issues
- Prevents accidental commits to protected branches
- Clean state for each task

## Rules

1. **Never modify the main repo** - It's read-only for reference
2. **One worktree per task** - Named for the issue/feature
3. **Clean up when done** - Remove worktrees after PR merge

## Creating a Worktree

```bash
# From main repo directory
git fetch origin
git worktree add ../magpie-worktrees/issue-{N}-$(date +%s) \
  -b issue-{N}-$(date +%s) origin/default

# Navigate to worktree
cd ../magpie-worktrees/issue-{N}-*
```

## Naming Convention

- Issue work: `issue-{N}-{timestamp}`
- Feature work: `feat-{description}-{timestamp}`

The timestamp ensures uniqueness when revisiting the same issue.

## Worktree Lifecycle

1. **Create** when starting a task
2. **Work** entirely within the worktree
3. **Push** branch and create PR
4. **Delete** after PR is merged: `git worktree remove {path}`
