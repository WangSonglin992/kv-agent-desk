"""Read-only Slurm node snapshots over a configured OpenSSH host alias."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time


DEFAULT_REFRESH_SECONDS = 60
MIN_REFRESH_SECONDS = 15
MAX_REFRESH_SECONDS = 3600
MAX_OUTPUT_BYTES = 256 * 1024
MAX_NODES = 4
SSH_TIMEOUT_SECONDS = 18
_HOST_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_NODE_FIELDS = (
    "name",
    "cpu_allocated",
    "cpu_total",
    "memory_allocated_mb",
    "memory_total_mb",
    "gpu_allocated",
    "gpu_total",
    "state",
)
_SLURM_FIELDS = {
    "NodeName",
    "CPUAlloc",
    "CPUTot",
    "AllocMem",
    "RealMemory",
    "State",
    "CfgTRES",
    "AllocTRES",
    "Gres",
}


class ClusterDataError(ValueError):
    """A bounded Slurm response could not be parsed safely."""


class ClusterQueryError(RuntimeError):
    """A query failed with a safe, user-facing detail string."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _source_id(host_alias: str) -> str:
    return hashlib.sha256(host_alias.encode("utf-8")).hexdigest()


def _parse_time(value: str | None) -> datetime | None:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
        if result and result.tzinfo is None:
            result = result.astimezone()
        return result
    except (ValueError, TypeError, AttributeError):
        return None


def _safe_nonnegative_int(raw: str) -> int:
    if not re.fullmatch(r"\d+", raw):
        raise ClusterDataError("invalid numeric field")
    return int(raw)


def _count_tres_gpus(raw: str) -> int | None:
    """Return a generic GPU count first, otherwise sum typed GPU counts."""
    generic: int | None = None
    typed: list[int] = []
    for entry in raw.split(","):
        key, separator, value = entry.strip().partition("=")
        if key != "gres/gpu" and not key.startswith("gres/gpu:"):
            continue
        if not separator:
            raise ClusterDataError("invalid GPU TRES field")
        count = _safe_nonnegative_int(value)
        if key == "gres/gpu":
            if generic is not None:
                raise ClusterDataError("duplicate GPU TRES field")
            generic = count
        else:
            typed.append(count)
    if generic is not None:
        return generic
    return sum(typed) if typed else None


def _count_gres_gpus(raw: str | None) -> int:
    if not raw or raw == "(null)":
        return 0
    generic: list[int] = []
    typed: list[int] = []
    for entry in raw.split(","):
        parts = entry.strip().split(":")
        if not parts or parts[0] != "gpu":
            continue
        if len(parts) == 2:
            count_text = parts[1].split("(", 1)[0]
            generic.append(_safe_nonnegative_int(count_text))
        elif len(parts) >= 3:
            count_text = parts[2].split("(", 1)[0]
            typed.append(_safe_nonnegative_int(count_text))
    if generic:
        return sum(generic)
    return sum(typed)


def _node_from_fields(fields: dict[str, str]) -> dict:
    required = (
        "NodeName",
        "CPUAlloc",
        "CPUTot",
        "AllocMem",
        "RealMemory",
        "State",
        "CfgTRES",
    )
    if any(field not in fields for field in required):
        raise ClusterDataError("required node field missing")
    if not fields["CfgTRES"]:
        raise ClusterDataError("required node field empty")

    name = fields["NodeName"]
    state = fields["State"]
    if (not name or len(name) > 128 or any(char.isspace() or ord(char) < 32 for char in name)
            or not state or len(state) > 128 or any(ord(char) < 32 for char in state)):
        raise ClusterDataError("invalid node identity field")

    cfg_gpu = _count_tres_gpus(fields["CfgTRES"])
    if cfg_gpu is None:
        cfg_gpu = _count_gres_gpus(fields.get("Gres"))
    alloc_gpu = _count_tres_gpus(fields.get("AllocTRES") or "")
    if alloc_gpu is None:
        alloc_gpu = 0

    return {
        "name": name,
        "cpu_allocated": _safe_nonnegative_int(fields["CPUAlloc"]),
        "cpu_total": _safe_nonnegative_int(fields["CPUTot"]),
        "memory_allocated_mb": _safe_nonnegative_int(fields["AllocMem"]),
        "memory_total_mb": _safe_nonnegative_int(fields["RealMemory"]),
        "gpu_allocated": alloc_gpu,
        "gpu_total": cfg_gpu,
        "state": state,
    }


def parse_scontrol_nodes(output: bytes | str) -> list[dict]:
    """Parse only the allowlisted fields, returning at most four node rows."""
    if isinstance(output, str):
        raw = output.encode("utf-8", errors="replace")
    elif isinstance(output, (bytes, bytearray)):
        raw = bytes(output)
    else:
        raise ClusterDataError("invalid Slurm output")
    if len(raw) > MAX_OUTPUT_BYTES:
        raise ClusterDataError("Slurm output exceeded limit")

    text = raw.decode("utf-8", errors="replace")
    nodes: list[dict] = []
    names: set[str] = set()
    for line in text.splitlines():
        fields: dict[str, str] = {}
        for token in line.split():
            key, separator, value = token.partition("=")
            if separator and key in _SLURM_FIELDS:
                if key in fields:
                    raise ClusterDataError("duplicate Slurm field")
                fields[key] = value
        if "NodeName" not in fields:
            continue
        node = _node_from_fields(fields)
        if node["name"] in names:
            raise ClusterDataError("duplicate node name")
        names.add(node["name"])
        nodes.append(node)
        if len(nodes) > MAX_NODES:
            raise ClusterDataError("too many node records")
    if not nodes:
        raise ClusterDataError("no node records")
    return nodes


def _ssh_executable() -> str | None:
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
        if system_root:
            return str(Path(system_root) / "System32" / "OpenSSH" / "ssh.exe")
        return None
    return shutil.which("ssh")


def _hidden_windows_options() -> dict:
    if os.name != "nt":
        return {}
    options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    startup_type = getattr(subprocess, "STARTUPINFO", None)
    if startup_type is not None:
        startup = startup_type()
        startup.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
        startup.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        options["startupinfo"] = startup
    return options


def _run_ssh(host_alias: str) -> bytes:
    executable = _ssh_executable()
    if not executable:
        raise ClusterQueryError("OpenSSH 未安装")
    command = [
        executable,
        "-T",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=8",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "NumberOfPasswordPrompts=0",
        "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        host_alias,
        "scontrol show nodes --oneliner",
    ]
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=SSH_TIMEOUT_SECONDS,
            check=False,
            **_hidden_windows_options(),
        )
    except subprocess.TimeoutExpired as exc:
        raise ClusterQueryError("SSH 查询超时") from exc
    except OSError as exc:
        raise ClusterQueryError("SSH 启动失败") from exc

    if result.returncode != 0:
        raise ClusterQueryError("Slurm 查询失败")
    output = result.stdout
    if isinstance(output, str):
        output = output.encode("utf-8", errors="replace")
    if not isinstance(output, (bytes, bytearray)):
        raise ClusterQueryError("Slurm 输出无效")
    if len(output) > MAX_OUTPUT_BYTES:
        raise ClusterQueryError("Slurm 输出超出上限")
    return bytes(output)


def _validated_cached_nodes(value: object, *, require_four: bool = False) -> list[dict] | None:
    if (not isinstance(value, list) or not 1 <= len(value) <= MAX_NODES
            or (require_four and len(value) != MAX_NODES)):
        return None
    normalized: list[dict] = []
    for row in value:
        if not isinstance(row, dict) or set(row) != set(_NODE_FIELDS):
            return None
        name = row.get("name")
        state = row.get("state")
        if (not isinstance(name, str) or not name or len(name) > 128
                or any(char.isspace() or ord(char) < 32 for char in name)
                or not isinstance(state, str) or not state or len(state) > 128
                or any(ord(char) < 32 for char in state)):
            return None
        clean = {"name": name, "state": state}
        for field in _NODE_FIELDS[1:-1]:
            number = row.get(field)
            if not isinstance(number, int) or isinstance(number, bool) or number < 0:
                return None
            clean[field] = number
        normalized.append(clean)
    return normalized


class ClusterMonitor:
    """Single-worker Slurm monitor with host-scoped last-good retention."""

    def __init__(self, path: Path | None, stop: threading.Event, demo: bool = False):
        self.path = Path(path) if path is not None else None
        self.stop = stop
        self.demo = demo
        self.lock = threading.RLock()
        self._wake = threading.Event()
        self.started = False
        self._thread: threading.Thread | None = None
        self._enabled = False
        self._host_alias = ""
        self._invalid_alias = False
        self._refresh_seconds = DEFAULT_REFRESH_SECONDS
        self._generation = 0
        self._known = False
        self._nodes: list[dict] = []
        self._updated_at: str | None = None
        self._last_attempt_at: str | None = None
        self._stale = True
        self._checking = False
        self._detail = "等待配置"
        self._last_manual_refresh: float | None = None
        self._last_poll_finished_monotonic: float | None = None
        self._wake_immediate = False
        self._disk_cache = self._read_cache()
        if self.demo:
            self._known = True
            self._nodes = [
                {"name": f"node-{index:02d}", "cpu_allocated": index * 3,
                 "cpu_total": 64, "memory_allocated_mb": index * 2048,
                 "memory_total_mb": 262144, "gpu_allocated": 1 if index in (1, 4) else 0,
                 "gpu_total": 4 if index in (1, 4) else 0, "state": "MIXED"}
                for index in range(1, MAX_NODES + 1)
            ]
            self._updated_at = _now_iso()
            self._stale = False
            self._detail = "演示数据"

    def _read_cache(self) -> dict | None:
        if self.path is None:
            return None
        try:
            metadata = self.path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_OUTPUT_BYTES:
                return None
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if (not isinstance(saved, dict) or not isinstance(saved.get("version"), int)
                or isinstance(saved.get("version"), bool) or saved.get("version") != 1):
            return None
        source_id = saved.get("source_id")
        updated_at = saved.get("updated_at")
        nodes = _validated_cached_nodes(saved.get("nodes"), require_four=True)
        if (not isinstance(source_id, str) or not re.fullmatch(r"[0-9a-f]{64}", source_id)
                or not isinstance(updated_at, str) or _parse_time(updated_at) is None or nodes is None):
            return None
        return {"source_id": source_id, "updated_at": updated_at, "nodes": nodes}

    def _save_cache(self, source_id: str, updated_at: str, nodes: list[dict]) -> None:
        cache = {"version": 1, "source_id": source_id,
                 "updated_at": updated_at, "nodes": deepcopy(nodes)}
        self._disk_cache = cache
        if self.path is None:
            return
        temporary_path: str | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not self._cache_target_is_regular_or_missing():
                return
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=self.path.parent,
                    prefix=f".{self.path.name}.", suffix=".tmp", delete=False) as temporary:
                temporary_path = temporary.name
                json.dump(cache, temporary, ensure_ascii=False, separators=(",", ":"))
                temporary.flush()
                os.fsync(temporary.fileno())
            if not self._cache_target_is_regular_or_missing():
                return
            os.replace(temporary_path, self.path)
            temporary_path = None
        except OSError:
            pass
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass

    def _cache_target_is_regular_or_missing(self) -> bool:
        if self.path is None:
            return False
        try:
            mode = self.path.lstat().st_mode
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return stat.S_ISREG(mode)

    def configure(self, enabled, ssh_host, refresh_seconds) -> None:
        raw_host = ssh_host if isinstance(ssh_host, str) else ""
        trimmed_host = raw_host.strip()
        valid_host = bool(_HOST_ALIAS.fullmatch(trimmed_host))
        host_alias = trimmed_host if valid_host else ""
        invalid_alias = bool(raw_host.strip()) and not valid_host
        enabled = bool(enabled)
        try:
            interval = int(refresh_seconds)
        except (TypeError, ValueError, OverflowError):
            interval = DEFAULT_REFRESH_SECONDS
        interval = max(MIN_REFRESH_SECONDS, min(MAX_REFRESH_SECONDS, interval))

        with self.lock:
            if self.demo:
                self._refresh_seconds = interval
                return
            previous_enabled = self._enabled
            changed = (enabled != self._enabled or host_alias != self._host_alias
                       or invalid_alias != self._invalid_alias or interval != self._refresh_seconds)
            if not changed:
                return
            host_changed = host_alias != self._host_alias or invalid_alias != self._invalid_alias
            self._enabled = enabled
            self._host_alias = host_alias
            self._invalid_alias = invalid_alias
            self._refresh_seconds = interval
            self._generation += 1
            if host_changed:
                self._known = False
                self._nodes = []
                self._updated_at = None
                self._last_attempt_at = None
                self._stale = True
                source_id = _source_id(host_alias) if host_alias else None
                cached = self._disk_cache if source_id and self._disk_cache else None
                if cached and cached.get("source_id") == source_id:
                    self._known = True
                    self._nodes = deepcopy(cached["nodes"])
                    self._updated_at = cached["updated_at"]
                    self._stale = True
                    self._detail = "已载入上次数据，等待更新"
                else:
                    self._detail = self._configuration_detail()
            elif not enabled:
                self._stale = self._known or self._stale
                self._detail = "监控已停用"
            elif invalid_alias:
                self._detail = "主机别名无效"
            elif not host_alias:
                self._detail = "未配置 SSH 主机别名"
            elif not self._known:
                self._detail = "等待采集"
            if host_changed or enabled != previous_enabled:
                self._wake_immediate = True
            self._wake.set()

    def _configuration_detail(self) -> str:
        if self._invalid_alias:
            return "主机别名无效"
        if not self._enabled:
            return "监控已停用"
        if not self._host_alias:
            return "未配置 SSH 主机别名"
        return "等待采集"

    def start(self) -> None:
        with self.lock:
            if self.started:
                return
            self.started = True
            if self.demo or self.stop.is_set():
                return
            self._thread = threading.Thread(
                target=self._worker, name="cluster-monitor", daemon=True)
            self._thread.start()

    def snapshot(self) -> dict:
        with self.lock:
            known = self._known
            updated_at = self._updated_at
            refresh_seconds = self._refresh_seconds
            stale = self._stale
            if known:
                seen = _parse_time(updated_at)
                if seen is None:
                    stale = True
                else:
                    age = (datetime.now().astimezone() - seen).total_seconds()
                    stale |= age > max(60, refresh_seconds * 2)
            return {
                "known": known,
                "nodes": deepcopy(self._nodes),
                "updated_at": updated_at,
                "last_attempt_at": self._last_attempt_at,
                "stale": bool(stale),
                "checking": self._checking,
                "detail": self._detail,
                "refresh_seconds": refresh_seconds,
            }

    def request_refresh(self) -> bool:
        now = time.monotonic()
        with self.lock:
            if (self.demo or self.stop.is_set() or not self._enabled
                    or not self._host_alias or self._invalid_alias):
                return False
            if self._last_manual_refresh is not None and now - self._last_manual_refresh < MIN_REFRESH_SECONDS:
                return False
            self._last_manual_refresh = now
            self._wake.set()
            return True

    def _query(self, host_alias: str) -> list[dict]:
        return parse_scontrol_nodes(_run_ssh(host_alias))

    def _refresh_once(self, host_alias: str, generation: int) -> None:
        with self.lock:
            if (generation != self._generation or not self._enabled
                    or self._host_alias != host_alias or self._invalid_alias):
                return
            self._checking = True
            self._last_attempt_at = _now_iso()
            self._detail = "正在读取集群状态"
        try:
            nodes = self._query(host_alias)
            nodes = _validated_cached_nodes(nodes, require_four=True)
            if nodes is None:
                raise ClusterDataError("invalid parsed node data")
            updated_at = _now_iso()
            failure_detail = ""
        except ClusterQueryError as exc:
            nodes = None
            updated_at = None
            failure_detail = exc.detail
        except (ClusterDataError, UnicodeError, ValueError, TypeError):
            nodes = None
            updated_at = None
            failure_detail = "节点数据无效"
        except Exception:
            nodes = None
            updated_at = None
            failure_detail = "集群状态读取失败"

        with self.lock:
            self._last_poll_finished_monotonic = time.monotonic()
            self._checking = False
            if generation != self._generation or self._host_alias != host_alias:
                return
            if nodes is None:
                self._stale = True
                self._detail = ("更新失败，保留上次数据" if self._known else failure_detail)
                return
            self._known = True
            self._nodes = nodes
            self._updated_at = updated_at
            self._stale = False
            self._detail = ""
            self._save_cache(_source_id(host_alias), updated_at, nodes)

    def _worker(self) -> None:
        next_poll: float | None = None
        while not self.stop.is_set():
            with self.lock:
                signaled = self._wake.is_set()
                immediate = self._wake_immediate
                self._wake.clear()
                self._wake_immediate = False
                enabled = self._enabled
                host_alias = self._host_alias
                invalid_alias = self._invalid_alias
                generation = self._generation
                interval = self._refresh_seconds
            active = enabled and bool(host_alias) and not invalid_alias
            if not active:
                next_poll = None
                self._wake.wait(0.5)
                continue

            now = time.monotonic()
            if signaled:
                next_poll = now
            if next_poll is None:
                next_poll = now
            if now >= next_poll:
                if signaled and not immediate:
                    with self.lock:
                        last_finished = self._last_poll_finished_monotonic
                    earliest = (last_finished + MIN_REFRESH_SECONDS
                                if last_finished is not None else now)
                    if now < earliest:
                        next_poll = earliest
                        self._wake.wait(min(next_poll - now, 1.0))
                        continue
                self._refresh_once(host_alias, generation)
                with self.lock:
                    interval = self._refresh_seconds
                next_poll = time.monotonic() + interval
                continue

            self._wake.wait(min(next_poll - now, 1.0))

