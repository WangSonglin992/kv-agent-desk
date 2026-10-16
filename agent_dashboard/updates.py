"""Bounded, integrity-checked updater for installed Windows builds."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


OWNER = "WangSonglin992"
REPOSITORY = "kv-agent-desk"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{OWNER}/{REPOSITORY}/releases/latest"
LATEST_MANIFEST_URL = f"https://github.com/{OWNER}/{REPOSITORY}/releases/latest/download/KV-Agent-Desk.update.json"
EXECUTABLE_NAME = "KV-Agent-Desk.exe"
CHECKSUM_NAME = f"{EXECUTABLE_NAME}.sha256"

MAX_RELEASE_JSON = 1024 * 1024
MAX_EXECUTABLE_BYTES = 200 * 1024 * 1024
MAX_CHECKSUM_BYTES = 1024
NETWORK_TIMEOUT_SECONDS = 10
DOWNLOAD_CHUNK_BYTES = 64 * 1024
INITIAL_DELAY_SECONDS = 15
CHECK_INTERVAL_SECONDS = 24 * 60 * 60
FAILURE_BACKOFF_SECONDS = (15 * 60, 60 * 60, 6 * 60 * 60, 24 * 60 * 60)

_VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_TAG_RE = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_MANIFEST_RELEASE_PATH_RE = re.compile(
    rf"^/{re.escape(OWNER)}/{re.escape(REPOSITORY)}/releases/download/v"
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)/KV-Agent-Desk\.update\.json$"
)
_CHECKSUM_RE = re.compile(
    rb"^([0-9a-fA-F]{64})(?:[ \t]+\*?KV-Agent-Desk\.exe)?$"
)
_ALLOWED_HOSTS = frozenset({
    "api.github.com",
    "github.com",
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
})
_KNOWN_STATES = frozenset({
    "idle", "unsupported", "disabled", "checking", "downloading", "up_to_date", "ready", "error", "stopped",
})


class UpdateError(Exception):
    """An invalid release, unsafe URL, or failed transfer."""


class UpdateCancelled(UpdateError):
    """A transfer stopped because the application is closing."""


class UpdateIntegrityError(UpdateError):
    """A release asset did not match its published checksum or size."""


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int


@dataclass(frozen=True)
class Release:
    tag: str
    version: str
    version_key: tuple[int, int, int]
    executable: ReleaseAsset
    checksum: ReleaseAsset


def _parse_version(value: str) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    match = _VERSION_RE.fullmatch(value.removeprefix("v"))
    return tuple(map(int, match.groups())) if match else None


def _expected_asset_url(tag: str, name: str) -> str:
    return f"https://github.com/{OWNER}/{REPOSITORY}/releases/download/{tag}/{name}"


def parse_release(payload: bytes | str | dict, current_version: str) -> Release | None:
    """Validate GitHub's latest-release JSON; return only a strictly newer stable build."""
    current_key = _parse_version(current_version)
    if current_key is None:
        raise UpdateError("invalid current version")
    if isinstance(payload, bytes):
        if len(payload) > MAX_RELEASE_JSON:
            raise UpdateError("release JSON too large")
        try:
            payload = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpdateError("invalid release JSON") from exc
    elif isinstance(payload, str):
        if len(payload.encode("utf-8")) > MAX_RELEASE_JSON:
            raise UpdateError("release JSON too large")
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise UpdateError("invalid release JSON") from exc
    if not isinstance(payload, dict):
        raise UpdateError("invalid release object")
    tag = payload.get("tag_name")
    match = _TAG_RE.fullmatch(tag) if isinstance(tag, str) else None
    if not match or payload.get("draft") is not False or payload.get("prerelease") is not False:
        raise UpdateError("release is not a stable semantic version")
    version_key = tuple(map(int, match.groups()))
    if version_key <= current_key:
        return None

    assets = payload.get("assets")
    if not isinstance(assets, list) or len(assets) > 64:
        raise UpdateError("unexpected release assets")
    by_name: dict[str, ReleaseAsset] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            raise UpdateError("invalid release asset")
        name = asset.get("name")
        if not isinstance(name, str):
            continue
        if name not in (EXECUTABLE_NAME, CHECKSUM_NAME):
            continue
        if name in by_name:
            raise UpdateError("unexpected release asset")
        size = asset.get("size")
        maximum = MAX_EXECUTABLE_BYTES if name == EXECUTABLE_NAME else MAX_CHECKSUM_BYTES
        if type(size) is not int or size < 1 or size > maximum:
            raise UpdateError("invalid release asset size")
        url = asset.get("browser_download_url")
        if url != _expected_asset_url(tag, name):
            raise UpdateError("unsafe release asset URL")
        by_name[name] = ReleaseAsset(name=name, url=url, size=size)
    if set(by_name) != {EXECUTABLE_NAME, CHECKSUM_NAME}:
        raise UpdateError("missing release assets")
    return Release(
        tag=tag,
        version=".".join(str(part) for part in version_key),
        version_key=version_key,
        executable=by_name[EXECUTABLE_NAME],
        checksum=by_name[CHECKSUM_NAME],
    )


def _validate_https_url(url: str, initial_url: str) -> None:
    try:
        parsed = urlsplit(url)
        original = urlsplit(initial_url)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise UpdateError("unsafe URL") from exc
    if (parsed.scheme != "https" or hostname not in _ALLOWED_HOSTS or
            parsed.username is not None or parsed.password is not None or
            (port is not None and port != 443) or parsed.fragment):
        raise UpdateError("unsafe URL")
    if hostname in {"api.github.com", "github.com"} and parsed.path != original.path:
        manifest_release_redirect = (
            initial_url == LATEST_MANIFEST_URL and hostname == "github.com" and
            _MANIFEST_RELEASE_PATH_RE.fullmatch(parsed.path) is not None
        )
        if not manifest_release_redirect:
            raise UpdateError("unsafe GitHub redirect path")
    if hostname in {"api.github.com", "github.com"} and parsed.query:
        raise UpdateError("unsafe GitHub redirect query")


class _RestrictedRedirectHandler(HTTPRedirectHandler):
    def __init__(self, initial_url: str):
        super().__init__()
        self._initial_url = initial_url

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_https_url(newurl, self._initial_url)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _make_opener(initial_url: str):
    _validate_https_url(initial_url, initial_url)
    return build_opener(_RestrictedRedirectHandler(initial_url))


def _response_url(response, fallback: str) -> str:
    getter = getattr(response, "geturl", None)
    return getter() if callable(getter) else fallback


def _content_length(response) -> int | None:
    headers = getattr(response, "headers", None)
    raw = headers.get("Content-Length") if headers is not None else None
    if raw is None:
        return None
    try:
        length = int(raw)
    except (TypeError, ValueError) as exc:
        raise UpdateError("invalid content length") from exc
    if length < 0:
        raise UpdateError("invalid content length")
    return length


def _read_bounded(response, url: str, limit: int, expected_size: int | None = None,
                  cancel_event: threading.Event | None = None) -> bytes:
    _validate_https_url(_response_url(response, url), url)
    declared = _content_length(response)
    if declared is not None and (declared > limit or (expected_size is not None and declared != expected_size)):
        raise UpdateError("invalid content length")
    chunks: list[bytes] = []
    total = 0
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise UpdateCancelled("cancelled")
        chunk = response.read(min(DOWNLOAD_CHUNK_BYTES, limit + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise UpdateError("asset exceeds size limit")
        chunks.append(chunk)
    if expected_size is not None and total != expected_size:
        raise UpdateIntegrityError("asset size mismatch")
    return b"".join(chunks)


def _fetch_asset_bytes(asset: ReleaseAsset, release: Release,
                       opener_factory: Callable[[str], object], limit: int,
                       cancel_event: threading.Event | None) -> bytes:
    if asset.url != _expected_asset_url(release.tag, asset.name):
        raise UpdateError("unsafe asset URL")
    opener = opener_factory(asset.url)
    request = Request(asset.url, headers={"User-Agent": "KV-Agent-Desk-Updater"})
    with closing(opener.open(request, timeout=NETWORK_TIMEOUT_SECONDS)) as response:
        return _read_bounded(response, asset.url, limit, asset.size, cancel_event)


def _parse_checksum(value: bytes) -> bytes:
    try:
        text = value.strip()
    except AttributeError as exc:
        raise UpdateIntegrityError("invalid checksum") from exc
    match = _CHECKSUM_RE.fullmatch(text)
    if not match:
        raise UpdateIntegrityError("invalid checksum")
    return bytes.fromhex(match.group(1).decode("ascii"))


def download_verified_release(
    release: Release,
    updates_dir: Path,
    *,
    opener_factory: Callable[[str], object] | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Download exact allowlisted assets, verify SHA-256, then atomically publish the EXE."""
    if release.executable.name != EXECUTABLE_NAME or release.checksum.name != CHECKSUM_NAME:
        raise UpdateError("unexpected release assets")
    if (type(release.executable.size) is not int or not 1 <= release.executable.size <= MAX_EXECUTABLE_BYTES or
            type(release.checksum.size) is not int or not 1 <= release.checksum.size <= MAX_CHECKSUM_BYTES):
        raise UpdateError("invalid release asset size")
    if release.tag != "v" + release.version or release.version_key != _parse_version(release.version):
        raise UpdateError("invalid release version")
    factory = opener_factory or _make_opener
    if cancel_event is not None and cancel_event.is_set():
        raise UpdateCancelled("cancelled")
    checksum_data = _fetch_asset_bytes(
        release.checksum, release, factory, MAX_CHECKSUM_BYTES, cancel_event
    )
    expected_hash = _parse_checksum(checksum_data)

    target_dir = Path(updates_dir) / release.version
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / EXECUTABLE_NAME
    temporary_path: Path | None = None
    digest = hashlib.sha256()
    total = 0
    try:
        if cancel_event is not None and cancel_event.is_set():
            raise UpdateCancelled("cancelled")
        fd, temp_name = tempfile.mkstemp(prefix=".KV-Agent-Desk-", suffix=".part", dir=target_dir)
        temporary_path = Path(temp_name)
        request = Request(release.executable.url, headers={"User-Agent": "KV-Agent-Desk-Updater"})
        with os.fdopen(fd, "wb") as output, closing(factory(release.executable.url).open(
            request, timeout=NETWORK_TIMEOUT_SECONDS
        )) as response:
            _validate_https_url(_response_url(response, release.executable.url), release.executable.url)
            declared = _content_length(response)
            if declared is not None and declared != release.executable.size:
                raise UpdateIntegrityError("executable size mismatch")
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise UpdateCancelled("cancelled")
                chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_EXECUTABLE_BYTES or total > release.executable.size:
                    raise UpdateError("executable exceeds size limit")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if total != release.executable.size:
            raise UpdateIntegrityError("executable size mismatch")
        if digest.digest() != expected_hash:
            raise UpdateIntegrityError("checksum mismatch")
        if cancel_event is not None and cancel_event.is_set():
            raise UpdateCancelled("cancelled")
        os.replace(temporary_path, target_path)
        temporary_path = None
        return target_path
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        return None
    return result.astimezone(timezone.utc)


class UpdateManager:
    """Background, daily release checker; never replaces the running executable itself."""

    def __init__(self, state_dir: Path, current_version: str, *,
                 enabled: Callable[[], bool], on_update_ready: Callable[[Path], None],
                 supported: bool = True):
        if _parse_version(current_version) is None:
            raise ValueError("current_version must be semantic X.Y.Z")
        self.state_dir = Path(state_dir)
        self.current_version = current_version
        self.enabled = enabled
        self.on_update_ready = on_update_ready
        self.supported = bool(supported)
        self.stop_event = threading.Event()
        self._wake = threading.Event()
        self._callback_gate = threading.RLock()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._started = False
        self._checking = False
        self._manual_pending = False
        self._last_checked: str | None = None
        self._last_checked_at: datetime | None = None
        self._available_version: str | None = None
        self._failure_count = 0
        self._state = "idle" if self.supported else "unsupported"
        self._message = "等待更新检查" if self.supported else "当前运行方式不支持自动更新"
        self._load_state()

    def _state_path(self) -> Path:
        return self.state_dir / "updates.json"

    def _load_state(self) -> None:
        try:
            path = self._state_path()
            if not path.is_file() or path.stat().st_size > 64 * 1024:
                return
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(data, dict) or type(data.get("version")) is not int or data.get("version") != 1:
            return
        checked = _parse_timestamp(data.get("last_checked"))
        state = data.get("state")
        available = data.get("available_version")
        failures = data.get("failures", 0)
        if type(failures) is not int or not 0 <= failures <= 100:
            failures = 0
        if checked is not None and checked <= _now() + timedelta(minutes=5):
            self._last_checked_at = checked
            self._last_checked = _timestamp(checked)
        if state in _KNOWN_STATES:
            self._state = state
        if isinstance(available, str) and _TAG_RE.fullmatch(available):
            self._available_version = available
        self._failure_count = failures
        if not self.supported:
            self._state = "unsupported"
            self._message = "当前运行方式不支持自动更新"
        elif checked is not None:
            self._message = "上次检查状态已恢复"

    def _persist_locked(self) -> None:
        payload = {
            "version": 1,
            "last_checked": self._last_checked,
            "available_version": self._available_version,
            "state": self._state,
            "failures": self._failure_count,
        }
        temporary: Path | None = None
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".updates-", suffix=".tmp", dir=self.state_dir)
            temporary = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._state_path())
            temporary = None
        except OSError:
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def _is_enabled(self) -> bool:
        try:
            return bool(self.enabled())
        except Exception:
            return False

    def _interval_locked(self) -> int:
        if self._failure_count:
            index = min(self._failure_count - 1, len(FAILURE_BACKOFF_SECONDS) - 1)
            return FAILURE_BACKOFF_SECONDS[index]
        return CHECK_INTERVAL_SECONDS

    def _due_locked(self, now: datetime | None = None) -> bool:
        if self._last_checked_at is None:
            return True
        elapsed = ((now or _now()) - self._last_checked_at).total_seconds()
        return elapsed >= self._interval_locked()

    def _remaining_delay(self) -> float:
        with self._lock:
            if self._last_checked_at is None:
                return float(CHECK_INTERVAL_SECONDS)
            elapsed = (_now() - self._last_checked_at).total_seconds()
            return max(0.0, self._interval_locked() - elapsed)

    def start(self) -> None:
        if not self.supported or self.stop_event.is_set():
            return
        with self._lock:
            if self._started:
                return
            self._started = True
            self._thread = threading.Thread(
                target=self._run, name="agent-dashboard-updates", daemon=True
            )
            self._thread.start()

    def check_now(self) -> bool:
        """Queue one explicit user-requested check, even if automatic checks are disabled or recent."""
        if not self.supported or self.stop_event.is_set():
            return False
        with self._lock:
            if self._checking or self._manual_pending:
                return False
            self._manual_pending = True
        self.start()
        if self.stop_event.is_set():
            with self._lock:
                self._manual_pending = False
            return False
        self._wake.set()
        return True

    def _run(self) -> None:
        delay = float(INITIAL_DELAY_SECONDS)
        while not self.stop_event.is_set():
            self._wake.wait(delay)
            self._wake.clear()
            if self.stop_event.is_set():
                break
            self._run_due_check()
            delay = self._remaining_delay()
        with self._lock:
            self._manual_pending = False
            self._checking = False

    def _run_due_check(self) -> None:
        with self._lock:
            manual = self._manual_pending
            self._manual_pending = False
            if self._checking or self.stop_event.is_set():
                return
            if not manual:
                enabled = self._is_enabled()
                if self.stop_event.is_set():
                    self._state = "stopped"
                    self._message = "更新检查已停止"
                    return
                if not enabled:
                    self._state = "disabled"
                    self._message = "自动更新已关闭"
                    return
                if not self._due_locked():
                    return
            self._checking = True
            self._state = "checking"
            self._message = "正在检查更新…"
        try:
            payload = self._fetch_latest_release()
            if self.stop_event.is_set():
                return
            release = parse_release(payload, self.current_version)
            if release is None:
                self._finish("up_to_date", "当前已是最新版本", None, failure=False)
                return
            with self._lock:
                self._state = "downloading"
                self._available_version = release.tag
                self._message = "正在下载并校验更新…"
            candidate = download_verified_release(
                release, self.state_dir / "updates", cancel_event=self.stop_event
            )
            if self.stop_event.is_set():
                return
            with self._callback_gate:
                if self.stop_event.is_set():
                    return
                self._finish("ready", "新版已下载并验证", release.tag, failure=False)
                try:
                    self.on_update_ready(candidate)
                except Exception:
                    with self._lock:
                        self._state = "error"
                        self._message = "新版已下载，但启动交接失败"
                        self._persist_locked()
        except UpdateCancelled:
            if not self.stop_event.is_set():
                self._finish("error", "更新下载已中断，将稍后重试", None, failure=True)
        except UpdateIntegrityError:
            self._finish("error", "新版文件校验失败，已跳过", None, failure=True)
        except UpdateError:
            self._finish("error", "更新信息无效或下载失败，将稍后重试", None, failure=True)
        except (OSError, URLError, TimeoutError):
            self._finish("error", "无法连接更新服务器，将稍后重试", None, failure=True)
        except Exception:
            self._finish("error", "更新检查失败，将稍后重试", None, failure=True)
        finally:
            with self._lock:
                self._checking = False

    def _fetch_latest_release(self) -> bytes:
        try:
            return self._fetch_metadata_url(LATEST_MANIFEST_URL)
        except HTTPError as exc:
            if exc.code != 404:
                raise
            exc.close()
        return self._fetch_metadata_url(LATEST_RELEASE_URL)

    def _fetch_metadata_url(self, url: str) -> bytes:
        headers = {"User-Agent": "KV-Agent-Desk-Updater"}
        if url == LATEST_RELEASE_URL:
            headers.update({
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            })
        else:
            headers["Accept"] = "application/json"
        opener = _make_opener(url)
        request = Request(url, headers=headers)
        with closing(opener.open(request, timeout=NETWORK_TIMEOUT_SECONDS)) as response:
            return _read_bounded(response, url, MAX_RELEASE_JSON)

    def _finish(self, state: str, message: str, available_version: str | None,
                *, failure: bool) -> None:
        with self._lock:
            now = _now()
            self._last_checked_at = now
            self._last_checked = _timestamp(now)
            self._available_version = available_version
            self._state = state
            self._message = message
            self._failure_count = min(100, self._failure_count + 1) if failure else 0
            self._persist_locked()

    def status(self) -> dict:
        with self._lock:
            state = self._state
            message = self._message
            if not self.supported:
                state = "unsupported"
                message = "当前运行方式不支持自动更新"
            return {
                "supported": self.supported,
                "checking": self._checking,
                "current_version": self.current_version,
                "available_version": self._available_version,
                "state": state,
                "message": message,
                "last_checked": self._last_checked,
            }

    def close(self) -> None:
        """Stop background work without joining; safe from the manager's own callback thread."""
        with self._callback_gate:
            self.stop_event.set()
            self._wake.set()
            with self._lock:
                self._state = "stopped"
                self._message = "更新检查已停止"
                self._manual_pending = False


__all__ = [
    "CHECKSUM_NAME", "EXECUTABLE_NAME", "LATEST_MANIFEST_URL", "LATEST_RELEASE_URL", "Release", "ReleaseAsset",
    "UpdateError", "UpdateIntegrityError", "UpdateManager", "download_verified_release", "parse_release",
]
