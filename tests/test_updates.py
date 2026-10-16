import hashlib
import io
import json
from email.message import Message
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request
from unittest.mock import patch

import agent_dashboard.updates as updates
from agent_dashboard.updates import (
    CHECKSUM_NAME,
    EXECUTABLE_NAME,
    MAX_EXECUTABLE_BYTES,
    LATEST_MANIFEST_URL,
    LATEST_RELEASE_URL,
    UpdateError,
    UpdateIntegrityError,
    UpdateManager,
    _RestrictedRedirectHandler,
    download_verified_release,
    parse_release,
)


def release_payload(tag="v0.5.1", executable=b"verified-executable", checksum=None, **overrides):
    checksum = checksum if checksum is not None else (
        hashlib.sha256(executable).hexdigest() + "  KV-Agent-Desk.exe\n"
    ).encode("ascii")
    assets = [
        {
            "name": EXECUTABLE_NAME,
            "size": len(executable),
            "browser_download_url": f"https://github.com/WangSonglin992/kv-agent-desk/releases/download/{tag}/{EXECUTABLE_NAME}",
        },
        {
            "name": CHECKSUM_NAME,
            "size": len(checksum),
            "browser_download_url": f"https://github.com/WangSonglin992/kv-agent-desk/releases/download/{tag}/{CHECKSUM_NAME}",
        },
    ]
    payload = {"tag_name": tag, "draft": False, "prerelease": False, "assets": assets}
    payload.update(overrides)
    return payload, executable, checksum


class FakeResponse(io.BytesIO):
    def __init__(self, data, url, headers=None):
        super().__init__(data)
        self._url = url
        self.headers = headers or {"Content-Length": str(len(data))}

    def geturl(self):
        return self._url


class FakeOpener:
    def __init__(self, responses):
        self.responses = dict(responses)
        self.requests = []

    def open(self, request, timeout=None):
        url = request.full_url
        self.requests.append((url, timeout))
        if url not in self.responses:
            raise AssertionError(f"unexpected URL requested: {url}")
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


class UpdateReleaseTests(unittest.TestCase):
    def test_release_requires_new_stable_semver_and_exact_assets(self):
        payload, _exe, _checksum = release_payload()
        release = parse_release(payload, "0.5.0")
        self.assertEqual(release.tag, "v0.5.1")
        self.assertEqual(release.version, "0.5.1")
        self.assertIsNone(parse_release(payload, "0.5.1"))
        self.assertIsNone(parse_release(payload, "0.6.0"))

        for bad in (
            dict(payload, draft=True),
            dict(payload, prerelease=True),
            dict(payload, tag_name="v0.5.1-rc.1"),
            dict(payload, tag_name="v00.5.1"),
        ):
            with self.subTest(bad=bad.get("tag_name")), self.assertRaises(UpdateError):
                parse_release(bad, "0.5.0")

    def test_real_release_attachments_are_ignored_except_selected_assets(self):
        payload, _exe, _checksum = release_payload()
        payload["assets"].extend([
            {"name": "KV-Agent-Desk-windows-x64.zip", "size": 100, "browser_download_url": "https://example.test/zip"},
            {"name": "KV-Agent-Desk-windows-x64.zip.sha256", "size": 64, "browser_download_url": "https://example.test/zip.sha256"},
            {"name": "Source code (zip)", "size": 100, "browser_download_url": "https://example.test/source.zip"},
            {"name": "Source code (tar.gz)", "size": 100, "browser_download_url": "https://example.test/source.tar.gz"},
        ])
        self.assertEqual(parse_release(payload, "0.5.0").tag, "v0.5.1")
        payload["assets"].extend(
            {"name": f"other-{index}"} for index in range(64 - len(payload["assets"]))
        )
        self.assertEqual(parse_release(payload, "0.5.0").tag, "v0.5.1")
        payload["assets"].append({"name": "over-limit"})
        with self.assertRaises(UpdateError):
            parse_release(payload, "0.5.0")

        payload, _exe, _checksum = release_payload()
        payload["assets"].append(dict(payload["assets"][0]))
        with self.assertRaises(UpdateError):
            parse_release(payload, "0.5.0")

    def test_release_rejects_wrong_host_path_and_oversized_metadata(self):
        payload, _exe, _checksum = release_payload()
        wrong_host = json.loads(json.dumps(payload))
        wrong_host["assets"][0]["browser_download_url"] = "https://evil.example/KV-Agent-Desk.exe"
        wrong_path = json.loads(json.dumps(payload))
        wrong_path["assets"][1]["browser_download_url"] = wrong_path["assets"][1]["browser_download_url"].replace(
            "/WangSonglin992/kv-agent-desk/", "/someone-else/other-repo/"
        )
        oversized = json.loads(json.dumps(payload))
        oversized["assets"][0]["size"] = MAX_EXECUTABLE_BYTES + 1
        for bad in (wrong_host, wrong_path, oversized):
            with self.subTest(asset=bad["assets"][0]), self.assertRaises(UpdateError):
                parse_release(bad, "0.5.0")

    def test_release_json_is_bounded_and_current_version_must_be_semver(self):
        payload, _exe, _checksum = release_payload()
        with self.assertRaises(UpdateError):
            parse_release(b" " * (updates.MAX_RELEASE_JSON + 1), "0.5.0")
        with self.assertRaises(UpdateError):
            parse_release(payload, "nightly")

    def test_restricted_redirect_allows_only_https_allowlisted_hosts_and_github_path(self):
        handler = _RestrictedRedirectHandler(LATEST_RELEASE_URL)
        request = Request(LATEST_RELEASE_URL)
        headers = Message()
        allowed = handler.redirect_request(
            request, None, 302, "Found", headers,
            "https://objects.githubusercontent.com/releases/download/asset?sig=opaque",
        )
        self.assertIsNotNone(allowed)
        for url in (
            "http://objects.githubusercontent.com/releases/download/asset",
            "https://evil.example/file",
            "https://api.github.com/repos/other/repo/releases/latest",
        ):
            with self.subTest(url=url), self.assertRaises(UpdateError):
                handler.redirect_request(request, None, 302, "Found", headers, url)

        manifest_handler = _RestrictedRedirectHandler(LATEST_MANIFEST_URL)
        manifest_request = Request(LATEST_MANIFEST_URL)
        accepted = manifest_handler.redirect_request(
            manifest_request, None, 302, "Found", headers,
            "https://github.com/WangSonglin992/kv-agent-desk/releases/download/v0.6.0/KV-Agent-Desk.update.json",
        )
        self.assertIsNotNone(accepted)
        for url in (
            "https://github.com/other/repo/releases/download/v0.6.0/KV-Agent-Desk.update.json",
            "https://github.com/WangSonglin992/kv-agent-desk/releases/download/v00.6.0/KV-Agent-Desk.update.json",
            "https://github.com/WangSonglin992/kv-agent-desk/releases/download/v0.6.0/other.json",
        ):
            with self.subTest(url=url), self.assertRaises(UpdateError):
                manifest_handler.redirect_request(manifest_request, None, 302, "Found", headers, url)

    def test_verified_download_atomically_replaces_target(self):
        payload, executable, checksum = release_payload()
        release = parse_release(payload, "0.5.0")
        responses = {
            release.checksum.url: FakeResponse(checksum, release.checksum.url),
            release.executable.url: FakeResponse(executable, release.executable.url),
        }
        opener = FakeOpener(responses)
        seen_initial_urls = []

        def opener_factory(url):
            seen_initial_urls.append(url)
            return opener

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / release.version / EXECUTABLE_NAME
            target.parent.mkdir()
            target.write_bytes(b"old-user-file")
            result = download_verified_release(release, Path(folder), opener_factory=opener_factory)
            self.assertEqual(result, target)
            self.assertEqual(target.read_bytes(), executable)
            self.assertFalse(list(target.parent.glob("*.part")))
        self.assertEqual(set(seen_initial_urls), {release.executable.url, release.checksum.url})
        self.assertEqual(len(opener.requests), 2)
        self.assertTrue(all(timeout == updates.NETWORK_TIMEOUT_SECONDS for _, timeout in opener.requests))

    def test_checksum_mismatch_preserves_existing_file_and_cleans_temp(self):
        payload, executable, _checksum = release_payload(checksum=b"0" * 64)
        release = parse_release(payload, "0.5.0")
        responses = {
            release.checksum.url: FakeResponse(b"0" * 64, release.checksum.url),
            release.executable.url: FakeResponse(executable, release.executable.url),
        }
        opener = FakeOpener(responses)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / release.version / EXECUTABLE_NAME
            target.parent.mkdir()
            target.write_bytes(b"keep-me")
            with self.assertRaises(UpdateIntegrityError):
                download_verified_release(release, Path(folder), opener_factory=lambda _url: opener)
            self.assertEqual(target.read_bytes(), b"keep-me")
            self.assertFalse(list(target.parent.glob("*.part")))

    def test_download_rejects_final_response_on_unapproved_host(self):
        payload, executable, checksum = release_payload()
        release = parse_release(payload, "0.5.0")
        responses = {
            release.checksum.url: FakeResponse(checksum, release.checksum.url),
            release.executable.url: FakeResponse(executable, "https://evil.example/asset"),
        }
        opener = FakeOpener(responses)
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(UpdateError):
            download_verified_release(release, Path(folder), opener_factory=lambda _url: opener)


class UpdateManagerTests(unittest.TestCase):
    def _wait_for(self, manager, state, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = manager.status()
            if current["state"] == state and not current["checking"]:
                return current
            time.sleep(0.01)
        self.fail(f"manager did not reach {state}: {manager.status()}")

    def test_source_or_demo_unsupported_and_auto_disabled_never_auto_fetch(self):
        with tempfile.TemporaryDirectory() as folder:
            unsupported = UpdateManager(
                Path(folder) / "source", "0.5.0", enabled=lambda: True,
                on_update_ready=lambda _path: self.fail("unsupported callback"), supported=False,
            )
            self.assertFalse(unsupported.check_now())
            self.assertEqual(unsupported.status()["state"], "unsupported")
            unsupported.close()

            disabled = UpdateManager(
                Path(folder) / "disabled", "0.5.0", enabled=lambda: False,
                on_update_ready=lambda _path: self.fail("disabled callback"),
            )
            try:
                with patch.object(disabled, "_fetch_latest_release", side_effect=AssertionError("network used")), \
                     patch.object(updates, "INITIAL_DELAY_SECONDS", 0):
                    disabled.start()
                    deadline = time.monotonic() + 2
                    while disabled.status()["state"] != "disabled" and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertEqual(disabled.status()["state"], "disabled")
            finally:
                disabled.close()

    def test_public_manifest_is_preferred_and_api_fallback_is_404_only(self):
        payload, _exe, _checksum = release_payload(tag="v0.6.0")
        manifest_bytes = json.dumps(payload).encode("utf-8")
        with tempfile.TemporaryDirectory() as folder:
            manager = UpdateManager(Path(folder), "0.5.0", enabled=lambda: True, on_update_ready=lambda _p: None)
            opener = FakeOpener({LATEST_MANIFEST_URL: FakeResponse(manifest_bytes, LATEST_MANIFEST_URL)})
            with patch.object(updates, "_make_opener", return_value=opener):
                result = manager._fetch_latest_release()
            self.assertEqual(result, manifest_bytes)
            self.assertEqual([url for url, _timeout in opener.requests], [LATEST_MANIFEST_URL])

            not_found = HTTPError(LATEST_MANIFEST_URL, 404, "Not Found", Message(), io.BytesIO(b"not exposed"))
            opener = FakeOpener({
                LATEST_MANIFEST_URL: not_found,
                LATEST_RELEASE_URL: FakeResponse(manifest_bytes, LATEST_RELEASE_URL),
            })
            with patch.object(updates, "_make_opener", return_value=opener):
                self.assertEqual(manager._fetch_latest_release(), manifest_bytes)
            self.assertEqual([url for url, _timeout in opener.requests], [LATEST_MANIFEST_URL, LATEST_RELEASE_URL])

            forbidden = HTTPError(LATEST_MANIFEST_URL, 403, "Forbidden", Message(), io.BytesIO(b"private response"))
            opener = FakeOpener({LATEST_MANIFEST_URL: forbidden})
            with patch.object(updates, "_make_opener", return_value=opener), self.assertRaises(HTTPError):
                manager._fetch_latest_release()
            self.assertEqual([url for url, _timeout in opener.requests], [LATEST_MANIFEST_URL])
            manager.close()

    def test_manual_check_works_when_automatic_updates_are_disabled(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = UpdateManager(
                Path(folder), "0.5.0", enabled=lambda: False,
                on_update_ready=lambda _path: self.fail("unexpected candidate"),
            )
            calls = []
            manager._fetch_latest_release = lambda: calls.append(True) or release_payload(tag="v0.5.0")[0]
            try:
                self.assertTrue(manager.check_now())
                self._wait_for(manager, "up_to_date")
                self.assertEqual(calls, [True])
            finally:
                manager.close()

    def test_check_now_is_background_single_flight_and_persists_metadata_only(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = UpdateManager(
                Path(folder), "0.5.0", enabled=lambda: True,
                on_update_ready=lambda _path: self.fail("unexpected candidate"),
            )
            calls = []
            started = threading.Event()

            def fetch():
                calls.append(True)
                started.set()
                time.sleep(0.05)
                return release_payload(tag="v0.5.0")[0]

            manager._fetch_latest_release = fetch
            try:
                self.assertTrue(manager.check_now())
                self.assertFalse(manager.check_now())
                self.assertTrue(started.wait(2))
                self._wait_for(manager, "up_to_date")
                self.assertEqual(len(calls), 1)
                stored = json.loads((Path(folder) / "updates.json").read_text(encoding="utf-8"))
                self.assertEqual(set(stored), {"version", "last_checked", "available_version", "state", "failures"})
                self.assertNotIn("url", json.dumps(stored).lower())
                self.assertEqual(stored["state"], "up_to_date")
                self.assertTrue(manager.check_now())  # an explicit manual request bypasses the daily window
                deadline = time.monotonic() + 2
                while len(calls) < 2 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self._wait_for(manager, "up_to_date")
                self.assertEqual(len(calls), 2)
            finally:
                manager.close()

    def test_persisted_daily_limit_and_failure_backoff(self):
        with tempfile.TemporaryDirectory() as folder:
            recent = {
                "version": 1,
                "last_checked": updates._timestamp(updates._now()),
                "available_version": None,
                "state": "up_to_date",
                "failures": 0,
            }
            Path(folder, "updates.json").write_text(json.dumps(recent), encoding="utf-8")
            manager = UpdateManager(
                Path(folder), "0.5.0", enabled=lambda: True,
                on_update_ready=lambda _path: None,
            )
            with patch.object(manager, "_fetch_latest_release", side_effect=AssertionError("rate limit ignored")):
                try:
                    with patch.object(updates, "INITIAL_DELAY_SECONDS", 0):
                        manager.start()
                        time.sleep(0.05)
                    self.assertFalse(manager._due_locked())
                    self.assertLessEqual(manager._remaining_delay(), updates.CHECK_INTERVAL_SECONDS)
                finally:
                    manager.close()

        with tempfile.TemporaryDirectory() as folder:
            manager = UpdateManager(
                Path(folder), "0.5.0", enabled=lambda: True,
                on_update_ready=lambda _path: None,
            )
            manager._fetch_latest_release = lambda: (_ for _ in ()).throw(OSError("private URL must not surface"))
            try:
                self.assertTrue(manager.check_now())
                error = self._wait_for(manager, "error")
                self.assertEqual(error["message"], "无法连接更新服务器，将稍后重试")
                self.assertFalse(manager._due_locked())
                stored = json.loads(Path(folder, "updates.json").read_text(encoding="utf-8"))
                self.assertEqual(stored["failures"], 1)
            finally:
                manager.close()

    def test_close_during_download_never_calls_ready_callback(self):
        payload, _exe, _checksum = release_payload()
        release = parse_release(payload, "0.5.0")
        entered = threading.Event()
        continue_download = threading.Event()
        callbacks = []
        with tempfile.TemporaryDirectory() as folder:
            manager = UpdateManager(
                Path(folder), "0.5.0", enabled=lambda: True,
                on_update_ready=lambda path: callbacks.append(path),
            )
            manager._fetch_latest_release = lambda: payload

            def delayed_download(*_args, **_kwargs):
                entered.set()
                continue_download.wait(2)
                return Path(folder) / release.version / EXECUTABLE_NAME

            try:
                with patch.object(updates, "download_verified_release", side_effect=delayed_download):
                    self.assertTrue(manager.check_now())
                    self.assertTrue(entered.wait(2))
                    manager.close()
                    continue_download.set()
                    deadline = time.monotonic() + 2
                    while manager.status()["checking"] and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertFalse(callbacks)
                    self.assertEqual(manager.status()["state"], "stopped")
            finally:
                manager.close()

    def test_verified_candidate_calls_ready_callback_and_callback_can_close(self):
        payload, _exe, _checksum = release_payload()
        ready = threading.Event()
        callbacks = []
        with tempfile.TemporaryDirectory() as folder:
            manager = None

            def on_ready(path):
                callbacks.append(path)
                ready.set()
                manager.close()

            manager = UpdateManager(
                Path(folder), "0.5.0", enabled=lambda: True, on_update_ready=on_ready,
            )
            manager._fetch_latest_release = lambda: payload
            candidate = Path(folder) / "updates" / "0.5.1" / EXECUTABLE_NAME
            try:
                with patch.object(updates, "download_verified_release", return_value=candidate):
                    self.assertTrue(manager.check_now())
                    self.assertTrue(ready.wait(2))
                    self._wait_for(manager, "stopped")
                self.assertEqual(callbacks, [candidate])
            finally:
                manager.close()

    def test_checksum_failure_never_calls_ready_callback(self):
        payload, _exe, _checksum = release_payload()
        callbacks = []
        with tempfile.TemporaryDirectory() as folder:
            manager = UpdateManager(
                Path(folder), "0.5.0", enabled=lambda: True,
                on_update_ready=lambda path: callbacks.append(path),
            )
            manager._fetch_latest_release = lambda: payload
            try:
                with patch.object(updates, "download_verified_release",
                                  side_effect=UpdateIntegrityError("bad checksum")):
                    self.assertTrue(manager.check_now())
                    result = self._wait_for(manager, "error")
                self.assertEqual(result["message"], "新版文件校验失败，已跳过")
                self.assertFalse(callbacks)
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
