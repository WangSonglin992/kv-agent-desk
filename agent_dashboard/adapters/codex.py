"""Read-only adapter for Codex's local rollout JSONL files.

The rollout format is an implementation detail and may evolve.  This adapter
parses the event envelope and a small set of known event payloads defensively.
It never reads Codex authentication state or contacts an API.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime, time as day_time, timedelta, timezone
from pathlib import Path
from typing import Any


_TOKEN_FIELDS = {
    "input": "input_tokens",
    "output": "output_tokens",
    "cached": "cached_input_tokens",
    "reasoning": "reasoning_output_tokens",
    "total": "total_tokens",
}
_LIFECYCLE_START = {"task_started", "turn_started"}
_LIFECYCLE_END = {"task_complete", "turn_complete", "turn_aborted"}
_WAIT_EVENTS = {"exec_approval_request", "request_permissions", "request_user_input"}
_ACTIVITY_EVENTS = {
    "agent_message",
    "agent_reasoning",
    "agent_message_content_delta",
    "item_started",
    "item_completed",
    "exec_command_begin",
    "exec_command_output_delta",
    "exec_command_end",
    "patch_apply_begin",
    "patch_apply_updated",
    "patch_apply_end",
    "mcp_tool_call_begin",
    "mcp_tool_call_end",
    "web_search_begin",
    "web_search_end",
    "image_generation_begin",
    "image_generation_end",
}
_WRAPPED_CONTEXT_TAGS = (
    "user_instructions",
    "environment_context",
    "environments_instructions",
    "apps_instructions",
    "tools",
    "collaboration_mode",
    "multi_agent_mode",
)


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _parse_timestamp(value: Any, local_tz: Any) -> datetime | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).astimezone(local_tz)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_tz)
    try:
        return parsed.astimezone(local_tz)
    except (OverflowError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float) and value.is_integer():
        return max(0, int(value))
    return None


def _usage_vector(raw: Any) -> dict[str, int | None] | None:
    if not isinstance(raw, dict):
        return None
    return {out: _safe_int(raw.get(src)) for out, src in _TOKEN_FIELDS.items()}


def _vector_add(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
    return {key: left[key] + right[key] for key in _TOKEN_FIELDS}


def _vector_subtract(
    current: dict[str, int | None], baseline: dict[str, int | None]
) -> dict[str, int] | None:
    result: dict[str, int] = {}
    for key in _TOKEN_FIELDS:
        after, before = current.get(key), baseline.get(key)
        if after is None or before is None or after < before:
            return None
        result[key] = after - before
    return result


def _normalise_plan(raw: Any) -> list[dict[str, str]] | None:
    if not isinstance(raw, list):
        return None
    result = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("step"), str):
            return None
        status = item.get("status")
        if not isinstance(status, str):
            return None
        status = status.lower()
        if status not in {"pending", "in_progress", "completed"}:
            return None
        result.append({"step": item["step"].strip(), "status": status})
    return result


def _extract_user_text(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    pieces: list[str] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                pieces.append(item["text"])
    elif isinstance(payload.get("message"), str):
        pieces.append(payload["message"])
    text = "\n".join(pieces).strip()
    marker = "## My request for Codex:"
    if marker in text:
        text = text.rsplit(marker, 1)[1].strip()
    for tag in _WRAPPED_CONTEXT_TAGS:
        opening = f"<{tag}>"
        closing = f"</{tag}>"
        if opening in text:
            text = text.split(opening, 1)[0].strip()
        if closing in text:
            text = text.split(closing, 1)[1].strip()
    text = re.sub(r"\s+", " ", text).strip()
    if text.startswith("<") and ">" in text[:80]:
        text = text.split(">", 1)[1].strip()
    return text[:180]


def _path_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("local_path", "path", "value"):
            if isinstance(value.get(key), str):
                return value[key]
    return None


def _plans_from_payload(payload: dict[str, Any]) -> list[dict[str, str]] | None:
    raw = payload.get("plan")
    if raw is None:
        raw = payload.get("steps")
    return _normalise_plan(raw)


def _rate_limit_objects(payload: dict[str, Any]) -> list[tuple[str | None, dict[str, Any]]]:
    """Handle both the current single-object field and newer ID-indexed maps."""
    result: list[tuple[str | None, dict[str, Any]]] = []

    def add(value: Any, hint: str | None = None) -> None:
        if isinstance(value, list):
            for item in value:
                add(item, hint)
        elif isinstance(value, dict):
            if "primary" in value or "secondary" in value or "limit_id" in value:
                result.append((hint, value))
            else:
                for key, item in value.items():
                    add(item, str(key))

    add(payload.get("rate_limits"))
    add(payload.get("rate_limits_by_limit_id"))
    add(payload.get("rateLimitsByLimitId"))
    return result


def _rate_plan_type(payload: dict[str, Any], bucket: dict[str, Any]) -> str | None:
    value = bucket.get("plan_type")
    if not isinstance(value, str):
        value = bucket.get("planType")
    if not isinstance(value, str):
        value = payload.get("plan_type") or payload.get("planType")
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 32 or not re.fullmatch(r"[A-Za-z0-9 _-]+", normalized):
        return None
    return normalized


def _clean_task(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:180]


class CodexAdapter:
    """Read a bounded, cached set of recent Codex rollouts without modifying them.

    ``root`` is the ``sessions`` directory (the directory containing the
    ``YYYY/MM/DD/rollout-*.jsonl`` tree).  Passing it is useful for tests or a
    non-default ``CODEX_HOME``.
    """

    FRESHNESS_SECONDS = 15 * 60
    DISCOVERY_SECONDS = 5.0
    QUOTA_DISCOVERY_SECONDS = 30.0
    LOOKBACK_DAYS = 14
    MAX_RECENT_FILES = 512
    MAX_INITIAL_BYTES = 64 * 1024 * 1024
    MAX_SESSION_META_BYTES = 4 * 1024 * 1024
    MAX_QUOTA_FILES = 256
    QUOTA_TAIL_BYTES = 1024 * 1024
    MAX_RECORDS_PER_FILE = 50_000

    def __init__(self, root: Path | str | None = None):
        if root is None:
            codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
            self.root = codex_home / "sessions"
        else:
            self.root = Path(root).expanduser()
        self._file_cache: dict[str, dict[str, Any]] = {}
        self._quota_cache: dict[str, dict[str, Any]] = {}
        self._files: list[Path] = []
        self._quota_files: list[Path] = []
        self._last_discovery = 0.0
        self._last_quota_discovery = 0.0
        self._discovery_truncated = False
        self._quota_discovery_truncated = False
        self._cache_date: date | None = None

    def collect(self) -> dict[str, Any]:
        return self._collect_at(_local_now())

    def _collect_at(self, now: datetime) -> dict[str, Any]:
        if now.tzinfo is None:
            now = now.astimezone()
        local_tz = now.tzinfo
        today = now.date()
        if self._cache_date != today:
            self._file_cache.clear()
            self._quota_cache.clear()
            self._files = []
            self._quota_files = []
            self._discovery_truncated = False
            self._quota_discovery_truncated = False
            self._cache_date = today
            self._last_discovery = 0.0
            self._last_quota_discovery = 0.0

        day_start = datetime.combine(today, day_time.min, tzinfo=local_tz)
        mono = time.monotonic()
        if mono - self._last_discovery >= self.DISCOVERY_SECONDS:
            self._files, self._discovery_truncated = self._discover_recent_files(now)
            self._last_discovery = mono
        if mono - self._last_quota_discovery >= self.QUOTA_DISCOVERY_SECONDS:
            self._quota_files, self._quota_discovery_truncated = self._discover_quota_files(now)
            self._last_quota_discovery = mono

        active: list[dict[str, Any]] = []
        for path in self._files:
            parsed = self._update_file(path, day_start, now)
            if parsed is not None:
                active.append(parsed)

        rates: list[dict[str, Any]] = []
        active_paths = {str(path) for path in self._files}
        for path in self._quota_files:
            if str(path) not in active_paths:
                rates.extend(self._update_quota_file(path, local_tz))
        for parsed in active:
            rates.extend(parsed["rate_limits"])

        if not self.root.exists():
            offline = self._empty_result(
                available=False,
                state="offline",
                observed_state="offline",
                state_label="未检测到 Codex",
                stale=False,
                detail="未找到本地 Codex 会话目录",
                local_date=today.isoformat(),
            )
            offline["subscription"] = self._subscription(rates)
            return offline

        if not active:
            result = self._empty_result(
                available=True,
                state="unknown",
                observed_state="unknown",
                state_label="数据待更新",
                stale=True,
                detail="未发现近期会话日志",
                local_date=today.isoformat(),
            )
            result["subscription"] = self._subscription(rates)
            return result

        summaries = [self._summarize_file(item) for item in active]
        root_summaries = [item for item in summaries if not item["is_child"]]
        selected = max(root_summaries or summaries, key=lambda item: item["sort_time"])
        active_tasks, active_tasks_known = self._active_task_count(summaries, now)
        updated_at = selected["updated_at"]
        age = (now - selected["sort_time"]).total_seconds() if selected["sort_time"] else None
        stale = age is None or age < -120 or age > self.FRESHNESS_SECONDS
        observed_state = selected["observed_state"]
        state = "unknown" if stale and observed_state not in {"offline", "unknown"} else observed_state
        if observed_state in {"offline", "unknown"}:
            state = observed_state
        label_map = {
            "working": "工作中",
            "waiting": "等待输入",
            "idle": "空闲",
            "offline": "离线",
            "unknown": "数据待更新" if stale else "状态未知",
        }
        progress = selected["progress"]
        task = selected["task"]
        tokens = self._tokens(active, day_start)
        tokens["date"] = today.isoformat()
        result = {
            "provider": "codex",
            "name": "Codex",
            "available": True,
            "state": state,
            "observed_state": observed_state,
            "state_label": label_map.get(state, "状态未知"),
            "stale": stale,
            "project": selected["project"],
            "task": task,
            "updated_at": updated_at,
            "session_id": selected["session_id"],
            "active_tasks": active_tasks,
            "active_tasks_known": active_tasks_known,
            "progress": progress,
            "tokens": tokens,
            "source": "Codex rollout JSONL",
            "detail": "读取本地 Codex rollout 日志",
        }
        result["subscription"] = self._subscription(rates)
        return result

    def _empty_result(
        self,
        *,
        available: bool,
        state: str,
        observed_state: str,
        state_label: str,
        stale: bool,
        detail: str,
        local_date: str,
    ) -> dict[str, Any]:
        return {
            "provider": "codex",
            "name": "Codex",
            "available": available,
            "state": state,
            "observed_state": observed_state,
            "state_label": state_label,
            "stale": stale,
            "project": "",
            "task": "",
            "updated_at": None,
            "session_id": None,
            "active_tasks": None,
            "active_tasks_known": False,
            "progress": {"completed": 0, "total": 0, "steps": [], "known": False},
            "tokens": {
                "input": None,
                "output": None,
                "cached": None,
                "reasoning": None,
                "total": None,
                "scope": "today",
                "known": False,
                "date": local_date,
            },
            "source": "Codex rollout JSONL",
            "detail": detail,
        }

    def _recent_day_dirs(self, now: datetime):
        for offset in range(self.LOOKBACK_DAYS):
            day = now.date() - timedelta(days=offset)
            yield self.root / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"

    def _discover_quota_files(self, now: datetime) -> tuple[list[Path], bool]:
        candidates: list[tuple[float, Path]] = []
        for folder in self._recent_day_dirs(now):
            try:
                for path in folder.glob("rollout-*.jsonl"):
                    try:
                        candidates.append((path.stat().st_mtime, path))
                    except OSError:
                        continue
            except OSError:
                continue
        # A test/user may also provide a flat sessions directory.
        if self.root.exists():
            try:
                for path in self.root.glob("rollout-*.jsonl"):
                    try:
                        candidates.append((path.stat().st_mtime, path))
                    except OSError:
                        continue
            except OSError:
                pass
        candidates.sort(key=lambda pair: pair[0], reverse=True)
        truncated = len(candidates) > self.MAX_QUOTA_FILES
        return [path for _, path in candidates[: self.MAX_QUOTA_FILES]], truncated

    def _discover_recent_files(self, now: datetime) -> tuple[list[Path], bool]:
        cutoff = (now - timedelta(hours=26)).timestamp()
        candidates: list[tuple[float, Path]] = []
        for folder in self._recent_day_dirs(now):
            try:
                for path in folder.glob("rollout-*.jsonl"):
                    try:
                        mtime = path.stat().st_mtime
                    except OSError:
                        continue
                    if cutoff <= mtime <= now.timestamp() + 300:
                        candidates.append((mtime, path))
            except OSError:
                continue
        if self.root.exists():
            try:
                for path in self.root.glob("rollout-*.jsonl"):
                    try:
                        mtime = path.stat().st_mtime
                    except OSError:
                        continue
                    if cutoff <= mtime <= now.timestamp() + 300:
                        candidates.append((mtime, path))
            except OSError:
                pass
        candidates.sort(key=lambda pair: pair[0], reverse=True)
        truncated = len(candidates) > self.MAX_RECENT_FILES
        return [path for _, path in candidates[: self.MAX_RECENT_FILES]], truncated

    def _update_file(
        self, path: Path, day_start: datetime, now: datetime
    ) -> dict[str, Any] | None:
        key = str(path)
        try:
            stat = path.stat()
        except OSError:
            self._file_cache.pop(key, None)
            return None
        cached = self._file_cache.get(key)
        if cached and stat.st_size == cached["size"] and stat.st_mtime_ns == cached["mtime_ns"]:
            return cached
        if cached and stat.st_size > cached["size"] and stat.st_mtime_ns >= cached["mtime_ns"]:
            self._read_appended(path, cached, stat, day_start, now)
            return cached

        parsed = self._scan_file(path, stat, day_start, now)
        if parsed is not None:
            self._file_cache[key] = parsed
        else:
            self._file_cache.pop(key, None)
        return parsed

    def _new_file_cache(self, path: Path) -> dict[str, Any]:
        return {
            "path": path,
            "size": 0,
            "mtime_ns": 0,
            "offset": 0,
            "records": [],
            "rate_limits": [],
            "today_snapshot": None,
            "baseline_snapshot": None,
            "token_records": [],
            "latest_ts": None,
            "first_scanned_ts": None,
            "meta": {},
            "cwd": None,
            "history_truncated": False,
            "incomplete_today": False,
            "record_overflow": False,
        }

    def _scan_file(
        self, path: Path, stat: os.stat_result, day_start: datetime, now: datetime
    ) -> dict[str, Any] | None:
        cached = self._new_file_cache(path)
        try:
            self._read_session_meta_header(path, cached, now.tzinfo)
            with path.open("rb") as stream:
                size = stat.st_size
                start = max(0, size - self.MAX_INITIAL_BYTES)
                if start:
                    cached["history_truncated"] = True
                    stream.seek(start)
                    stream.readline()  # discard the partial first record
                    start = stream.tell()
                else:
                    stream.seek(0)
                cached["offset"] = start
                self._consume_stream(stream, cached, start, day_start, now)
        except (OSError, ValueError):
            return None
        cached["size"] = stat.st_size
        cached["mtime_ns"] = stat.st_mtime_ns
        if cached["history_truncated"]:
            first_ts = cached.get("first_scanned_ts")
            if first_ts is None or first_ts >= day_start:
                cached["incomplete_today"] = True
        return cached

    def _read_session_meta_header(self, path: Path, cached: dict[str, Any], local_tz: Any) -> None:
        try:
            with path.open("rb") as stream:
                raw = stream.readline(self.MAX_SESSION_META_BYTES + 1)
            if len(raw) > self.MAX_SESSION_META_BYTES:
                return
            envelope = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
            return
        if not isinstance(envelope, dict) or envelope.get("type") != "session_meta":
            return
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            return
        cached["meta"] = {
            "session_id": payload.get("session_id") or payload.get("id"),
            "thread_id": payload.get("id"),
            "cwd": _path_text(payload.get("cwd")),
            "parent_thread_id": payload.get("parent_thread_id"),
            "forked_from_id": payload.get("forked_from_id"),
            "thread_source": payload.get("thread_source"),
            "created_at": _parse_timestamp(payload.get("timestamp"), local_tz),
        }
        if cached["meta"].get("cwd"):
            cached["cwd"] = cached["meta"]["cwd"]

    def _read_appended(
        self,
        path: Path,
        cached: dict[str, Any],
        stat: os.stat_result,
        day_start: datetime,
        now: datetime,
    ) -> None:
        try:
            with path.open("rb") as stream:
                stream.seek(cached["offset"])
                start = cached["offset"]
                self._consume_stream(stream, cached, start, day_start, now)
        except OSError:
            return
        cached["size"] = stat.st_size
        cached["mtime_ns"] = stat.st_mtime_ns

    def _consume_stream(
        self,
        stream: Any,
        cached: dict[str, Any],
        start_offset: int,
        day_start: datetime | None,
        now: datetime | None,
    ) -> None:
        data = stream.read()
        complete_end = data.rfind(b"\n") + 1
        complete = data[:complete_end]
        cached["offset"] = start_offset + complete_end
        for raw in complete.splitlines():
            if not raw.strip():
                continue
            try:
                envelope = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
                continue
            if not isinstance(envelope, dict):
                continue
            self._consume_envelope(envelope, cached, day_start, now)

    def _consume_envelope(
        self,
        envelope: dict[str, Any],
        cached: dict[str, Any],
        day_start: datetime | None,
        now: datetime | None,
    ) -> None:
        local_tz = (now or _local_now()).tzinfo
        ts = _parse_timestamp(envelope.get("timestamp"), local_tz)
        if ts is not None:
            if cached["latest_ts"] is None or ts >= cached["latest_ts"]:
                cached["latest_ts"] = ts
            if cached["first_scanned_ts"] is None:
                cached["first_scanned_ts"] = ts
        kind = envelope.get("type")
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            return

        normalized: dict[str, Any] | None = None
        if kind == "session_meta":
            cached["meta"] = {
                "session_id": payload.get("session_id") or payload.get("id"),
                "thread_id": payload.get("id"),
                "cwd": _path_text(payload.get("cwd")),
                "parent_thread_id": payload.get("parent_thread_id"),
                "forked_from_id": payload.get("forked_from_id"),
                "thread_source": payload.get("thread_source"),
                "created_at": _parse_timestamp(payload.get("timestamp"), local_tz),
            }
            if cached["meta"].get("cwd"):
                cached["cwd"] = cached["meta"]["cwd"]
            return
        if kind == "turn_context":
            cwd = _path_text(payload.get("cwd"))
            if cwd:
                cached["cwd"] = cwd
            return
        if kind == "token_usage_record":
            usage = _usage_vector(payload.get("usage"))
            if usage is not None and ts is not None and day_start is not None and ts >= day_start:
                cached["token_records"].append(
                    {
                        "timestamp": ts,
                        "response_id": payload.get("response_id"),
                        "session_id": payload.get("session_id"),
                        "turn_id": payload.get("turn_id"),
                        "usage": usage,
                    }
                )
                self._trim_records(cached)
            return
        if kind == "event_msg":
            event_type = payload.get("type")
            if not isinstance(event_type, str):
                return
            if event_type in _LIFECYCLE_START | _LIFECYCLE_END:
                normalized = {
                    "kind": "lifecycle",
                    "event": event_type,
                    "turn_id": payload.get("turn_id") if isinstance(payload.get("turn_id"), str) else None,
                    "timestamp": ts,
                }
            elif event_type == "plan_update":
                plan = _plans_from_payload(payload)
                normalized = {"kind": "plan", "plan": plan, "timestamp": ts}
            elif event_type in _WAIT_EVENTS:
                normalized = {"kind": "wait", "timestamp": ts}
            elif event_type in _ACTIVITY_EVENTS:
                normalized = {"kind": "activity", "timestamp": ts}
            elif event_type == "token_count":
                info = payload.get("info")
                total = _usage_vector(info.get("total_token_usage")) if isinstance(info, dict) else None
                if total is not None and ts is not None and day_start is not None:
                    snapshot = {"timestamp": ts, "usage": total}
                    if ts >= day_start:
                        cached["today_snapshot"] = snapshot
                    elif cached["baseline_snapshot"] is None or ts >= cached["baseline_snapshot"]["timestamp"]:
                        cached["baseline_snapshot"] = snapshot
                for hint, bucket in _rate_limit_objects(payload):
                    bucket_id = bucket.get("limit_id")
                    if not isinstance(bucket_id, str) and hint:
                        bucket_id = hint
                    if not isinstance(bucket_id, str):
                        continue
                    for slot in ("primary", "secondary"):
                        window = bucket.get(slot)
                        if not isinstance(window, dict):
                            continue
                        minutes = _safe_int(window.get("window_minutes"))
                        used = window.get("used_percent")
                        if isinstance(used, bool) or not isinstance(used, (int, float)):
                            used = None
                        else:
                            used = float(used)
                        cached["rate_limits"].append(
                            {
                                "limit_id": bucket_id.lower(),
                                "window_minutes": minutes,
                                "used_percent": used,
                                "resets_at": window.get("resets_at"),
                                "timestamp": ts,
                                "plan_type": _rate_plan_type(payload, bucket),
                            }
                        )
                if ts is not None:
                    cached["records"].append({"kind": "activity", "timestamp": ts})
                    self._trim_records(cached)
                return
            if normalized is not None and ts is not None:
                cached["records"].append(normalized)
                self._trim_records(cached)
            return
        if kind == "response_item":
            item_type = payload.get("type")
            if payload.get("role") == "user":
                text = _extract_user_text(payload)
                if text:
                    normalized = {"kind": "user", "text": text, "timestamp": ts}
            elif item_type in {"function_call", "custom_tool_call"} and payload.get("name") == "update_plan":
                args = payload.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = None
                if isinstance(args, dict):
                    plan = _plans_from_payload(args)
                    normalized = {"kind": "plan", "plan": plan, "timestamp": ts}
            elif item_type in {"function_call_output", "custom_tool_call_output", "message"}:
                normalized = {"kind": "activity", "timestamp": ts}
            if normalized is not None and ts is not None:
                cached["records"].append(normalized)
                self._trim_records(cached)

    def _trim_records(self, cached: dict[str, Any]) -> None:
        for key in ("records", "token_records"):
            records = cached[key]
            if len(records) > self.MAX_RECORDS_PER_FILE:
                del records[: len(records) - self.MAX_RECORDS_PER_FILE]
                cached["record_overflow"] = True

    def _update_quota_file(self, path: Path, local_tz: Any) -> list[dict[str, Any]]:
        key = str(path)
        try:
            stat = path.stat()
        except OSError:
            self._quota_cache.pop(key, None)
            return []
        cached = self._quota_cache.get(key)
        if cached and cached["size"] == stat.st_size and cached["mtime_ns"] == stat.st_mtime_ns:
            return cached["rate_limits"]
        rates: list[dict[str, Any]] = []
        try:
            with path.open("rb") as stream:
                start = max(0, stat.st_size - self.QUOTA_TAIL_BYTES)
                stream.seek(start)
                if start:
                    stream.readline()
                raw_lines = stream.read().splitlines()
            for raw in raw_lines:
                try:
                    envelope = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(envelope, dict) or envelope.get("type") != "event_msg":
                    continue
                payload = envelope.get("payload")
                if not isinstance(payload, dict) or payload.get("type") != "token_count":
                    continue
                ts = _parse_timestamp(envelope.get("timestamp"), local_tz)
                for hint, bucket in _rate_limit_objects(payload):
                    bucket_id = bucket.get("limit_id")
                    if not isinstance(bucket_id, str) and hint:
                        bucket_id = hint
                    if not isinstance(bucket_id, str):
                        continue
                    for slot in ("primary", "secondary"):
                        window = bucket.get(slot)
                        if not isinstance(window, dict):
                            continue
                        used = window.get("used_percent")
                        used = float(used) if isinstance(used, (int, float)) and not isinstance(used, bool) else None
                        rates.append(
                            {
                                "limit_id": bucket_id.lower(),
                                "window_minutes": _safe_int(window.get("window_minutes")),
                                "used_percent": used,
                                "resets_at": window.get("resets_at"),
                                "timestamp": ts,
                                "plan_type": _rate_plan_type(payload, bucket),
                            }
                        )
        except OSError:
            return []
        self._quota_cache[key] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "rate_limits": rates}
        return rates

    def _summarize_file(self, cached: dict[str, Any]) -> dict[str, Any]:
        records = cached["records"]
        lifecycle = [record for record in records if record["kind"] == "lifecycle"]
        starts = [record for record in lifecycle if record["event"] in _LIFECYCLE_START]
        latest_start = starts[-1] if starts else None
        latest_lifecycle = lifecycle[-1] if lifecycle else None
        terminal = None
        if latest_start is not None:
            for record in lifecycle:
                if record["timestamp"] <= latest_start["timestamp"]:
                    continue
                if record["event"] in _LIFECYCLE_END:
                    if latest_start["turn_id"] and record["turn_id"] == latest_start["turn_id"]:
                        terminal = record
        if latest_start is None:
            if latest_lifecycle and latest_lifecycle["event"] in {"task_complete", "turn_complete"} and latest_lifecycle["turn_id"]:
                observed = "idle"
            else:
                observed = "unknown"
        elif terminal is not None:
            observed = "idle"
        elif latest_lifecycle is not None and latest_lifecycle["event"] in _LIFECYCLE_END:
            # A terminal for another/unspecified turn makes concurrent state ambiguous.
            observed = "unknown"
        elif latest_start["turn_id"]:
            observed = "working"
        else:
            observed = "unknown"

        task = ""
        progress = {"completed": 0, "total": 0, "steps": [], "known": False}
        start_index = records.index(latest_start) if latest_start in records else -1
        end_index = len(records)
        if terminal is not None:
            end_index = records.index(terminal)
        segment = records[start_index + 1 : end_index] if start_index >= 0 else records
        user_records = [record for record in segment if record["kind"] == "user"]
        if user_records:
            task = _clean_task(user_records[-1]["text"])
        plan_records = [record for record in segment if record["kind"] == "plan"]
        if plan_records:
            latest_plan = plan_records[-1]
            if latest_plan["plan"] is not None and (not user_records or user_records[-1]["timestamp"] <= latest_plan["timestamp"]):
                steps = latest_plan["plan"]
                progress = {
                    "completed": sum(step["status"] == "completed" for step in steps),
                    "total": len(steps),
                    "steps": steps,
                    "known": True,
                }

        waiting = False
        if observed == "working" and latest_start is not None:
            wait_records = [r for r in records if r["kind"] == "wait" and r["timestamp"] >= latest_start["timestamp"]]
            activity_records = [r for r in records if r["kind"] == "activity" and r["timestamp"] >= latest_start["timestamp"]]
            latest_wait = wait_records[-1] if wait_records else None
            latest_activity = activity_records[-1] if activity_records else None
            waiting = bool(latest_wait and (latest_activity is None or latest_wait["timestamp"] > latest_activity["timestamp"]))
            if waiting:
                observed = "waiting"

        meta = cached["meta"]
        cwd = cached["cwd"] or meta.get("cwd") or ""
        project = Path(cwd.replace("\\", "/")).name if cwd else ""
        session_id = meta.get("session_id")
        thread_id = meta.get("thread_id")
        child = bool(meta.get("parent_thread_id")) or str(meta.get("thread_source") or "").lower() in {"subagent", "guardian_review"}
        updated_at = cached["latest_ts"]
        return {
            "observed_state": observed,
            "project": project,
            "task": task,
            "progress": progress,
            "updated_at": updated_at.isoformat() if updated_at else None,
            "sort_time": updated_at,
            "session_id": session_id if isinstance(session_id, str) else None,
            "thread_id": thread_id if isinstance(thread_id, str) else None,
            "is_child": child,
            "cache": cached,
        }

    def _active_task_count(
        self, summaries: list[dict[str, Any]], now: datetime
    ) -> tuple[int | None, bool]:
        roots = [item for item in summaries if not item["is_child"]]
        if not roots or self._discovery_truncated:
            return None, False
        by_session: dict[str, dict[str, Any]] = {}
        for item in roots:
            identity = item.get("thread_id") or item.get("session_id")
            if not identity:
                return None, False
            key = str(identity)
            previous = by_session.get(key)
            if previous is None or (
                item["sort_time"] is not None
                and (previous["sort_time"] is None or item["sort_time"] >= previous["sort_time"])
            ):
                by_session[key] = item

        working = 0
        for item in by_session.values():
            observed = item["observed_state"]
            at = item["sort_time"]
            age = (now - at).total_seconds() if at is not None else None
            if observed not in {"working", "waiting", "idle"}:
                return None, False
            if age is None or age < -120:
                return None, False
            if observed == "idle":
                continue
            if age > self.FRESHNESS_SECONDS:
                return None, False
            if observed == "working":
                working += 1
        return working, True

    def _tokens(self, active: list[dict[str, Any]], day_start: datetime) -> dict[str, Any]:
        totals = {key: 0 for key in _TOKEN_FIELDS}
        has_usage = False
        known = not self._discovery_truncated
        response_ids: set[Any] = set()
        sessions_with_records: set[str] = set()
        records_seen: list[tuple[str, dict[str, Any]]] = []
        for cached in active:
            session_id = cached["meta"].get("session_id") or cached["meta"].get("id") or str(cached["path"])
            session_key = str(session_id)
            if cached["incomplete_today"] or cached["record_overflow"]:
                known = False
            for record in cached["token_records"]:
                if record["timestamp"] < day_start:
                    continue
                vector = record["usage"]
                if any(value is None for value in vector.values()):
                    known = False
                    continue
                response_id = record.get("response_id")
                if response_id:
                    dedupe_key = ("response", response_id)
                else:
                    dedupe_key = (
                        "record",
                        record["timestamp"].isoformat(),
                        record.get("turn_id"),
                        tuple(vector.get(field) for field in _TOKEN_FIELDS),
                    )
                if dedupe_key in response_ids:
                    continue
                response_ids.add(dedupe_key)
                sessions_with_records.add(session_key)
                records_seen.append((session_key, vector))

        for _, vector in records_seen:
            totals = _vector_add(totals, vector)  # type: ignore[arg-type]
            has_usage = True

        # Older rollouts may not contain token_usage_record. Use cumulative
        # token_count snapshots only when a same-session day-boundary baseline
        # (or a complete rollout beginning after local midnight) is available.
        grouped: dict[str, list[dict[str, Any]]] = {}
        for cached in active:
            session_id = cached["meta"].get("session_id") or cached["meta"].get("id") or str(cached["path"])
            grouped.setdefault(str(session_id), []).append(cached)
        for session_key, group in grouped.items():
            if session_key in sessions_with_records:
                continue
            snapshots = [c["today_snapshot"] for c in group if c["today_snapshot"] is not None]
            if not snapshots:
                latest_today_activity = any(
                    c["latest_ts"] is not None and c["latest_ts"].date() == day_start.date()
                    for c in group
                )
                if latest_today_activity:
                    known = False
                continue
            current = max(snapshots, key=lambda item: item["timestamp"])
            baselines = [c["baseline_snapshot"] for c in group if c["baseline_snapshot"] is not None]
            baseline = max(baselines, key=lambda item: item["timestamp"]) if baselines else None
            if baseline is not None:
                delta = _vector_subtract(current["usage"], baseline["usage"])
            else:
                metas = [c["meta"].get("created_at") for c in group if c["meta"].get("created_at") is not None]
                created_at = min(metas) if metas else None
                if created_at is not None and created_at >= day_start:
                    delta = current["usage"] if all(v is not None for v in current["usage"].values()) else None
                else:
                    delta = None
            if delta is None:
                known = False
                continue
            totals = _vector_add(totals, delta)
            has_usage = True

        if not has_usage:
            values: dict[str, int | None] = {key: None for key in _TOKEN_FIELDS}
            known = False
        else:
            values = totals
        return {
            **values,
            "scope": "today",
            "known": bool(known and has_usage),
        }

    def _subscription(self, rates: list[dict[str, Any]]) -> dict[str, Any]:
        codex_rates = [rate for rate in rates if rate.get("limit_id") == "codex"]
        weekly = [rate for rate in codex_rates if rate.get("window_minutes") == 7 * 24 * 60]
        five_hour = [rate for rate in codex_rates if rate.get("window_minutes") == 300]
        latest_five = max(five_hour, key=lambda item: item["timestamp"] or datetime.min.replace(tzinfo=timezone.utc), default=None)
        five_window = {"remaining_percent": None, "reset_at": None, "known": False}
        if latest_five is not None:
            used = latest_five.get("used_percent")
            reset = _parse_timestamp(latest_five.get("resets_at"), _local_now().tzinfo)
            if isinstance(used, (int, float)) and not isinstance(used, bool) and reset is not None:
                five_window = {"remaining_percent": max(0.0, min(100.0, 100.0 - float(used))),
                               "reset_at": reset.isoformat(), "known": True}
        latest = max(weekly, key=lambda item: item["timestamp"] or datetime.min.replace(tzinfo=timezone.utc), default=None)
        plan_records = [
            rate
            for rate in codex_rates
            if isinstance(rate.get("plan_type"), str) and rate["plan_type"].strip()
        ]
        latest_plan_timestamp = max(
            (
                rate.get("timestamp")
                for rate in plan_records
                if isinstance(rate.get("timestamp"), datetime)
            ),
            default=None,
        )
        latest_plan_values = {
            rate["plan_type"].strip().lower()
            for rate in plan_records
            if rate.get("timestamp") == latest_plan_timestamp
        }
        plan_type = next(iter(latest_plan_values)) if len(latest_plan_values) == 1 else None
        known_plan_types = {"free", "plus", "pro", "business", "enterprise", "team", "edu"}
        plan_name = f"ChatGPT {plan_type.title()}" if plan_type in known_plan_types else None
        metadata_source = (
            "Codex rollout token_count.rate_limits.plan_type (no subscription expiry field)"
            if plan_name
            else "Codex rollout token_count.rate_limits (plan type unavailable; no subscription expiry field)"
        )
        remaining: float | None = None
        reset_at: str | None = None
        updated_at: str | None = None
        weekly_known = False
        if latest is not None:
            used = latest.get("used_percent")
            reset = _parse_timestamp(latest.get("resets_at"), _local_now().tzinfo)
            if isinstance(used, (int, float)) and not isinstance(used, bool):
                remaining = max(0.0, min(100.0, 100.0 - float(used)))
            if reset is not None:
                reset_at = reset.isoformat()
            stamp = latest.get("timestamp")
            updated_at = stamp.isoformat() if isinstance(stamp, datetime) else None
            weekly_known = remaining is not None and reset_at is not None
        five_stamp = latest_five.get("timestamp") if latest_five else None
        if isinstance(five_stamp, datetime) and (updated_at is None or five_stamp.isoformat() > updated_at):
            updated_at = five_stamp.isoformat()
        if isinstance(latest_plan_timestamp, datetime) and (
            updated_at is None or latest_plan_timestamp.isoformat() > updated_at
        ):
            updated_at = latest_plan_timestamp.isoformat()
        return {
            "name": "ChatGPT",
            "five_hour": five_window,
            "weekly": {"remaining_percent": remaining, "reset_at": reset_at, "known": weekly_known},
            "updated_at": updated_at,
            "source": "Codex rollout token_count.rate_limits (limit_id=codex)",
            "plan_name": plan_name,
            "plan_spec": None,
            "expires_at": None,
            "metadata_source": metadata_source,
        }
