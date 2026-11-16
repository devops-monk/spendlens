"""`python -m spendlens` == `spendlens` (used by `spendlens report` to run each step in its own process)."""

from .cli import app

if __name__ == "__main__":
    app()
