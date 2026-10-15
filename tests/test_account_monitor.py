from pathlib import Path
import tempfile
import threading
import unittest

from agent_dashboard.accounts.monitor import AccountMonitor


class AccountMonitorTests(unittest.TestCase):
    def test_failure_retains_latest_value_and_restart_restores_it(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "accounts.json"
            monitor = AccountMonitor(path, threading.Event())
            monitor.accept([{"id": "deepseek_lab", "name": "课题组 DeepSeek", "known": True,
                             "value": "947.14", "currency": "CNY", "kind": "balance", "brand": "deepseek"}])
            monitor.accept([{"id": "deepseek_lab", "known": False, "detail": "网络不可用"}])
            account = monitor.snapshot()[0]
            self.assertEqual(account["value"], "947.14")
            self.assertTrue(account["stale"])
            self.assertEqual(AccountMonitor(path, threading.Event()).snapshot()[0]["value"], "947.14")

    def test_demo_never_starts_network_threads(self):
        monitor = AccountMonitor(None, threading.Event(), demo=True)
        monitor.start()
        self.assertFalse(monitor.started)
        self.assertEqual(len(monitor.snapshot()), 2)
