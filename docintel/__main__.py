"""Run the command line as `python -m docintel`.

It does the same as the `docintel` command. Use it when the `docintel` launcher is not on the PATH or is blocked,
which happens now and then on Windows.
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
