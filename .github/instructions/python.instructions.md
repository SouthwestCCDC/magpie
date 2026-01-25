---
applyTo: "**/*.py"
---

# Python Code Guidelines

## Type Hints
- All public functions must have type hints
- Use Python 3.13+ built-in types: `list[str]`, `dict[str, Any]`, `str | None` (not `List`, `Dict`, `Optional`)
- Pydantic models for request/response validation

## Error Handling
- Catch specific exceptions, not bare `except:`
- FastAPI endpoints should return appropriate HTTP status codes
- CLI commands should present user-friendly error messages

## Code Organization
- Server code in `src/magpie/server/`
- CLI code in `src/magpie/cli/` (client) and `src/magpie/ctl/` (admin)
- Storage operations in `src/magpie/storage/`
- Client CLI should NOT import from server internals

## Testing
- Unit tests in `tests/unit/`
- Integration tests in `tests/integration/`
- E2E tests in `tests/e2e/` (require docker-compose)
- Use pytest fixtures for common setup

## FastAPI Patterns
- Use dependency injection (`Depends()`) for auth and storage
- Pydantic models for request/response schemas
- Background tasks for non-blocking operations
