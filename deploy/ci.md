# .github — CI/CD workflows for PRED Agent
# Grounded in kit/09_CONFIG_SECURITY_DEPLOY.md (Dependency pinning, pip-audit in CI)

## ci.yml

Runs on every push/PR to main:
1. ruff — lint and format check
2. mypy — static type checking
3. pytest — unit + integration tests (mocked network, no PRED sandbox)
4. pip-audit — dependency vulnerability scan

## Dependency pinning

All dependencies are pinned with exact versions in `backend/pyproject.toml`.
A monthly manual update routine is recommended:
- Review `pip-audit` output
- Update patch versions in `pyproject.toml`
- Run full test suite before deploying

## Monthly update routine

1. `pip list --outdated` — check for updates
2. Update `pyproject.toml` versions
3. `pip install -e ".[dev]"` and run tests
4. `pip-audit` — verify no new vulnerabilities
5. Deploy updated image to VPS
