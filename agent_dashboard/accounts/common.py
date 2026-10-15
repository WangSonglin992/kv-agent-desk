"""Restricted read-only transport and named environment credential lookup."""

from __future__ import annotations

import json
import os
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

_ENV_NAMES = frozenset({"DeepSeek_public", "DeepSeek_personal"})
_HTTPS_HOSTS = frozenset({"api.deepseek.com"})


def read_secret_env(name: str) -> str | None:
    if name not in _ENV_NAMES:
        raise ValueError("Unsupported account credential")
    # Re-read named Windows values so rotating a system/user key does not
    # require restarting the long-running dashboard process.
    if os.name == "nt":
        import winreg
        for hive, path in ((winreg.HKEY_CURRENT_USER, "Environment"),
                           (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
            try:
                with winreg.OpenKey(hive, path, 0, winreg.KEY_READ) as key:
                    value, _ = winreg.QueryValueEx(key, name)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            except OSError:
                continue
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    return None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def get_json(url: str, key: str, header: str = "Authorization", bearer: bool = True, timeout: float = 5) -> dict:
    parsed = urlsplit(url)
    official = parsed.scheme == "https" and parsed.hostname in _HTTPS_HOSTS and parsed.port in (None, 443)
    if not official or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Unsupported account endpoint")
    if key and key in url:
        raise ValueError("Account credentials must be sent in the authorization header")
    request = Request(url, headers={header: ("Bearer " if bearer else "") + key,
                                   "Accept": "application/json"}, method="GET")
    try:
        handlers = [_NoRedirect()]
        with build_opener(*handlers).open(request, timeout=timeout) as response:
            if response.status != 200 or response.geturl() != url:
                raise ValueError("Unexpected account response")
            body = response.read(262145)
        if len(body) > 262144:
            raise ValueError("Account response too large")
        result = json.loads(body.decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError("Invalid account response")
        return result
    except HTTPError as exc:
        status = int(exc.code)
        exc.close()
        raise RuntimeError(f"Account query failed (HTTP {status})") from None
    except (URLError, TimeoutError, OSError):
        raise RuntimeError("Account query failed (network/timeout)") from None
    except Exception:
        # Do not expose request/response exceptions, headers or credential values.
        raise RuntimeError("Account query failed") from None
