"""支持 `python -I app/__main__.py` 形式的隔离模式入口。"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
