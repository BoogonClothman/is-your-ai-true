"""JSONL 归档：把每次回复（成功 / 失败 / 取消）追加为一行完整结构。"""

import json
from pathlib import Path

from .record import ResponseRecord


class Archive:
    """ replies 留档仓库：单文件 responses.jsonl 持续追加。"""

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        self.path = self.directory / "responses.jsonl"

    def append(self, record: ResponseRecord) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(record.to_jsonl_line() + "\n")
        return self.path

    def iter_records(self):
        """按行读回历史记录（惰性迭代）。"""
        if not self.path.exists():
            return
        with open(self.path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def count(self) -> int:
        return sum(1 for _ in self.iter_records())
