# Contributing

## Dev setup

```bash
git clone https://github.com/n1cke1/winkers
cd winkers
python -m venv .venv && .venv/Scripts/activate  # or source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -v
ruff check src/ tests/
```

## Adding a language

1. `pip install tree-sitter-<lang>`, add to `pyproject.toml`
2. Create `src/winkers/languages/<lang>.py` with tree-sitter queries (see existing profiles)
3. Register in `languages/__init__.py` and add loader in `parser.py`
4. Add fixture in `tests/fixtures/` and tests in `test_languages.py`

Each profile is ~50 lines. Look at `python.py` or `go.py` as examples.

## Code style

- `ruff check src/ tests/` — 0 errors
- `pytest tests/` — all green
- Functions < 40 lines, type hints everywhere
