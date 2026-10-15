"""Read ZCode's Coding Plan quota from its official read-only API.

The selected provider's API key is loaded only from ``.zcode/v2/config.json``
for one in-memory HTTPS GET. ZCode's official contract uses
``GET /api/monitor/usage/quota/limit`` with the complete key in ``authorization``
(no Bearer prefix); ``percentage`` is used percentage and ``nextResetTime`` is
epoch milliseconds. The result is cached in memory for at least 60 seconds.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


_QUOTA_PATH = "/api/monitor/usage/quota/limit"
_MIN_REFRESH_SECONDS = 60.0
_REQUEST_TIMEOUT_SECONDS = 8.0
_MAX_RESPONSE_BYTES = 256 * 1024
_ALLOWED_HOSTS = {
    "bigmodel": frozenset({"bigmodel.cn", "open.bigmodel.cn"}),
    "zai": frozenset({"api.z.ai"}),
}
_WINDOW_TYPES = frozenset({"TOKENS_LIMIT", "CREDIT_LIMIT"})


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the authorization header to a redirect target.
        return None


class _QuotaSourceUnavailable(Exception):
    def __init__(self, reason: str, source: str):
        super().__init__(reason)
        self.reason = reason
        self.source = source


class ZCodeQuotaCollector:
    """Return a subscription quota snapshot; calls are throttled to once/minute."""

    def __init__(self, root: Path | str | None = None):
        path = Path(root).expanduser() if root is not None else Path.home() / ".zcode"
        if path.is_file() or path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
            path = path.parent.parent.parent
        self.root = path
        self._lock = threading.Lock()
        self._last_attempt_at: float | None = None
        self._last_result = self._unknown_snapshot(
            "尚未查询官方订阅额度。", str(self.root / "v2" / "config.json")
        )

    def collect(self) -> dict[str, Any]:
        with self._lock:
            now_mono = time.monotonic()
            if (
                self._last_attempt_at is not None
                and now_mono - self._last_attempt_at < _MIN_REFRESH_SECONDS
            ):
                return deepcopy(self._last_result)
            self._last_attempt_at = now_mono

            attempt_source = str(self.root / "v2" / "config.json")
            try:
                api_key, url, provider_source = self._resolve_target()
                attempt_source = provider_source
                result = deepcopy(self._last_result)
                fetched_any = False
                plan_level: str | None = None

                try:
                    quota_payload = self._fetch_json(url, api_key)
                    quota_snapshot = self._parse_response(quota_payload, url)
                    plan_level = quota_snapshot.get("_plan_level")
                    result.update(
                        {
                            "five_hour": quota_snapshot["five_hour"],
                            "weekly": quota_snapshot["weekly"],
                            "source": quota_snapshot["source"],
                        }
                    )
                    fetched_any = True
                except _QuotaSourceUnavailable as exc:
                    if not result["five_hour"]["known"] and not result["weekly"]["known"]:
                        result["source"] = f"{exc.source}；{exc.reason}"
                except Exception:
                    if not result["five_hour"]["known"] and not result["weekly"]["known"]:
                        result["source"] = f"{provider_source}；官方额度查询失败，异常详情已隐藏。"

                subscription_url = self._subscription_url(url)
                try:
                    details_payload = self._fetch_json(subscription_url, api_key)
                    details = self._parse_subscription_details(details_payload, subscription_url)
                    result.update(details)
                    fetched_any = True
                except _QuotaSourceUnavailable as exc:
                    if not self._has_plan_metadata(result):
                        result["metadata_source"] = f"{exc.source}；{exc.reason}"
                except Exception:
                    if not self._has_plan_metadata(result):
                        result["metadata_source"] = (
                            f"ZCode {subscription_url}；官方订阅详情查询失败，异常详情已隐藏。"
                        )
                if not result.get("plan_spec") and plan_level:
                    result["plan_spec"] = plan_level.strip().title()
                    existing_source = result.get("metadata_source")
                    level_source = f"ZCode官方额度接口 {url} (data.level)"
                    result["metadata_source"] = (
                        f"{existing_source}；{level_source}" if existing_source else level_source
                    )

                if fetched_any:
                    result["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            except _QuotaSourceUnavailable as exc:
                result = self._keep_last_or_unknown(exc.reason, exc.source)
            except Exception:
                # Do not surface exception text or response bodies: either may
                # contain request headers, an API key, or provider diagnostics.
                result = self._keep_last_or_unknown(
                    "官方订阅额度查询失败；异常详情已隐藏。", attempt_source
                )

            self._last_result = result
            return deepcopy(result)

    def _resolve_target(self) -> tuple[str, str, str]:
        settings_path = self.root / "v2" / "setting.json"
        config_path = self.root / "v2" / "config.json"
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError):
            raise _QuotaSourceUnavailable("ZCode provider选择配置不可读。", str(settings_path)) from None

        family = settings.get("providerFamilyDomain")
        if family not in _ALLOWED_HOSTS:
            raise _QuotaSourceUnavailable(
                "本机没有可识别的 Z.ai / BigModel Coding Plan provider。", str(settings_path)
            )
        selections = settings.get("modelProviderFamilySelectedKeys")
        selected = selections.get(family) if isinstance(selections, dict) else None
        if not isinstance(selected, str) or not selected.startswith("coding-plan:"):
            raise _QuotaSourceUnavailable(
                "当前选中项不是 Coding Plan API Key provider，未发起额度请求。",
                str(settings_path),
            )
        provider_id = selected[len("coding-plan:") :].strip()
        if not provider_id:
            raise _QuotaSourceUnavailable("当前 Coding Plan provider ID 为空。", str(settings_path))

        try:
            provider = self._read_selected_provider(config_path, provider_id)
        except (OSError, ValueError, TypeError):
            raise _QuotaSourceUnavailable(
                "当前选中的 Coding Plan provider 配置不可读。", str(config_path)
            ) from None
        if not isinstance(provider, dict):
            raise _QuotaSourceUnavailable(
                "当前选中的 Coding Plan provider 配置不存在。", str(config_path)
            )

        options = provider.get("options")
        options = options if isinstance(options, dict) else {}
        key_value = options.get("apiKey")
        api_key = key_value.strip() if isinstance(key_value, str) else ""
        if not api_key:
            raise _QuotaSourceUnavailable(
                "当前选中的 Coding Plan provider 没有可用 API Key；未尝试其他账户。",
                str(config_path),
            )

        base_url = options.get("baseURL") or options.get("baseUrl")
        url = self._official_url(family, base_url)
        if not url:
            raise _QuotaSourceUnavailable(
                "当前 provider origin 不是已核实的官方 HTTPS host；为避免外发密钥，已跳过请求。",
                str(config_path),
            )
        safe_source = f"ZCode 已选 {family} Coding Plan 配置 → {url}"
        return api_key, url, safe_source

    @staticmethod
    def _subscription_url(quota_url: str) -> str:
        parsed = urlsplit(quota_url)
        return f"{parsed.scheme}://{parsed.hostname}/api/biz/subscription/list"

    @staticmethod
    def _read_selected_provider(config_path: Path, provider_id: str) -> object:
        """Decode only the selected provider object, not other providers' keys."""
        text = config_path.read_text(encoding="utf-8-sig")
        root_start = ZCodeQuotaCollector._skip_space(text, 0)
        provider_span = ZCodeQuotaCollector._member_span(text, root_start, "provider")
        if provider_span is None:
            raise ValueError("provider map missing")
        selected_span = ZCodeQuotaCollector._member_span(text, provider_span[0], provider_id)
        if selected_span is None:
            return None
        return json.loads(text[selected_span[0] : selected_span[1]])

    @staticmethod
    def _member_span(text: str, object_start: int, target_key: str) -> tuple[int, int] | None:
        decoder = json.JSONDecoder()
        index = ZCodeQuotaCollector._skip_space(text, object_start)
        if index >= len(text) or text[index] != "{":
            raise ValueError("expected object")
        index += 1
        while True:
            index = ZCodeQuotaCollector._skip_space(text, index)
            if index >= len(text):
                raise ValueError("unterminated object")
            if text[index] == "}":
                return None
            key, key_end = decoder.raw_decode(text, index)
            if not isinstance(key, str):
                raise ValueError("invalid object key")
            index = ZCodeQuotaCollector._skip_space(text, key_end)
            if index >= len(text) or text[index] != ":":
                raise ValueError("missing member separator")
            value_start = ZCodeQuotaCollector._skip_space(text, index + 1)
            value_end = ZCodeQuotaCollector._skip_json_value(text, value_start)
            if key == target_key:
                return value_start, value_end
            index = ZCodeQuotaCollector._skip_space(text, value_end)
            if index < len(text) and text[index] == ",":
                index += 1
            elif index < len(text) and text[index] == "}":
                return None
            else:
                raise ValueError("invalid object member boundary")

    @staticmethod
    def _skip_json_value(text: str, start: int) -> int:
        if start >= len(text):
            raise ValueError("missing JSON value")
        if text[start] == '"':
            index = start + 1
            escaped = False
            while index < len(text):
                char = text[index]
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    return index + 1
                index += 1
            raise ValueError("unterminated JSON string")
        if text[start] in "[{":
            stack = ["}" if text[start] == "{" else "]"]
            index = start + 1
            in_string = False
            escaped = False
            while index < len(text) and stack:
                char = text[index]
                if in_string:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        in_string = False
                elif char == '"':
                    in_string = True
                elif char == "{":
                    stack.append("}")
                elif char == "[":
                    stack.append("]")
                elif char in "}]":
                    if char != stack[-1]:
                        raise ValueError("mismatched JSON container")
                    stack.pop()
                index += 1
            if stack:
                raise ValueError("unterminated JSON container")
            return index
        index = start
        while index < len(text) and text[index] not in ",}]" and not text[index].isspace():
            index += 1
        if index == start:
            raise ValueError("invalid primitive")
        return index

    @staticmethod
    def _skip_space(text: str, index: int) -> int:
        while index < len(text) and text[index].isspace():
            index += 1
        return index

    @staticmethod
    def _official_url(family: str, base_url: object) -> str | None:
        if isinstance(base_url, str) and base_url.strip():
            parsed = urlsplit(base_url.strip())
            hostname = (parsed.hostname or "").lower()
            if (
                parsed.scheme != "https"
                or hostname not in _ALLOWED_HOSTS[family]
                or parsed.port is not None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                return None
            origin = f"https://{hostname}"
        else:
            default_host = "api.z.ai" if family == "zai" else "bigmodel.cn"
            origin = f"https://{default_host}"
        return origin + _QUOTA_PATH

    @staticmethod
    def _fetch_json(url: str, api_key: str) -> dict[str, Any]:
        request = Request(
            url,
            headers={"authorization": api_key, "accept": "application/json"},
            method="GET",
        )
        opener = build_opener(_NoRedirectHandler())
        try:
            with opener.open(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
                if response.geturl() != url or response.status != 200:
                    raise ValueError("unexpected upstream response")
                body = response.read(_MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            exc.close()
            raise RuntimeError("quota HTTP request failed") from None
        except (URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("quota HTTP request failed") from None
        if len(body) > _MAX_RESPONSE_BYTES:
            raise ValueError("quota response too large")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid quota response")
        return payload

    def _parse_response(self, payload: dict[str, Any], url: str) -> dict[str, Any]:
        code = payload.get("code")
        if payload.get("success") is False or code not in (None, 0, 200):
            raise _QuotaSourceUnavailable("官方额度接口返回失败状态。", f"ZCode官方额度接口 {url}")
        data = payload.get("data")
        limits = data.get("limits") if isinstance(data, dict) else None
        if not isinstance(limits, list):
            raise _QuotaSourceUnavailable(
                "官方额度响应没有可解析的limits数组。", f"ZCode官方额度接口 {url}"
            )

        five_matches = [
            item
            for item in limits
            if isinstance(item, dict)
            and item.get("type") in _WINDOW_TYPES
            and item.get("unit") == 3
            and item.get("number") == 5
        ]
        weekly_matches = [
            item
            for item in limits
            if isinstance(item, dict)
            and item.get("type") in _WINDOW_TYPES
            and item.get("unit") == 6
            and item.get("number") in (None, 1)
        ]
        now = datetime.now().astimezone()
        source = f"ZCode官方额度接口 {url}"
        five_hour, five_reason = self._window(five_matches)
        weekly, weekly_reason = self._window(weekly_matches)
        reasons = [reason for reason in (five_reason, weekly_reason) if reason]
        if reasons:
            source += "；" + "；".join(reasons)
        return {
            "name": "ZCode",
            "five_hour": five_hour,
            "weekly": weekly,
            "updated_at": now.isoformat(timespec="seconds"),
            "source": source,
            "_plan_level": data.get("level") if isinstance(data.get("level"), str) else None,
        }

    def _parse_subscription_details(self, payload: dict[str, Any], url: str) -> dict[str, Any]:
        code = payload.get("code")
        if payload.get("success") is False or code not in (None, 0, 200):
            raise _QuotaSourceUnavailable("官方订阅详情接口返回失败状态。", f"ZCode {url}")
        data = payload.get("data")
        if not isinstance(data, list):
            raise _QuotaSourceUnavailable("官方订阅详情响应不是订阅列表。", f"ZCode {url}")

        candidates = []
        for item in data:
            if not isinstance(item, dict):
                continue
            looks_coding = any(
                isinstance(item.get(field), str) and "coding" in item[field].lower()
                for field in ("productId", "productName")
            )
            if (
                looks_coding
                and item.get("status") == "VALID"
                and item.get("inCurrentPeriod") is True
            ):
                candidates.append(item)
        if len(candidates) != 1:
            return {
                "plan_name": None,
                "plan_spec": None,
                "expires_at": None,
                "metadata_source": f"ZCode {url}；未找到唯一有效的Coding Plan订阅项。",
            }

        item = candidates[0]
        product_name = self._safe_metadata_text(item.get("productName"))
        billing_cycle = self._billing_cycle_label(item.get("billingCycle"))
        auto_renew = item.get("autoRenew") is True or item.get("autoRenew") == 1
        if auto_renew:
            expires_at = self._valid_period_end(item.get("valid"))
            expiry_field = "valid"
        else:
            expires_at = self._parse_local_datetime(item.get("nextRenewTime"))
            expiry_field = "nextRenewTime"
            if expires_at is None:
                expires_at = self._valid_period_end(item.get("valid"))
                expiry_field = "valid"
        fields = ["productName", "billingCycle", expiry_field]
        return {
            "plan_name": product_name,
            "plan_spec": billing_cycle,
            "expires_at": expires_at,
            "metadata_source": f"ZCode {url} ({','.join(fields)})",
        }

    @staticmethod
    def _billing_cycle_label(value: object) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.strip()
        labels = {
            "monthly": "月付",
            "quarterly": "季付",
            "yearly": "年付",
            "annually": "年付",
        }
        return labels.get(normalized.lower(), normalized[:64])

    @staticmethod
    def _safe_metadata_text(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.split())
        return normalized[:128] if normalized else None

    @classmethod
    def _valid_period_end(cls, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        dates = re.findall(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2})?", value)
        return cls._parse_local_datetime(dates[-1]) if dates else None

    @staticmethod
    def _parse_local_datetime(value: object) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        raw = value.strip()
        date_only = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw))
        normalized = raw.replace(" ", "T", 1) if " " in raw and "T" not in raw else raw
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(
                    tzinfo=timezone.utc if date_only else datetime.now().astimezone().tzinfo
                )
            return parsed.astimezone().isoformat(timespec="seconds")
        except (OverflowError, ValueError):
            return None

    @classmethod
    def _window(cls, matches: list[dict[str, Any]]) -> tuple[dict[str, Any], str | None]:
        if len(matches) != 1:
            reason = "额度窗口匹配不唯一" if matches else "官方响应未提供该额度窗口"
            return {"remaining_percent": None, "reset_at": None, "known": False}, reason
        item = matches[0]
        percentage = item.get("percentage")
        if isinstance(percentage, bool) or not isinstance(percentage, (int, float)):
            return {"remaining_percent": None, "reset_at": None, "known": False}, "响应缺少有效percentage"
        used = float(percentage)
        if not math.isfinite(used) or used < 0:
            return {"remaining_percent": None, "reset_at": None, "known": False}, "percentage超出可识别范围"
        remaining = max(0.0, min(100.0, 100.0 - used))
        reset_at = cls._reset_at(item.get("nextResetTime"))
        reason = None if reset_at else "官方响应未提供有效重置时间"
        return {"remaining_percent": remaining, "reset_at": reset_at, "known": True}, reason

    @staticmethod
    def _reset_at(value: object) -> str | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        milliseconds = float(value)
        if not math.isfinite(milliseconds) or milliseconds <= 0:
            return None
        try:
            return datetime.fromtimestamp(milliseconds / 1000).astimezone().isoformat(timespec="seconds")
        except (OSError, OverflowError, ValueError):
            return None

    def _keep_last_or_unknown(self, reason: str, source: str) -> dict[str, Any]:
        if (
            self._last_result["five_hour"]["known"]
            or self._last_result["weekly"]["known"]
            or self._has_plan_metadata(self._last_result)
        ):
            return deepcopy(self._last_result)
        return self._unknown_snapshot(reason, source)

    @staticmethod
    def _has_plan_metadata(result: dict[str, Any]) -> bool:
        return any(result.get(field) is not None for field in ("plan_name", "plan_spec", "expires_at"))

    @staticmethod
    def _unknown_snapshot(reason: str, source: str) -> dict[str, Any]:
        unknown_window = {"remaining_percent": None, "reset_at": None, "known": False}
        return {
            "name": "ZCode",
            "five_hour": dict(unknown_window),
            "weekly": dict(unknown_window),
            "updated_at": None,
            "plan_name": None,
            "plan_spec": None,
            "expires_at": None,
            "metadata_source": f"{source}；尚无可用订阅详情。",
            "source": f"{source}；{reason}",
        }
