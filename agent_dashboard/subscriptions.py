"""Persist optional subscription descriptions independently of live quota data."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import json
import logging
import os
from pathlib import Path
import threading

PROVIDERS = ("codex", "zcode", "antigravity")
FIELDS = ("plan_name", "plan_spec", "expires_at")


class SubscriptionProfiles:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.lock = threading.RLock()
        self.values: dict[str, dict] = {}
        self.last_read_status = "ready" if path is None else "unread"
        self._last_failure = None
        self.reload()

    def reload(self) -> bool:
        """Retry local metadata without discarding values after a failed read."""
        if self.path is None:
            return True
        with self.lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or not isinstance(data.get("profiles", {}), dict):
                    raise ValueError("invalid saved subscription metadata")
                profiles = data.get("profiles", {})
                updated = {}
                for provider in PROVIDERS:
                    try:
                        updated[provider] = self.validate(profiles.get(provider, {}))
                    except ValueError:
                        updated[provider] = deepcopy(self.values.get(provider, {}))
            except (OSError, ValueError, TypeError) as exc:
                self.last_read_status = ("missing" if isinstance(exc, FileNotFoundError) else
                                         "unavailable" if isinstance(exc, OSError) else "invalid")
                failure = (type(exc).__name__, getattr(exc, "errno", None))
                if failure != self._last_failure and not isinstance(exc, FileNotFoundError):
                    logging.getLogger(__name__).warning("Saved subscription metadata unavailable (%s, errno=%s)", *failure)
                self._last_failure = failure
                return False
            self.values = updated
            self.last_read_status = "ready"
            self._last_failure = None
            return True

    @staticmethod
    def validate(value: object) -> dict[str, str]:
        if not isinstance(value, dict) or set(value) - set(FIELDS):
            raise ValueError("套餐信息格式不正确")
        result = {}
        for field, limit, label in (("plan_name", 80, "套餐名称"), ("plan_spec", 160, "套餐规格"),
                                    ("expires_at", 40, "到期日期")):
            raw = value.get(field, "")
            if not isinstance(raw, str):
                raise ValueError(label + "应填写文字")
            clean = " ".join(raw.split())
            if len(clean) > limit:
                raise ValueError(f"{label}最多{limit}个字符")
            if field == "expires_at" and clean:
                try:
                    if len(clean) == 10:
                        date.fromisoformat(clean)
                    else:
                        datetime.fromisoformat(clean.replace("Z", "+00:00"))
                except ValueError:
                    raise ValueError("请填写有效的到期日期") from None
            result[field] = clean
        return result

    def get_all(self) -> dict[str, dict[str, str]]:
        with self.lock:
            return {provider: {field: self.values.get(provider, {}).get(field, "") for field in FIELDS}
                    for provider in PROVIDERS}

    def update(self, provider: object, value: object) -> dict:
        if not isinstance(provider, str) or provider not in PROVIDERS:
            raise ValueError("未知订阅来源")
        profile = self.validate(value)
        with self.lock:
            if self.path is not None:
                self.reload()
                if self.last_read_status not in {"ready", "missing"}:
                    raise OSError("Saved subscription metadata is not readable; retry before saving changes")
            updated = deepcopy(self.values)
            updated[provider] = profile
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(json.dumps({"version": 1, "profiles": updated}, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
                os.replace(temporary, self.path)
            self.values = updated
            self.last_read_status = "ready"
            return self.get_all()

    def apply(self, agent: dict) -> dict:
        result = deepcopy(agent)
        subscription = result.setdefault("subscription", {})
        automatic = {field: subscription.get(field) or "" for field in FIELDS}
        profile = self.get_all().get(agent.get("provider"), {})
        sources = {}
        for field in FIELDS:
            if profile.get(field):
                subscription[field] = profile[field]
                sources[field] = "manual"
            elif automatic[field]:
                sources[field] = "cached" if field in subscription.get("metadata_retained", []) else "automatic"
            else:
                sources[field] = "unknown"
        subscription["automatic_metadata"] = automatic
        subscription["metadata_sources"] = sources
        return result
