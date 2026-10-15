"""Read-only adapter for ZCode sessions and the official quota API.

Observed Windows schema (ZCode 2026-10): ``session`` stores session id,
directory, title, and millisecond ``time_updated``; ``todo`` stores per-session
step content/status/position; ``tool_usage`` and ``turn_usage`` store explicit
tool/turn statuses and timestamps; ``model_usage`` stores request-level token
counts keyed by ``logical_request_id`` and ``attempt_index``. The database is
WAL-backed, so this adapter opens it with SQLite ``mode=ro`` and a short timeout.
It never reads message bodies or tool arguments. Quota credentials are read
only by :mod:`zcode_quota` from the currently selected provider config.
"""

from __future__ import annotations

import csv
from contextlib import closing
from datetime import datetime
import os
from pathlib import Path
import sqlite3
import subprocess
import time
from typing import Any

from .zcode_quota import ZCodeQuotaCollector


_STATE_LABELS = {
    "working": "工作中",
    "waiting": "等待输入",
    "idle": "空闲",
    "offline": "未接入",
    "unknown": "状态未知",
}

_ACTIVE = {"running", "in_progress", "started", "streaming", "working"}
_WAITING = {
    "waiting",
    "pending",
    "queued",
    "blocked",
    "approval_required",
    "requires_approval",
    "awaiting_approval",
}
# A locally observed ZCode input status.  A promoted input is linked to model
# requests through model_usage.parent_user_message_id; it is only treated as
# ongoing when that linked turn has recent model/tool activity and no terminal
# turn row.  Other unobserved input statuses are deliberately not guessed.
_PROMOTED_INPUT = {"promoted"}
_COMPLETED = {"completed", "complete", "success", "succeeded", "done"}
_TERMINAL = _COMPLETED | {"cancelled", "canceled", "error", "failed"}
_APPROVAL_WAITING = {"pending", "required", "waiting", "requested", "awaiting"}

_REQUIRED_COLUMNS = {
    "session": {"id", "directory", "title", "time_updated"},
    "todo": {"session_id", "content", "status", "position", "time_created", "time_updated"},
    "tool_usage": {
        "session_id", "turn_id", "tool_name", "status", "approval_status",
        "started_at", "first_output_at", "completed_at",
    },
    "turn_usage": {"session_id", "turn_id", "user_message_id", "status", "started_at", "first_token_at", "completed_at"},
    "session_input": {"session_id", "promoted_message_id", "status", "time_created", "time_updated"},
    "model_usage": {
        "id",
        "logical_request_id",
        "attempt_index",
        "session_id",
        "turn_id",
        "parent_user_message_id",
        "status",
        "started_at",
        "first_token_at",
        "completed_at",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "computed_total_tokens",
    },
}


class ZCodeAdapter:
    """Read ZCode session/progress/token data without modifying its database.

    ``root`` is the ZCode data directory (normally ``%USERPROFILE%\\.zcode``).
    Passing the SQLite file itself is also supported for tests and diagnostics.
    """

    IO_TIMEOUT_SECONDS = 0.15
    TERMINAL_FRESH_SECONDS = 120
    ACTIVE_FRESH_SECONDS = 15 * 60
    PROCESS_CACHE_SECONDS = 5
    MAX_TEXT_CHARS = 160
    SESSION_LOOKBACK_SECONDS = 36 * 60 * 60
    MAX_RECENT_SESSIONS = 128

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root).expanduser() if root is not None else Path.home() / ".zcode"
        self._process_checked_at = 0.0
        self._process_cached: bool | None = None
        self._quota_collector = ZCodeQuotaCollector(self.root)

    def collect(self) -> dict[str, Any]:
        now = datetime.now().astimezone()
        result = self._empty_result(now.date().isoformat())
        db_path = self._db_path()
        result["source"] = str(db_path)
        result["subscription"] = self._quota_collector.collect()

        if not db_path.is_file():
            result["detail"] = "未找到 ZCode 本地活动数据库。"
            return result

        try:
            with closing(self._open_readonly(db_path)) as connection:
                self._validate_schema(connection)
                cutoff_ms = int((now.timestamp() - self.SESSION_LOOKBACK_SECONDS) * 1000)
                recent_sessions = self._recent_sessions(connection, cutoff_ms)
                lifecycle_sessions, lifecycle_scan_truncated = self._recent_lifecycle_sessions(connection, cutoff_ms)
                sessions = self._merge_session_lists(lifecycle_sessions, recent_sessions)
                session = sessions[0] if sessions else None
                active_tasks = (
                    None if lifecycle_scan_truncated
                    else self._count_active_tasks(connection, sessions, cutoff_ms, now)
                )
                progress = self._read_progress(connection, session["id"] if session else None)
                tokens = self._read_tokens(connection, now)
                observed_state, activity_at, state_at = self._observe_state(
                    connection, session["id"] if session else None, session, cutoff_ms
                )
        except sqlite3.OperationalError as exc:
            result["available"] = db_path.is_file()
            result["state"] = "unknown"
            result["state_label"] = _STATE_LABELS["unknown"]
            result["detail"] = self._database_error_detail(exc)
            return result
        except (sqlite3.DatabaseError, OSError, ValueError, TypeError, KeyError):
            result["state"] = "unknown"
            result["state_label"] = _STATE_LABELS["unknown"]
            result["detail"] = "ZCode 数据库不可读或结构不兼容，状态与指标未知。"
            return result

        process_running = self._zcode_process_running()
        result.update(
            {
                "available": True,
                "project": self._clean_text(session.get("directory") if session else ""),
                "task": self._clean_text(session.get("title") if session else "") or "尚未命名任务",
                "session_id": session.get("id") if session else None,
                "updated_at": self._iso_timestamp(activity_at),
                "active_tasks": active_tasks,
                "progress": progress,
                "tokens": tokens,
                "observed_state": observed_state,
            }
        )

        if session is None and process_running is True:
            observed_state = "idle"
            result["observed_state"] = observed_state

        state_age = self._age_seconds(state_at, now.timestamp())
        stale = False
        if process_running is False:
            state = "offline"
            stale = True
            result["detail"] = "ZCode 当前未运行；显示可读取的最近本地记录。"
        elif observed_state in {"working", "waiting"}:
            stale = state_age is None or state_age > self.ACTIVE_FRESH_SECONDS
            state = "unknown" if stale else observed_state
            result["detail"] = (
                "最近的运行记录较旧，当前状态待检查。"
                if stale
                else "状态来自 ZCode 当前会话的工具/轮次记录。"
            )
        elif activity_at is None and process_running is True:
            state = "idle"
            result["detail"] = "ZCode 正在运行，尚无本地会话记录。"
        else:
            stale = state_age is None or state_age > self.TERMINAL_FRESH_SECONDS
            state = "unknown" if stale else observed_state
            result["detail"] = (
                "最近的会话记录较旧，当前状态待检查。"
                if stale
                else "状态来自 ZCode 最近的已结束会话记录。"
            )

        if process_running is None and state == "idle":
            # On non-Windows hosts process presence cannot be checked; don't
            # infer a live idle state from old persisted records.
            if state_age is None or state_age > self.TERMINAL_FRESH_SECONDS:
                state = "unknown"
                stale = True

        result["state"] = state
        result["state_label"] = _STATE_LABELS[state]
        result["stale"] = stale
        return result

    def _db_path(self) -> Path:
        if self.root.is_file() or self.root.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
            return self.root
        return self.root / "cli" / "db" / "db.sqlite"

    def _empty_result(self, date: str) -> dict[str, Any]:
        return {
            "provider": "zcode",
            "name": "ZCode",
            "available": False,
            "state": "offline",
            "state_label": _STATE_LABELS["offline"],
            "observed_state": "unknown",
            "stale": True,
            "project": "",
            "task": "",
            "session_id": None,
            "updated_at": None,
            "active_tasks": None,
            "progress": {"completed": 0, "total": 0, "steps": [], "known": False},
            "tokens": {
                "input": None,
                "output": None,
                "cached": None,
                "reasoning": None,
                "total": None,
                "scope": "today",
                "date": date,
                "known": False,
            },
            "subscription": self._unknown_subscription(),
            "source": "",
            "detail": "",
        }

    def _open_readonly(self, db_path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(
            db_path.resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=self.IO_TIMEOUT_SECONDS,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        for table, required in _REQUIRED_COLUMNS.items():
            actual = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            if not required.issubset(actual):
                raise ValueError("unsupported ZCode schema")

    def _recent_sessions(self, connection: sqlite3.Connection, cutoff_ms: int) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT id, directory, title, time_updated FROM session "
            "WHERE time_updated>=? ORDER BY time_updated DESC LIMIT ?",
            (cutoff_ms, self.MAX_RECENT_SESSIONS),
        ).fetchall()
        return [dict(row) for row in rows]

    def _recent_lifecycle_sessions(
        self, connection: sqlite3.Connection, cutoff_ms: int
    ) -> tuple[list[dict[str, Any]], bool]:
        """Prioritize sessions with recent explicit in-flight lifecycle rows.

        The session table is small in the observed schema, but its update time
        is not indexed and can lag background-session activity.  These bounded
        status queries use the activity tables' started_at indexes instead.
        """
        active_statuses = tuple(sorted(_ACTIVE | _WAITING))
        placeholders = ",".join("?" for _ in active_statuses)
        candidates: dict[str, int] = {}
        truncated = False
        for table in ("tool_usage", "turn_usage", "model_usage"):
            rows = connection.execute(
                f"SELECT session_id, started_at FROM {table} "
                f"WHERE started_at>=? AND lower(status) IN ({placeholders}) "
                "ORDER BY started_at DESC LIMIT ?",
                (cutoff_ms, *active_statuses, 4097),
            ).fetchall()
            if len(rows) > 4096:
                truncated = True
                rows = rows[:4096]
            for row in rows:
                session_id = str(row["session_id"] or "")
                activity_at = self._timestamp_ms(row["started_at"])
                if session_id and activity_at is not None:
                    candidates[session_id] = max(candidates.get(session_id, 0), activity_at)

        ordered = sorted(candidates.items(), key=lambda pair: pair[1], reverse=True)
        ids = [session_id for session_id, _ in ordered[: self.MAX_RECENT_SESSIONS + 1]]
        if not ids:
            return [], truncated
        placeholders = ",".join("?" for _ in ids)
        rows = connection.execute(
            f"SELECT id, directory, title, time_updated FROM session WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        by_id = {str(row["id"]): dict(row) for row in rows}
        return [by_id[session_id] for session_id in ids if session_id in by_id], truncated

    def _merge_session_lists(
        self,
        lifecycle_sessions: list[dict[str, Any]],
        recent_sessions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in (*lifecycle_sessions, *recent_sessions):
            session_id = str(row.get("id") or "")
            if session_id and session_id not in seen:
                merged.append(row)
                seen.add(session_id)
            if len(merged) >= self.MAX_RECENT_SESSIONS + 1:
                break
        return merged

    def _count_active_tasks(
        self,
        connection: sqlite3.Connection,
        sessions: list[dict[str, Any]],
        cutoff_ms: int,
        now: datetime,
    ) -> int | None:
        # A full 128-row page may be truncated. Keep the count unknown instead
        # of presenting a partial result as the user's total active sessions.
        if len(sessions) >= self.MAX_RECENT_SESSIONS:
            return None
        session_ids = [str(row["id"]) for row in sessions if row.get("id")]
        if not session_ids:
            return 0
        events_by_session = self._read_lifecycle_events(connection, session_ids, cutoff_ms)
        if events_by_session is None:
            return None

        active = 0
        for events in events_by_session.values():
            state, state_at = self._choose_lifecycle_state(events)
            if state != "working":
                continue
            age = self._age_seconds(state_at, now.timestamp())
            if age is not None and age <= self.ACTIVE_FRESH_SECONDS:
                active += 1
        return active

    def _read_lifecycle_events(
        self,
        connection: sqlite3.Connection,
        session_ids: list[str],
        cutoff_ms: int,
    ) -> dict[str, list[dict[str, Any]]] | None:
        events_by_session: dict[str, list[dict[str, Any]]] = {session_id: [] for session_id in session_ids}
        queries = (
            (
                "tool_usage",
                "tool",
                "SELECT session_id, turn_id, status, approval_status, started_at, first_output_at, completed_at "
                "FROM tool_usage WHERE started_at>=? "
                "ORDER BY started_at DESC LIMIT 4097",
                4096,
            ),
            (
                "turn_usage",
                "turn",
                "SELECT session_id, turn_id, user_message_id, status, started_at, first_token_at, completed_at "
                "FROM turn_usage WHERE started_at>=? "
                "ORDER BY started_at DESC LIMIT 2049",
                2048,
            ),
            (
                "model_usage",
                "model",
                "SELECT session_id, turn_id, parent_user_message_id, status, started_at, first_token_at, completed_at "
                "FROM model_usage WHERE started_at>=? "
                "ORDER BY started_at DESC LIMIT 2049",
                2048,
            ),
        )
        for table, source, sql, max_rows in queries:
            rows = connection.execute(sql, (cutoff_ms,)).fetchall()
            if len(rows) > max_rows:
                return None
            for row in rows:
                session_id = str(row["session_id"] or "")
                if session_id not in events_by_session:
                    continue
                status = self._normalize_status(row["status"])
                approval = self._normalize_status(row["approval_status"]) if source == "tool" else ""
                semantic = self._semantic_state(status, approval)
                # Tool/model completion settles only that operation. The turn
                # row is the authoritative enclosing-turn terminal signal.
                if source not in {"turn", "input"} and semantic == "idle":
                    semantic = None
                event_time = max(
                    (
                        stamp
                        for key in ("started_at", "first_output_at", "first_token_at", "completed_at")
                        if key in row.keys()
                        if (stamp := self._timestamp_ms(row[key])) is not None
                    ),
                    default=None,
                )
                events_by_session[session_id].append(
                    {
                        "source": source,
                        "turn_id": row["turn_id"],
                        "user_message_id": (
                            row["user_message_id"] if source == "turn" else
                            row["parent_user_message_id"] if source == "model" else None
                        ),
                        "status": status,
                        "state": semantic,
                        "time": event_time,
                    }
                )

        input_rows = connection.execute(
            "SELECT session_id, promoted_message_id, status, time_created, time_updated "
            f"FROM session_input WHERE session_id IN ({','.join('?' for _ in session_ids)}) "
            "AND (time_created>=? OR time_updated>=?) ORDER BY time_updated DESC LIMIT 4097",
            (*session_ids, cutoff_ms, cutoff_ms),
        ).fetchall()
        if len(input_rows) > 4096:
            return None
        for row in input_rows:
            status = self._normalize_status(row["status"])
            state = self._semantic_state(status)
            input_time = max(
                (
                    stamp
                    for key in ("time_created", "time_updated")
                    if (stamp := self._timestamp_ms(row[key])) is not None
                ),
                default=None,
            )
            events_by_session[str(row["session_id"])].append(
                {
                    "source": "input",
                    "turn_id": None,
                    "user_message_id": row["promoted_message_id"],
                    "status": status,
                    "state": state,
                    "time": input_time,
                }
            )

        for events in events_by_session.values():
            self._add_promoted_input_activity(events)
        return events_by_session

    @staticmethod
    def _add_promoted_input_activity(events: list[dict[str, Any]]) -> None:
        """Bridge model/tool gaps for an input proven to have been promoted.

        ZCode's observed `promoted` input has a `promoted_message_id` that
        matches model_usage.parent_user_message_id.  If the linked turn has
        recent model/tool activity and has no terminal turn/input row, keep it
        working through short gaps between individual calls. Freshness is still
        enforced by the caller, so this cannot remain active indefinitely.
        """
        terminal_turns = [
            event for event in events
            if event["source"] == "turn" and event["state"] == "idle"
        ]
        terminal_inputs = [
            event for event in events
            if event["source"] == "input" and event["state"] == "idle"
        ]
        model_events = [event for event in events if event["source"] == "model"]
        for input_event in events:
            message_id = input_event.get("user_message_id")
            if input_event["source"] != "input" or input_event["status"] not in _PROMOTED_INPUT or not message_id:
                continue
            linked_models = [
                event for event in model_events
                if event.get("user_message_id") == message_id
                and event.get("status") in (_COMPLETED | _ACTIVE | _WAITING)
                and event.get("time") is not None
                and (input_event.get("time") is None or event["time"] >= input_event["time"])
            ]
            if not linked_models:
                continue
            linked_turn_ids = {event.get("turn_id") for event in linked_models if event.get("turn_id")}
            has_terminal = any(
                (event.get("user_message_id") == message_id)
                or (event.get("turn_id") in linked_turn_ids)
                for event in (*terminal_turns, *terminal_inputs)
            )
            if has_terminal:
                continue
            related_activity = [
                event for event in events
                if event["source"] in {"model", "tool", "turn"}
                and event.get("status") in (_COMPLETED | _ACTIVE | _WAITING)
                and event.get("time") is not None
                and (
                    event.get("user_message_id") == message_id
                    or (event.get("turn_id") and event.get("turn_id") in linked_turn_ids)
                )
            ]
            if not related_activity:
                continue
            latest = max(related_activity, key=lambda event: event["time"])
            events.append(
                {
                    "source": "promoted_input",
                    "turn_id": latest.get("turn_id"),
                    "user_message_id": message_id,
                    "status": "promoted",
                    "state": "working",
                    "time": latest["time"],
                }
            )

    def _read_progress(
        self, connection: sqlite3.Connection, session_id: str | None
    ) -> dict[str, Any]:
        if not session_id:
            return {"completed": 0, "total": 0, "steps": [], "known": False}
        rows = connection.execute(
            "SELECT content, status FROM todo WHERE session_id=? "
            "ORDER BY position ASC, time_created ASC",
            (session_id,),
        ).fetchall()
        steps = []
        completed = 0
        for row in rows:
            status = self._normalize_status(row["status"])
            if status in _COMPLETED:
                completed += 1
            steps.append(
                {
                    "step": self._clean_text(row["content"]),
                    "status": status or "unknown",
                }
            )
        return {"completed": completed, "total": len(steps), "steps": steps, "known": bool(steps)}

    def _read_tokens(self, connection: sqlite3.Connection, now: datetime) -> dict[str, Any]:
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_start_ms = int(today.timestamp() * 1000)
        row = connection.execute(
            "WITH ranked AS ("
            " SELECT status, input_tokens, output_tokens, reasoning_tokens,"
            "        cache_creation_input_tokens, cache_read_input_tokens,"
            "        ROW_NUMBER() OVER ("
            "          PARTITION BY COALESCE(NULLIF(logical_request_id, ''), id)"
            "          ORDER BY attempt_index DESC, started_at DESC"
            "        ) AS attempt_rank"
            " FROM model_usage"
            " WHERE started_at >= ? AND lower(status) IN ('completed','complete','success','succeeded')"
            ")"
            " SELECT COUNT(*) AS requests,"
            "        COALESCE(SUM(COALESCE(input_tokens, 0)), 0) AS input_tokens,"
            "        COALESCE(SUM(COALESCE(output_tokens, 0)), 0) AS output_tokens,"
            "        COALESCE(SUM(COALESCE(reasoning_tokens, 0)), 0) AS reasoning_tokens,"
            "        COALESCE(SUM(COALESCE(cache_creation_input_tokens, 0) +"
            "                       COALESCE(cache_read_input_tokens, 0)), 0) AS cached_tokens"
            " FROM ranked WHERE attempt_rank=1",
            (day_start_ms,),
        ).fetchone()
        input_tokens = int(row["input_tokens"] or 0)
        output_tokens = int(row["output_tokens"] or 0)
        return {
            "input": input_tokens,
            "output": output_tokens,
            "cached": int(row["cached_tokens"] or 0),
            "reasoning": int(row["reasoning_tokens"] or 0),
            # Cached input is a subset and reasoning is part of output; do not
            # add either field a second time.
            "total": input_tokens + output_tokens,
            "scope": "today",
            "date": now.date().isoformat(),
            "known": True,
        }

    @staticmethod
    def _unknown_subscription() -> dict[str, Any]:
        unknown_window = {"remaining_percent": None, "reset_at": None, "known": False}
        return {
            "name": "ZCode",
            "five_hour": dict(unknown_window),
            "weekly": dict(unknown_window),
            "updated_at": None,
            "source": "ZCode quota collector尚未读取当前provider配置。",
        }

    def _observe_state(
        self,
        connection: sqlite3.Connection,
        session_id: str | None,
        session: dict[str, Any] | None,
        cutoff_ms: int,
    ) -> tuple[str, int | None, int | None]:
        activity_times = [self._timestamp_ms(session.get("time_updated")) if session else None]
        events: list[dict[str, Any]] = []
        if session_id:
            result = self._read_lifecycle_events(connection, [session_id], cutoff_ms)
            if result is None:
                return "unknown", max(activity_times), None
            events = result.get(session_id, [])
            activity_times.extend(event.get("time") for event in events)

            for row in connection.execute(
                "SELECT time_updated FROM todo WHERE session_id=? AND time_updated>=? "
                "ORDER BY time_updated DESC LIMIT 1",
                (session_id, cutoff_ms),
            ):
                activity_times.append(self._timestamp_ms(row["time_updated"]))

        activity_at = max((value for value in activity_times if value is not None), default=None)
        observed_state, state_at = self._choose_lifecycle_state(events)
        if observed_state:
            return observed_state, activity_at, state_at
        if session is None:
            return "unknown", activity_at, activity_at
        return "idle", activity_at, activity_at

    @staticmethod
    def _choose_lifecycle_state(events: list[dict[str, Any]]) -> tuple[str | None, int | None]:
        lifecycle = [
            event for event in events
            if event.get("state") in {"working", "waiting", "idle"}
        ]
        terminal_events = [
            event for event in lifecycle
            if event["state"] == "idle" and event["source"] in {"turn", "input"}
        ]
        latest_terminal = max(terminal_events, key=lambda event: event["time"] or 0, default=None)
        if latest_terminal:
            terminal_time = latest_terminal["time"] or 0
            terminal_turn_id = latest_terminal.get("turn_id")
            terminal_message_id = latest_terminal.get("user_message_id")
            lifecycle = [
                event for event in lifecycle
                if not (
                    event["state"] in {"working", "waiting"}
                    and (
                        (terminal_turn_id and event.get("turn_id") == terminal_turn_id)
                        or (terminal_message_id and event.get("user_message_id") == terminal_message_id)
                        or (event.get("time") is not None and event["time"] <= terminal_time)
                    )
                )
            ]

        if lifecycle:
            priority = {"working": 1, "waiting": 2, "idle": 3}
            latest = max(
                lifecycle,
                key=lambda event: (event["time"] or 0, priority.get(event["state"], 0)),
            )
            if latest["state"] in {"working", "waiting"}:
                related_activity = [
                    event["time"] for event in events
                    if event.get("time") is not None
                    and (
                        (latest.get("turn_id") and event.get("turn_id") == latest.get("turn_id"))
                        or (
                            latest.get("user_message_id")
                            and event.get("user_message_id") == latest.get("user_message_id")
                        )
                    )
                ]
                return latest["state"], max([latest.get("time")] + related_activity, key=lambda v: v or 0)
            return latest["state"], latest.get("time")
        return None, None

    @staticmethod
    def _semantic_state(status: str, approval_status: str = "") -> str | None:
        if status in _ACTIVE:
            return "working"
        if status in _WAITING or approval_status in _APPROVAL_WAITING:
            return "waiting"
        if status in _TERMINAL:
            return "idle"
        return None

    def _zcode_process_running(self) -> bool | None:
        now = time.monotonic()
        if now - self._process_checked_at < self.PROCESS_CACHE_SECONDS:
            return self._process_cached
        self._process_checked_at = now
        self._process_cached = self._detect_zcode_process()
        return self._process_cached

    @staticmethod
    def _detect_zcode_process() -> bool | None:
        if os.name != "nt":
            return None
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq ZCode.exe", "/FO", "CSV", "/NH"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=1.0,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0:
                return None
            for line in result.stdout.splitlines():
                try:
                    fields = next(csv.reader([line]))
                except (csv.Error, StopIteration):
                    continue
                if fields and fields[0].casefold() == "zcode.exe":
                    return True
            return False
        except (OSError, subprocess.SubprocessError):
            return None

    @classmethod
    def _clean_text(cls, value: object) -> str:
        if value is None:
            return ""
        text = " ".join(str(value).split())
        if len(text) > cls.MAX_TEXT_CHARS:
            return text[: cls.MAX_TEXT_CHARS - 1].rstrip() + "…"
        return text

    @staticmethod
    def _normalize_status(value: object) -> str:
        return "_".join(str(value or "").strip().lower().replace("-", " ").split())

    @staticmethod
    def _timestamp_ms(value: object) -> int | None:
        try:
            stamp = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        # Observed ZCode timestamps are epoch milliseconds. Accept epoch seconds
        # too so a compatible database version can still be read safely.
        return stamp * 1000 if abs(stamp) < 100_000_000_000 else stamp

    @staticmethod
    def _iso_timestamp(value: int | None) -> str | None:
        if value is None:
            return None
        try:
            return datetime.fromtimestamp(value / 1000).astimezone().isoformat(timespec="seconds")
        except (OSError, OverflowError, ValueError):
            return None

    @staticmethod
    def _age_seconds(timestamp_ms: int | None, now_seconds: float) -> float | None:
        if timestamp_ms is None:
            return None
        stamp_seconds = timestamp_ms / 1000
        if stamp_seconds > now_seconds + 300:
            return None
        return max(0.0, now_seconds - stamp_seconds)

    @staticmethod
    def _database_error_detail(error: sqlite3.OperationalError) -> str:
        message = str(error).casefold()
        if "locked" in message or "busy" in message:
            return "ZCode 数据库正忙，暂时无法读取；保留上一份状态待检查。"
        return "ZCode 数据库暂时无法读取，状态与指标未知。"
