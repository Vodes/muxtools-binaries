set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

# Run all development checks (the default recipe).
check: lint test

# Validate packages and check Python and TOML formatting, linting, and Python types.
lint:
    uv run muxtools-build validate
    uv run ruff format --check src tests builder packages
    uv run ruff check src tests builder packages
    uv run tombi format --check
    uv run tombi lint --error-on-warnings
    uv run pyrefly check

# Run shared and package-specific tests, forwarding extra pytest arguments.
test *args:
    uv run pytest -q {{args}}

# Format Python and TOML files and apply automatic Python lint fixes.
format:
    uv run ruff format src tests builder packages
    uv run ruff check --fix src tests builder packages
    uv run tombi format
