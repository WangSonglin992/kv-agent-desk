"""Refresh account cards in the background, retaining successful snapshots."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import threading

from ..retention import timestamp

ACCOUNT_CARDS = (
    ("deepseek_lab", "课题组 DeepSeek", "deepseek", "balance"),
    ("deepseek_personal", "个人 DeepSeek", "deepseek", "balance"),
)


class AccountMonitor:
    def __init__(self, path: Path | None, stop: threading.Event, demo: bool = False):
        self.path, self.stop, self.demo = path, stop, demo
        self.lock = threading.Lock()
        self.started = False
        self.values = {key: {"id": key, "name": name, "brand": brand, "kind": kind,
                            "known": False, "value": None, "remaining_percent": None,
                            "currency": None, "reset_at": None, "updated_at": None,
                            "detail": "等待更新", "stale": True, "source": ""}
                       for key, name, brand, kind in ACCOUNT_CARDS}
        if path and path.is_file():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                for row in saved.get("accounts", []):
                    if isinstance(row, dict) and row.get("id") in self.values and row.get("known"):
                        self.values[row["id"]].update(row, stale=True)
            except (OSError, ValueError, TypeError, AttributeError):
                pass

    def accept(self, rows: list[dict]) -> None:
        with self.lock:
            for row in rows:
                key = row.get("id")
                if key not in self.values:
                    continue
                old = self.values[key]
                if not row.get("known") and old.get("known"):
                    old.update(stale=True, detail=row.get("detail") or "数据待检查更新")
                else:
                    self.values[key] = deepcopy(row)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(json.dumps({"accounts": list(self.values.values())}, ensure_ascii=False), encoding="utf-8")
                os.replace(temporary, self.path)

    def snapshot(self) -> list[dict]:
        now = datetime.now().astimezone()
        with self.lock:
            rows = [deepcopy(self.values[key]) for key, *_ in ACCOUNT_CARDS]
        for row in rows:
            seen = timestamp(row.get("updated_at"))
            if not seen or (now - seen).total_seconds() > 300:
                row["stale"] = True
        return rows

    def start(self) -> None:
        if self.started or self.demo:
            return
        self.started = True
        from .deepseek import DeepSeekAccounts
        for collector in (DeepSeekAccounts(),):
            threading.Thread(target=self._poll, args=(collector,), daemon=True, name="account-quota").start()

    def _poll(self, collector) -> None:
        while not self.stop.is_set():
            try:
                self.accept(collector.collect())
            except Exception:
                pass
            self.stop.wait(30)
