# Review Checklist

Use this checklist when reviewing PRs or pre-commit changes.

## Code Quality

- [ ] Clear, readable code with meaningful names
- [ ] Appropriate error handling
- [ ] Follows existing patterns in the codebase
- [ ] Type hints present on public functions

## API Design

- [ ] RESTful conventions followed
- [ ] Correct HTTP status codes
- [ ] Pydantic models for request/response
- [ ] OpenAPI documentation accurate

## Storage Layer

- [ ] Atomic operations (no partial writes)
- [ ] No path traversal vulnerabilities
- [ ] Proper file permissions

## Testing

- [ ] Tests cover new functionality
- [ ] Edge cases handled
- [ ] Not over-mocked (tests real behavior)

## Security

- [ ] No hardcoded secrets
- [ ] Input validation present
- [ ] Auth checks on protected endpoints

## AI Disclosure

- [ ] AI-generated commits include `Co-authored-by: {AI Tool/Model} <noreply@{domain}.com>`
- [ ] PR description mentions AI assistance if applicable

## Assessment Categories

| Assessment | Meaning |
|------------|---------|
| **Ready to merge** | All checks pass, no blocking issues |
| **Needs fixes** | Issues found that must be addressed |
| **Needs discussion** | Architectural or scope questions to resolve |
