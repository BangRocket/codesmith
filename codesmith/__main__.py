"""Codesmith entry point with mode selection.

Run with:
    poetry run python -m codesmith

Environment variables:
    CODESMITH_MODE: "stream" (default) or "pty"
"""

import os

mode = os.environ.get("CODESMITH_MODE", "stream").lower()

if mode == "pty":
    from .bot import main
else:
    from .stream_bot import main

if __name__ == "__main__":
    main()
