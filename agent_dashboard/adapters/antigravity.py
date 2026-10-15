"""Read Antigravity quota data from its already-running local language server."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import http.client
import json
import math
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Any


_MIN_REFRESH_SECONDS = 60.0
_MAX_RESPONSE_BYTES = 1024 * 1024
_RPC_PATH = "/exa.language_server_pb.LanguageServerService/GetUserStatus"
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
    """Read per-model quota snapshots without inferring quota windows or plans.

    ``root`` is the exact Antigravity installation directory. Credentials are
    read from the running official language-server process and sent only to
    loopback listeners owned by that exact executable.
    """

    def __init__(self, root: Path | str | None = None):
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        self.root = Path(root).expanduser() if root is not None else local_app_data / "Programs" / "antigravity"
        self._lock = threading.Lock()
        self._last_attempt: float | None = None
        self._last_result: dict[str, Any] | None = None

    def collect(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            if (
                self._last_attempt is not None
                and now - self._last_attempt < _MIN_REFRESH_SECONDS
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
                    runtime = self._discover_runtime()
                    if not runtime.get("app_running"):
                        result = self._offline_snapshot()
                    else:
                        payload = self._fetch_user_status(runtime.get("servers", []))
                        result = self._parse_user_status(payload)
                except Exception:
                    result = self._failure_snapshot()

            self._last_result = result
            return deepcopy(result)

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
                timeout=3,
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
    def _fetch_user_status(servers: object) -> dict[str, Any]:
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
                        _RPC_PATH,
                        body=_REQUEST_BODY,
                        headers={
                            "Content-Type": "application/json",
                            "Connect-Protocol-Version": "1",
                            "X-Codeium-Csrf-Token": csrf,
                        },
                    )
                    response = connection.getresponse()
                    if response.status != 200:
                        continue
                    body = response.read(_MAX_RESPONSE_BYTES + 1)
                    if len(body) > _MAX_RESPONSE_BYTES:
                        continue
                    payload = json.loads(body.decode("utf-8"))
                    if isinstance(payload, dict):
                        return payload
                except Exception:
                    continue
                finally:
                    connection.close()
        raise RuntimeError("Antigravity quota query failed")

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
            stale=False,
            detail="已读取 Antigravity 按模型额度；工作任务状态暂不可用。",
            updated_at=timestamp,
            quota_windows=windows,
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
        result = self._snapshot(
            available=True,
            state="offline",
            observed_state="offline",
            state_label="未运行",
            stale=self._last_result is None or bool(self._last_result.get("subscription", {}).get("quota_windows")),
            detail="Antigravity 已安装但当前未运行；保留最近一次额度快照。",
            quota_windows=(self._last_result or {}).get("subscription", {}).get("quota_windows", []),
            updated_at=(self._last_result or {}).get("updated_at"),
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
        quota_windows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        unknown_window = {"remaining_percent": None, "reset_at": None, "known": False}
        return {
            "provider": "antigravity",
            "name": "Antigravity",
            "available": available,
            "state": state,
            "state_label": state_label,
            "observed_state": observed_state,
            "stale": stale,
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
                "five_hour": deepcopy(unknown_window),
                "weekly": deepcopy(unknown_window),
                "quota_windows": deepcopy(quota_windows or []),
            },
            "source": "Antigravity language_server GetUserStatus localhost RPC",
            "detail": detail,
        }
