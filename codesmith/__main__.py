"""Codesmith entry point with mode selection.

Run with:
    poetry run python -m codesmith

Environment variables:
    CODESMITH_MODE: "pty" (default) or "stream"
"""

import os

mode = os.environ.get("CODESMITH_MODE", "pty").lower()

if mode == "stream":
    from .stream_bot import main
else:
    from .bot import main

if __name__ == "__main__":
    main()
