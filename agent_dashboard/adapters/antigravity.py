"""Read Antigravity activity and quotas from its running local language server."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import http.client
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Any


_MIN_REFRESH_SECONDS = 60.0
_ACTIVITY_REFRESH_SECONDS = 2.0
_RUNTIME_REFRESH_SECONDS = 10.0
_USAGE_REFRESH_SECONDS = 15.0
_MAX_RESPONSE_BYTES = 1024 * 1024
_RPC_PATH = "/exa.language_server_pb.LanguageServerService/GetUserStatus"
_SUMMARY_RPC_PATH = "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"
_ACTIVITY_RPC_PATH = "/exa.language_server_pb.LanguageServerService/GetAllCascadeTrajectories"
_USAGE_RPC_PATH = "/exa.language_server_pb.LanguageServerService/GetCascadeTrajectoryGeneratorMetadata"
_REQUEST_BODY = json.dumps(
    {
        "metadata": {
            "ideName": "antigravity",
            "extensionName": "antigravity",
            "ideVersion": "unknown",
            "locale": "en",
        }
    },
    separators=(",", ":"),
).encode("utf-8")
_SUMMARY_REQUEST_BODY = b'{"forceRefresh":true}'
_ACTIVITY_REQUEST_BODY = b'{"excludeSubtrajectories":true}'
_RUN_STATUSES = {1: "IDLE", 2: "RUNNING", 3: "CANCELING", 4: "BUSY"}

_DISCOVERY_SCRIPT = r'''
$root = [IO.Path]::GetFullPath($env:AGENT_ANTIGRAVITY_ROOT)
$appExe = [IO.Path]::GetFullPath((Join-Path $root 'Antigravity.exe'))
$serverExe = [IO.Path]::GetFullPath((Join-Path $root 'resources\bin\language_server.exe'))
$items = Get-CimInstance -ClassName Win32_Process -Filter "Name='Antigravity.exe' OR Name='language_server.exe'" -Property Name,ProcessId,ExecutablePath,CommandLine -ErrorAction Stop
$appRunning = [bool](@($items | Where-Object { $_.Name -eq 'Antigravity.exe' -and $_.ExecutablePath -eq $appExe }).Count)
$servers = @()
foreach ($item in @($items | Where-Object { $_.Name -eq 'language_server.exe' -and $_.ExecutablePath -eq $serverExe })) {
    $match = [regex]::Match($item.CommandLine, '--csrf_token(?:=|\s+)(?:"([^"]+)"|(\S+))')
    $csrf = if ($match.Groups[1].Success) { $match.Groups[1].Value } else { $match.Groups[2].Value }
    $ports = @(Get-NetTCPConnection -State Listen -OwningProcess $item.ProcessId -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -eq '127.0.0.1' } |
        Select-Object -ExpandProperty LocalPort -Unique)
    $servers += [ordered]@{ csrf = $csrf; ports = $ports }
}
[ordered]@{ app_running = $appRunning; servers = @($servers) } | ConvertTo-Json -Compress -Depth 5
'''


class AntigravityAdapter:
    """Read explicit Gemini shared quota windows without inferring their periods.

    ``root`` is the exact Antigravity installation directory. Credentials are
    read from the running official language-server process and sent only to
    loopback listeners owned by that exact executable.
    """

    def __init__(self, root: Path | str | None = None):
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        self.root = Path(root).expanduser() if root is not None else local_app_data / "Programs" / "antigravity"
        self._lock = threading.Lock()
        self._last_attempt: float | None = None
        self._last_quota_attempt: float | None = None
        self._last_runtime_attempt: float | None = None
        self._runtime: dict[str, Any] | None = None
        self._quota_result: dict[str, Any] | None = None
        self._last_usage_attempt: float | None = None
        self._usage_result: dict[str, Any] | None = None
        self._usage_thread: threading.Thread | None = None
        self._last_result: dict[str, Any] | None = None

    def collect(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            if (
                self._last_attempt is not None
                and now - self._last_attempt < _ACTIVITY_REFRESH_SECONDS
                and self._last_result is not None
            ):
                return deepcopy(self._last_result)
            self._last_attempt = now

            installed = (self.root / "Antigravity.exe").is_file()
            if not installed:
                result = self._snapshot(
                    available=False,
                    state="offline",
                    observed_state="offline",
                    state_label="未检测到 Antigravity",
                    stale=False,
                    detail="未找到此安装目录中的 Antigravity 程序。",
                )
            else:
                try:
                    if (self._runtime is None or self._last_runtime_attempt is None
                            or now - self._last_runtime_attempt >= _RUNTIME_REFRESH_SECONDS):
                        self._runtime = self._discover_runtime()
                        self._last_runtime_attempt = now
                    runtime = self._runtime
                    if not runtime.get("app_running"):
                        result = self._offline_snapshot()
                    else:
                        servers = runtime.get("servers", [])
                        if (self._quota_result is None or self._last_quota_attempt is None
                                or now - self._last_quota_attempt >= _MIN_REFRESH_SECONDS):
                            self._last_quota_attempt = now
                            try:
                                try:
                                    summary = self._fetch_quota_summary(servers)
                                    quota = self._parse_quota_summary(summary)
                                except Exception:
                                    # Older builds only expose per-model quotas.
                                    quota = self._parse_user_status(self._fetch_user_status(servers))
                            except Exception:
                                quota = self._failure_snapshot()
                            quota["subscription"].setdefault("updated_at", quota.get("updated_at"))
                            self._quota_result = quota
                        result = deepcopy(self._quota_result)
                        try:
                            result.update(self._fetch_activity(servers))
                            result["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                        except Exception:
                            # Let LastGoodStore retain the last credible count
                            # and mark it for checking, rather than invent zero.
                            result.update(state="unknown", observed_state="unknown", state_label="数据待更新",
                                          active_tasks=None, active_tasks_known=False, stale=True)
                            result["updated_at"] = (self._last_result or {}).get("updated_at")
                            self._runtime = None
                        if "GetAllCascadeTrajectories" not in result["source"]:
                            result["source"] += "; GetAllCascadeTrajectories localhost RPC"
                        self._start_usage_refresh(servers, now)
                        result["tokens"] = deepcopy(self._usage_result or {
                            "known": False, "total": None, "scope": "cumulative"})
                        if self._usage_result is not None and not self._usage_result.get("known"):
                            result["stale"] = True
                        if "GetCascadeTrajectoryGeneratorMetadata" not in result["source"]:
                            result["source"] += "; GetCascadeTrajectoryGeneratorMetadata localhost RPC"
                except Exception:
                    result = self._failure_snapshot()
                    result.update(state="unknown", observed_state="unknown", active_tasks=None, active_tasks_known=False)
                    self._runtime = None

            self._last_result = result
            return deepcopy(result)

    def _start_usage_refresh(self, servers: object, now: float) -> None:
        if self._usage_thread is not None and self._usage_thread.is_alive():
            return
        if self._last_usage_attempt is not None and now - self._last_usage_attempt < _USAGE_REFRESH_SECONDS:
            return
        self._last_usage_attempt = now
        candidates = deepcopy(servers)

        def refresh() -> None:
            try:
                result = self._fetch_tokens(candidates)
            except Exception:
                result = {"known": False, "total": None, "scope": "cumulative"}
            with self._lock:
                self._usage_result = result

        self._usage_thread = threading.Thread(target=refresh, daemon=True, name="antigravity-usage")
        self._usage_thread.start()

    @staticmethod
    def _token_count(value: object) -> int:
        if type(value) is int and 0 <= value <= 2**64 - 1:
            return value
        if isinstance(value, str) and re.fullmatch(r"[0-9]{1,20}", value):
            count = int(value)
            if count <= 2**64 - 1:
                return count
        raise ValueError("invalid token count")

    @classmethod
    def _fetch_tokens(cls, servers: object) -> dict[str, Any]:
        if not isinstance(servers, list) or not servers:
            raise RuntimeError("usage server unavailable")
        # Include child trajectories for consumption, while activity counts
        # only parent tasks. IDs are temporary deduplication keys, never saved.
        owners = {}
        for server in servers:
            payload = cls._fetch_local_rpc([server], _ACTIVITY_RPC_PATH, b'{"excludeSubtrajectories":false}')
            if not payload:
                summaries = {}
            else:
                summaries = payload.get("trajectorySummaries")
            if not isinstance(summaries, dict):
                raise ValueError("usage trajectory map missing")
            for identity in summaries:
                if not isinstance(identity, str) or not identity:
                    raise ValueError("invalid usage trajectory")
                owners.setdefault(identity, server)
        if not owners:
            raise ValueError("usage trajectories unavailable")
        if len(owners) > 128:
            raise ValueError("usage trajectory limit exceeded")

        records = {}
        requests = 0
        for identity, server in owners.items():
            offset = 0
            while True:
                requests += 1
                if requests > 256:
                    raise ValueError("usage request limit exceeded")
                body = json.dumps({"cascadeId": identity, "generatorMetadataOffset": offset,
                                   "includeMessages": False}, separators=(",", ":")).encode("utf-8")
                payload = cls._fetch_local_rpc([server], _USAGE_RPC_PATH, body)
                page = payload.get("generatorMetadata", []) if not payload or "generatorMetadata" in payload else None
                if not isinstance(page, list):
                    raise ValueError("usage metadata missing")
                if not page:
                    break
                if offset + len(page) > 8192:
                    raise ValueError("usage metadata limit exceeded")
                for index, entry in enumerate(page):
                    if not isinstance(entry, dict):
                        raise ValueError("invalid generator metadata")
                    model = entry.get("chatModel")
                    if not isinstance(model, dict):
                        continue
                    usage = model.get("usage")
                    if not isinstance(usage, dict) or not any(
                            key in usage for key in ("inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens")):
                        continue
                    counts = tuple(cls._token_count(usage.get(key, 0)) for key in
                                   ("inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens", "thinkingOutputTokens"))
                    message = usage.get("messageId")
                    key = ("message", message) if isinstance(message, str) and message else ("generator", identity, offset + index)
                    if key in records and records[key] != counts:
                        raise ValueError("conflicting usage metadata")
                    records[key] = counts
                offset += len(page)
        if owners and not records:
            raise ValueError("usage counts unavailable")
        totals = [sum(counts[index] for counts in records.values()) for index in range(5)]
        return {"known": True, "scope": "cumulative", "input": totals[0], "output": totals[1],
                "cached": totals[2], "cache_write": totals[3], "reasoning": totals[4],
                "total": totals[0] + totals[1]}

    @classmethod
    def _trajectory_statuses(cls, payload: object) -> dict[str, str]:
        if not isinstance(payload, dict):
            raise ValueError("invalid activity response")
        # Empty proto3 maps can be omitted from the JSON response.
        rows = payload.get("trajectorySummaries", {}) if not payload or "trajectorySummaries" in payload else None
        if not isinstance(rows, dict):
            raise ValueError("activity summary map missing")
        statuses = {}
        for identity, row in rows.items():
            if not isinstance(identity, str) or not identity or not isinstance(row, dict):
                raise ValueError("invalid activity summary")
            raw = row.get("status")
            if isinstance(raw, str) and raw.startswith("CASCADE_RUN_STATUS_"):
                status = raw.removeprefix("CASCADE_RUN_STATUS_")
            elif type(raw) is int:
                status = _RUN_STATUSES.get(raw)
            else:
                status = None
            if status not in _RUN_STATUSES.values():
                raise ValueError("unknown activity status")
            # IDs exist only during this request, for cross-window deduplication.
            # Titles, workspaces and message/step bodies are never copied.
            statuses[identity] = status
        return statuses

    @classmethod
    def _fetch_activity(cls, servers: object) -> dict[str, Any]:
        if not isinstance(servers, list) or not servers:
            raise RuntimeError("activity server unavailable")
        statuses: dict[str, str] = {}
        for server in servers:
            rows = cls._trajectory_statuses(cls._fetch_local_rpc([server], _ACTIVITY_RPC_PATH, _ACTIVITY_REQUEST_BODY))
            for identity, status in rows.items():
                if identity in statuses and statuses[identity] != status:
                    raise ValueError("conflicting activity summaries")
                statuses[identity] = status
        count = sum(status in {"RUNNING", "BUSY", "CANCELING"} for status in statuses.values())
        state = "working" if count else "idle"
        return {"state": state, "observed_state": state, "state_label": "工作中" if count else "空闲",
                "active_tasks": count, "active_tasks_known": True}

    def _discover_runtime(self) -> dict[str, Any]:
        if os.name != "nt":
            return {"app_running": False, "servers": []}
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        env = {
            "SystemRoot": str(system_root),
            "PATH": os.environ.get("PATH", ""),
            "AGENT_ANTIGRAVITY_ROOT": str(self.root.resolve()),
        }
        try:
            completed = subprocess.run(
                [str(powershell), "-NoProfile", "-NonInteractive", "-Command", _DISCOVERY_SCRIPT],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=5,
                check=False,
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode != 0:
                raise RuntimeError("process discovery failed")
            result = json.loads(completed.stdout)
            if not isinstance(result, dict):
                raise ValueError("invalid process data")
            return result
        except Exception:
            raise RuntimeError("process discovery failed") from None

    @staticmethod
    def _fetch_local_rpc(servers: object, path: str, body: bytes) -> dict[str, Any]:
        if not isinstance(servers, list):
            raise ValueError("invalid language server data")
        for server in servers:
            if not isinstance(server, dict):
                continue
            csrf = server.get("csrf")
            ports = server.get("ports")
            if not isinstance(csrf, str) or not csrf or not isinstance(ports, list):
                continue
            for raw_port in ports:
                if not isinstance(raw_port, int) or isinstance(raw_port, bool) or not 1 <= raw_port <= 65535:
                    continue
                connection = http.client.HTTPConnection("127.0.0.1", raw_port, timeout=2.0)
                try:
                    connection.request(
                        "POST",
                        path,
                        body=body,
                        headers={
                            "Content-Type": "application/json",
                            "Connect-Protocol-Version": "1",
                            "X-Codeium-Csrf-Token": csrf,
                        },
                    )
                    response = connection.getresponse()
                    if response.status != 200:
                        continue
                    response_body = response.read(_MAX_RESPONSE_BYTES + 1)
                    if len(response_body) > _MAX_RESPONSE_BYTES:
                        continue
                    payload = json.loads(response_body.decode("utf-8"))
                    if isinstance(payload, dict):
                        return payload
                except Exception:
                    continue
                finally:
                    connection.close()
        raise RuntimeError("Antigravity quota query failed")

    @staticmethod
    def _fetch_user_status(servers: object) -> dict[str, Any]:
        return AntigravityAdapter._fetch_local_rpc(servers, _RPC_PATH, _REQUEST_BODY)

    @staticmethod
    def _fetch_quota_summary(servers: object) -> dict[str, Any]:
        return AntigravityAdapter._fetch_local_rpc(servers, _SUMMARY_RPC_PATH, _SUMMARY_REQUEST_BODY)

    @staticmethod
    def _quota_groups(payload: object) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            raise ValueError("invalid quota summary")
        candidates: list[object] = [payload]
        for key in ("response", "data"):
            child = payload.get(key)
            if isinstance(child, dict):
                candidates.append(child)
        command = payload.get("command")
        command_data = command.get("data") if isinstance(command, dict) else None
        if isinstance(command_data, dict):
            candidates.append(command_data)
        for candidate in candidates:
            groups = candidate.get("groups") if isinstance(candidate, dict) else None
            if isinstance(groups, list):
                return [group for group in groups if isinstance(group, dict)]
        raise ValueError("quota summary groups missing")

    @staticmethod
    def _group_label(group: dict[str, Any]) -> str:
        parts = []
        for key in ("displayName", "display_name", "groupName", "group_name", "groupId", "group_id", "name", "id"):
            value = group.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        return " ".join(parts).casefold()

    @classmethod
    def _is_gemini_group(cls, group: dict[str, Any]) -> bool:
        label = cls._group_label(group)
        if any(token in label for token in ("claude", "gpt", "third party", "third-party", "third_party", "3p")):
            return False
        if "gemini" in label:
            return True
        buckets = group.get("buckets")
        if not isinstance(buckets, list):
            return False
        identifiers = []
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            for key in ("bucketId", "bucket_id"):
                value = bucket.get(key)
                if isinstance(value, str):
                    identifiers.append(value.casefold())
        return bool(identifiers) and all(
            "gemini" in value and not any(token in value for token in ("claude", "gpt", "3p"))
            for value in identifiers
        )

    @staticmethod
    def _bucket_window(bucket: dict[str, Any]) -> str | None:
        raw_window = bucket.get("window")
        if isinstance(raw_window, str) and raw_window.strip():
            candidates = [raw_window.casefold().strip()]
        else:
            candidates = []
            for key in ("bucketId", "bucket_id", "displayName", "display_name"):
                value = bucket.get(key)
                if isinstance(value, str) and value.strip():
                    candidates.append(value.casefold().strip())
        for value in candidates:
            compact = re.sub(r"[^a-z0-9]+", "", value)
            if compact in {
                "5h", "5hr", "5hour", "5hours", "fivehour", "fivehours",
                "fivehourlimit", "rolling5h", "gemini5h",
            }:
                return "five_hour"
            if compact in {
                "week", "weekly", "7d", "7day", "7days", "168h", "168hours",
                "weeklylimit", "geminiweekly",
            }:
                return "weekly"
        return None

    @classmethod
    def _parse_summary_bucket(cls, bucket: dict[str, Any], window: str) -> dict[str, Any]:
        remaining: object = bucket.get("remainingFraction", bucket.get("remaining_fraction"))
        nested_remaining = bucket.get("remaining")
        if remaining is None and isinstance(nested_remaining, dict):
            remaining = nested_remaining.get("remainingFraction", nested_remaining.get("remaining_fraction"))
        percent = None if bucket.get("disabled") is True else cls._remaining_percent(remaining)
        reset_raw = bucket.get("resetTime", bucket.get("reset_time"))
        reset = cls._parse_reset_time(reset_raw)
        period_label = "5小时" if window == "five_hour" else "周额度"
        return {
            "label": period_label,
            "model": "Gemini",
            "window": window,
            "remaining_percent": percent,
            "reset_at": reset,
            "known": percent is not None,
        }

    @classmethod
    def _parse_quota_summary(cls, payload: object) -> dict[str, Any]:
        groups = cls._quota_groups(payload)
        gemini_groups = [group for group in groups if cls._is_gemini_group(group)]
        exact_groups = [group for group in gemini_groups
                        if "gemini models" in cls._group_label(group)]
        if len(exact_groups) == 1:
            group = exact_groups[0]
        elif len(gemini_groups) == 1:
            group = gemini_groups[0]
        else:
            raise ValueError("Gemini quota group missing or ambiguous")

        buckets = group.get("buckets")
        if not isinstance(buckets, list):
            raise ValueError("Gemini quota buckets missing")
        candidates: dict[str, list[dict[str, Any]]] = {"five_hour": [], "weekly": []}
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            window = cls._bucket_window(bucket)
            if window is not None:
                candidates[window].append(cls._parse_summary_bucket(bucket, window))

        unknown = {"known": False, "remaining_percent": None, "reset_at": None}
        windows = {
            key: values[0] if len(values) == 1 else dict(unknown)
            for key, values in candidates.items()
        }
        known_keys = [key for key, value in windows.items() if value.get("known")]
        labels = {"five_hour": "5小时", "weekly": "周额度"}
        if len(known_keys) == 2:
            detail = "已读取 Antigravity Gemini 共享额度的5小时与周窗口。"
        elif known_keys:
            missing = "周额度" if known_keys[0] == "five_hour" else "5小时"
            detail = f"已读取 Antigravity Gemini 共享额度的{labels[known_keys[0]]}窗口；{missing}窗口本次未返回。"
        else:
            detail = "已连接 Antigravity，但当前 Gemini 共享额度没有可用窗口数据。"
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        return cls._snapshot(
            available=True,
            state="unknown",
            observed_state="unknown",
            state_label="已连接",
            stale=not known_keys,
            detail=detail,
            updated_at=timestamp,
            five_hour=windows["five_hour"],
            weekly=windows["weekly"],
            quota_group="gemini",
            quota_windows=[bucket for values in candidates.values() for bucket in values],
            source="Antigravity language_server RetrieveUserQuotaSummary localhost RPC",
        )

    @staticmethod
    def _is_gemini_model(label: object, model: object) -> bool:
        identity = " ".join(value.casefold() for value in (label, model) if isinstance(value, str))
        return "gemini" in identity and not any(token in identity for token in ("claude", "gpt", "3p"))

    @classmethod
    def _parse_user_status(cls, payload: object) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("invalid response")
        user_status = payload.get("userStatus")
        model_data = user_status.get("cascadeModelConfigData") if isinstance(user_status, dict) else None
        configs = model_data.get("clientModelConfigs") if isinstance(model_data, dict) else None
        if not isinstance(configs, list):
            raise ValueError("quota fields missing")

        windows: list[dict[str, Any]] = []
        for config in configs:
            if not isinstance(config, dict):
                continue
            quota = config.get("quotaInfo")
            if not isinstance(quota, dict):
                quota = {}
            reset_at = cls._parse_reset_time(quota.get("resetTime"))
            remaining = cls._remaining_percent(quota.get("remainingFraction"))
            if "remainingFraction" not in quota and reset_at is not None:
                # The matching QuotaInfo proto declares remaining_fraction as
                # a non-optional proto3 float, whose zero value may be omitted
                # from JSON. A valid reset_time identifies a real quota window.
                remaining = 0.0
            model_value = config.get("modelOrAlias")
            if isinstance(model_value, dict):
                model = model_value.get("model") or model_value.get("alias")
            else:
                model = model_value
            label = config.get("label")
            if not isinstance(model, str) or not model:
                model = label if isinstance(label, str) else ""
            if not isinstance(label, str) or not label:
                label = model or "未知模型"
            if not cls._is_gemini_model(label, model):
                continue
            windows.append(
                {
                    "label": label,
                    "remaining_percent": remaining,
                    "reset_at": reset_at,
                    "known": remaining is not None,
                    "model": model,
                }
            )

        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        return cls._snapshot(
            available=True,
            state="unknown",
            observed_state="unknown",
            state_label="已连接",
            stale=True,
            detail="旧接口仅返回 Gemini 按模型额度，未提供可验证的共享5小时/周窗口；额度周期保持待更新。",
            updated_at=timestamp,
            quota_group="gemini",
            quota_windows=windows,
            source="Antigravity language_server GetUserStatus legacy per-model RPC",
        )

    @staticmethod
    def _remaining_percent(value: object) -> float | None:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        fraction = float(value)
        if not math.isfinite(fraction):
            return None
        return round(max(0.0, min(1.0, fraction)) * 100.0, 2)

    @staticmethod
    def _parse_reset_time(value: object) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _offline_snapshot(self) -> dict[str, Any]:
        previous = self._last_result or {}
        previous_subscription = previous.get("subscription", {})
        result = self._snapshot(
            available=True,
            state="offline",
            observed_state="offline",
            state_label="未运行",
            stale=True,
            detail="Antigravity 已安装但当前未运行；保留最近一次额度快照。",
            updated_at=previous.get("updated_at"),
            five_hour=previous_subscription.get("five_hour"),
            weekly=previous_subscription.get("weekly"),
            quota_group=previous.get("quota_group", "gemini"),
            quota_windows=previous_subscription.get("quota_windows", []),
            source=previous.get("source", "Antigravity language_server local RPC"),
        )
        return result

    def _failure_snapshot(self) -> dict[str, Any]:
        if self._last_result is not None:
            result = deepcopy(self._last_result)
            result["stale"] = True
            result["detail"] = "Antigravity 额度刷新失败；已保留上次成功结果。"
            return result
        return self._snapshot(
            available=True,
            state="unknown",
            observed_state="unknown",
            state_label="数据待更新",
            stale=True,
            detail="Antigravity 额度暂不可用；没有可保留的历史结果。",
        )

    @staticmethod
    def _snapshot(
        *,
        available: bool,
        state: str,
        observed_state: str,
        state_label: str,
        stale: bool,
        detail: str,
        updated_at: str | None = None,
        five_hour: dict[str, Any] | None = None,
        weekly: dict[str, Any] | None = None,
        quota_group: str = "gemini",
        quota_windows: list[dict[str, Any]] | None = None,
        source: str = "Antigravity language_server GetUserStatus localhost RPC",
    ) -> dict[str, Any]:
        unknown_window = {"remaining_percent": None, "reset_at": None, "known": False}
        five_hour_window = deepcopy(five_hour) if isinstance(five_hour, dict) else deepcopy(unknown_window)
        weekly_window = deepcopy(weekly) if isinstance(weekly, dict) else deepcopy(unknown_window)
        return {
            "provider": "antigravity",
            "name": "Antigravity",
            "available": available,
            "state": state,
            "state_label": state_label,
            "observed_state": observed_state,
            "stale": stale,
            "quota_group": quota_group,
            "project": "",
            "task": "",
            "updated_at": updated_at,
            "active_tasks": None,
            "active_tasks_known": False,
            "progress": {"completed": 0, "total": 0, "steps": [], "known": False},
            "tokens": {
                "input": None,
                "output": None,
                "cached": None,
                "reasoning": None,
                "total": None,
                "scope": "unknown",
                "known": False,
            },
            "subscription": {
                "five_hour": five_hour_window,
                "weekly": weekly_window,
                "quota_group": quota_group,
                "quota_windows": deepcopy(quota_windows or []),
            },
            "source": source,
            "detail": detail,
        }
