"""Read DeepSeek API account balances without making inference requests."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import threading
import time
from typing import Any

from .common import get_json, read_secret_env


_BALANCE_URL = "https://api.deepseek.com/user/balance"
_MIN_REFRESH_SECONDS = 60.0
_ACCOUNTS = (
    ("deepseek_lab", "课题组 DeepSeek", "DeepSeek_public"),
    ("deepseek_personal", "个人 DeepSeek", "DeepSeek_personal"),
)


class DeepSeekAccounts:
    """Collect the two configured DeepSeek account balances at most once/minute."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_attempt: dict[str, float] = {}
        self._last_result = {
            account_id: self._unknown(account_id, name)
            for account_id, name, _ in _ACCOUNTS
        }

    def collect(self) -> list[dict[str, Any]]:
        with self._lock:
            now_mono = time.monotonic()
            for account_id, name, env_name in _ACCOUNTS:
                try:
                    key = read_secret_env(env_name)
                except Exception:
                    key = None

                if not isinstance(key, str) or not key.strip():
                    self._mark_unavailable(
                        account_id,
                        name,
                        "未配置当前账户的 DeepSeek API Key；未发出请求。",
                    )
                    continue

                last_attempt = self._last_attempt.get(account_id)
                if last_attempt is not None and now_mono - last_attempt < _MIN_REFRESH_SECONDS:
                    continue

                self._last_attempt[account_id] = now_mono
                try:
                    payload = get_json(
                        _BALANCE_URL,
                        key.strip(),
                        header="Authorization",
                        bearer=True,
                        timeout=5,
                    )
                    value, currency, is_available = self._parse_balance(payload)
                    if is_available is True:
                        detail = "已读取 DeepSeek 官方账户余额。"
                    elif is_available is False:
                        detail = "已读取余额；DeepSeek 当前标记账户余额不足。"
                    else:
                        detail = "已读取 DeepSeek 官方账户余额，余额可用状态未提供。"
                    self._last_result[account_id] = {
                        "id": account_id,
                        "name": name,
                        "brand": "deepseek",
                        "kind": "balance",
                        "known": True,
                        "value": value,
                        "currency": currency,
                        "remaining_percent": None,
                        "reset_at": None,
                        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                        "source": _BALANCE_URL,
                        "detail": detail,
                        "stale": False,
                    }
                except Exception:
                    self._mark_unavailable(
                        account_id,
                        name,
                        "DeepSeek 官方余额查询失败；已保留上次成功结果。",
                    )

            return [deepcopy(self._last_result[account_id]) for account_id, _, _ in _ACCOUNTS]

    @staticmethod
    def _unknown(account_id: str, name: str) -> dict[str, Any]:
        return {
            "id": account_id,
            "name": name,
            "brand": "deepseek",
            "kind": "balance",
            "known": False,
            "value": None,
            "currency": None,
            "remaining_percent": None,
            "reset_at": None,
            "updated_at": None,
            "source": _BALANCE_URL,
            "detail": "尚未成功读取 DeepSeek 官方余额。",
            "stale": True,
        }

    def _mark_unavailable(self, account_id: str, name: str, detail: str) -> None:
        previous = self._last_result.get(account_id)
        if previous is None:
            previous = self._unknown(account_id, name)
        result = deepcopy(previous)
        result["detail"] = detail
        result["stale"] = True
        self._last_result[account_id] = result

    @staticmethod
    def _parse_balance(payload: object) -> tuple[str, str, bool | None]:
        if not isinstance(payload, dict):
            raise ValueError("invalid response")
        balance_infos = payload.get("balance_infos")
        if not isinstance(balance_infos, list):
            raise ValueError("invalid response")

        candidates: list[tuple[str, str]] = []
        for item in balance_infos:
            if not isinstance(item, dict):
                continue
            currency = item.get("currency")
            raw_value = item.get("total_balance")
            if not isinstance(currency, str) or currency.upper() not in {"CNY", "USD"}:
                continue
            if not isinstance(raw_value, (str, int, float)) or isinstance(raw_value, bool):
                continue
            try:
                amount = Decimal(str(raw_value))
            except (InvalidOperation, ValueError):
                continue
            if (
                not amount.is_finite()
                or amount < 0
                or len(amount.as_tuple().digits) > 80
                or (amount != 0 and abs(amount.adjusted()) > 100)
            ):
                continue
            candidates.append((currency.upper(), format(amount, "f")))

        if not candidates:
            raise ValueError("no usable balance")
        # The display has one amount per account. Prefer the domestic currency
        # when the API returns multiple currencies; otherwise keep API order.
        currency, value = next(
            (candidate for candidate in candidates if candidate[0] == "CNY"),
            candidates[0],
        )
        is_available = payload.get("is_available")
        if not isinstance(is_available, bool):
            is_available = None
        return value, currency, is_available
