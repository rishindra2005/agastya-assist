# Rules for coding agents in this repo

- NEVER run any git command (no git status/add/commit/diff/log/checkout/stash/reset). Do not read or modify the .git directory. Version control is handled by a supervisor.
- Use the existing virtualenv: `.venv/bin/python`, install packages with `uv pip install --python .venv/bin/python <pkg>` and record them in pyproject.toml.
- Read PLAN.md first. Only do the chunk you are assigned.
- Every module gets pytest tests under tests/. Run `.venv/bin/python -m pytest -q -m "not live"` and make it pass before finishing.
- No real network calls in non-live tests (mock HTTP; use local fixture HTML).
- Never put secrets in code; read from .env via agastya.config.
- SAFETY: the assistant must never click final submit, pay, e-verify, OTP, or login buttons on real government portals.
- Finish with a short summary of the files you created/changed and the test result.
