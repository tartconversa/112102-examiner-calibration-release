"""追加式审计日志（JSONL）。

批次追加、班次提交/确认/改派、撤销重核等动作都只追加、不改写，
已收到的确认与晚到撤销交错时可按事件顺序完整追查。
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


class Journal:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        seq = self._next_seq()
        entry = {"seq": seq, "action": action, **payload}
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False,
                                    default=self._default) + "\n")
        return entry

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as stream:
            return [json.loads(line) for line in stream if line.strip()]

    def _next_seq(self) -> int:
        entries = self.read()
        return 1 + max((e.get("seq", 0) for e in entries), default=0)

    @staticmethod
    def _default(obj: Any) -> Any:
        if is_dataclass(obj):
            return asdict(obj)
        if isinstance(obj, (set, frozenset)):
            return sorted(obj)
        raise TypeError(f"不可序列化的对象: {type(obj)!r}")
