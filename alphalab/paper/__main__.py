"""`python -m alphalab.paper` 兼容入口（与 `python -m alphalab` 同一 CLI）。"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())

