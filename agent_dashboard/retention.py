"""Keep the last observed values on screen, with an understated freshness hint."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
import json
import os


def timestamp(value: str | None) -> datetime | None:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
        if result and result.tzinfo is None:
            result = result.astimezone()
        return result
    except (ValueError, TypeError, AttributeError):
        return None


class LastGoodStore:
    def __init__(self, path: Path | None = None, stale_seconds: float = 300):
        self.path = path
        self.stale_seconds = stale_seconds
        self.values: dict[str, dict] = {}
        self._saved = ""
        if path and path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("version") == 1 and isinstance(data.get("agents"), dict):
                    self.values = {k: v for k, v in data["agents"].items() if isinstance(v, dict)}
            except (OSError, ValueError, TypeError):
                pass

    def merge(self, current: dict, now: datetime | None = None) -> dict:
        now = now or datetime.now().astimezone()
        key = current["provider"]
        previous = self.values.get(key, {})
        result = deepcopy(current)
        retained = []
        seen = timestamp(current.get("updated_at"))
        previous_seen = timestamp(previous.get("updated_at"))
        older = bool(seen and previous_seen and seen < previous_seen)
        stale = bool(current.get("stale")) or not current.get("available", False)
        stale |= seen is None or (now - seen).total_seconds() > self.stale_seconds
        if older:
            # A restored/partially written source must not roll the screen back.
            for field in ("state", "state_label", "project", "task", "updated_at", "session_id", "progress", "active_tasks"):
                if previous.get(field) is not None:
                    result[field] = deepcopy(previous[field])
                    retained.append(field)
            old_tokens = previous.get("tokens") or {}
            new_tokens = result.get("tokens") or {}
            if old_tokens.get("known") and str(new_tokens.get("date", "")) <= str(old_tokens.get("date", "")):
                result["tokens"] = deepcopy(old_tokens)
                retained.append("tokens")
            stale = True
        observed = current.get("observed_state", current.get("state"))
        if older:
            observed = previous.get("state", "unknown")
        if not older and current.get("state") in {"working", "waiting", "idle"}:
            observed = current["state"]
        if observed in {"working", "waiting", "idle"}:
            result["state"] = observed
            if current.get("state") in {"unknown", "offline"}:
                result["state_label"] = {"working": "工作中", "waiting": "等待输入", "idle": "空闲"}[observed]
                retained.append("state")
        elif previous.get("state") in {"working", "waiting", "idle"}:
            result["state"] = previous["state"]
            result["state_label"] = previous.get("state_label", "")
            retained.append("state")

        for field in ("project", "task", "updated_at", "session_id"):
            if not result.get(field) and previous.get(field):
                result[field] = previous[field]
                if field != "session_id":
                    retained.append(field)
        count = result.get("active_tasks")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            if isinstance(previous.get("active_tasks"), int):
                result["active_tasks"] = previous["active_tasks"]
                retained.append("active_tasks")
            else:
                result["active_tasks"] = None

        for field in ("progress", "tokens"):
            value = result.get(field) or {}
            old = previous.get(field) or {}
            if not value.get("known") and old.get("known"):
                result[field] = deepcopy(old)
                result[field]["retained"] = True
                retained.append(field)
            elif value.get("known"):
                result[field] = deepcopy(value)
                result[field]["retained"] = False
            if field == "tokens" and result.get(field, {}).get("known"):
                result[field].setdefault("date", now.date().isoformat())
        subscription = deepcopy(current.get("subscription") or {})
        incoming_metadata = {field: subscription.get(field) for field in ("plan_name", "plan_spec", "expires_at", "metadata_source")}
        old_subscription = previous.get("subscription") or {}
        quota_time = timestamp(subscription.get("updated_at"))
        old_quota_time = timestamp(old_subscription.get("updated_at"))
        if quota_time and old_quota_time and quota_time < old_quota_time:
            subscription = deepcopy(old_subscription)
            retained.append("subscription")
        for window in ("five_hour", "weekly"):
            value = subscription.get(window) or {}
            old_value = old_subscription.get(window) or {}
            if not value.get("known") and old_value.get("known"):
                subscription[window] = deepcopy(old_value)
                subscription[window]["retained"] = True
                retained.append("subscription." + window)
        old_windows = old_subscription.get("quota_windows") or []
        windows = subscription.get("quota_windows") or []
        if not windows and old_windows:
            subscription["quota_windows"] = deepcopy(old_windows)
            retained.append("subscription.quota_windows")
        elif isinstance(windows, list):
            prior = {(item.get("model") or item.get("label")): item for item in old_windows if isinstance(item, dict)}
            for index, item in enumerate(windows):
                if not isinstance(item, dict):
                    continue
                old = prior.get(item.get("model") or item.get("label"))
                if not item.get("known") and old and old.get("known"):
                    windows[index] = deepcopy(old)
                    retained.append("subscription.quota_windows")
        if key == "codex":
            subscription.setdefault("name", "ChatGPT")
        if subscription or any(old_subscription.get(field) for field in ("plan_name", "plan_spec", "expires_at")):
            subscription["metadata_retained"] = []
            for field in ("plan_name", "plan_spec", "expires_at", "metadata_source"):
                if incoming_metadata.get(field):
                    subscription[field] = incoming_metadata[field]
                elif old_subscription.get(field):
                    subscription[field] = old_subscription[field]
                    if field != "metadata_source":
                        subscription["metadata_retained"].append(field)
            for field in ("source", "updated_at", "detail"):
                if not subscription.get(field) and old_subscription.get(field):
                    subscription[field] = old_subscription[field]
            result["subscription"] = subscription
            quota_seen = timestamp(subscription.get("updated_at"))
            if quota_seen and (now - quota_seen).total_seconds() > self.stale_seconds:
                stale = True
            for window in ("five_hour", "weekly"):
                item = subscription.get(window) or {}
                reset = timestamp(item.get("reset_at"))
                if item.get("known") and not item.get("unlimited") and reset and reset < now:
                    stale = True
        result["stale"] = bool(stale or retained)
        result["retained_fields"] = retained
        result["freshness_note"] = "数据待检查更新" if result["stale"] else ""
        result["last_good_at"] = result.get("updated_at")
        # Save only display fields, never raw transcripts, request arguments or credentials.
        keep = ("provider", "name", "state", "state_label", "project", "task", "updated_at",
                "session_id", "progress", "tokens", "subscription", "active_tasks")
        self.values[key] = {k: deepcopy(result.get(k)) for k in keep}
        return result

    def save(self) -> None:
        if self.path is None:
            return
        encoded = json.dumps({"version": 1, "agents": self.values}, ensure_ascii=False, sort_keys=True)
        if encoded == self._saved:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, self.path)
        self._saved = encoded
