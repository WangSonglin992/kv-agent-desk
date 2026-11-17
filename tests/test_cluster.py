from __future__ import annotations

import json
import hashlib
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agent_dashboard.cluster import (
    MAX_OUTPUT_BYTES,
    ClusterDataError,
    ClusterMonitor,
    ClusterQueryError,
    _run_ssh,
    parse_scontrol_nodes,
)


def node_line(name="n01", *, cfg="cpu=64,mem=256000M", alloc="cpu=8,mem=32768M",
              gres="(null)", state="ALLOCATED", cpu_alloc="8", cpu_total="64",
              alloc_mem="32768", real_mem="256000"):
    alloc_field = f"AllocTRES={alloc} " if alloc is not None else ""
    return (f"NodeName={name} CPUAlloc={cpu_alloc} CPUTot={cpu_total} "
            f"AllocMem={alloc_mem} RealMemory={real_mem} State={state} "
            f"CfgTRES={cfg} {alloc_field}Gres={gres}")


def node(name, *, cpu_allocated=8, gpu_total=0, gpu_allocated=0):
    return {
        "name": name,
        "cpu_allocated": cpu_allocated,
        "cpu_total": 64,
        "memory_allocated_mb": 32768,
        "memory_total_mb": 256000,
        "gpu_allocated": gpu_allocated,
        "gpu_total": gpu_total,
        "state": "ALLOCATED",
    }


def four_nodes(prefix):
    return [node(f"{prefix}-{index:02d}") for index in range(1, 5)]


class ClusterParserTests(unittest.TestCase):
    def test_parses_only_allowlisted_fields_and_memory_is_slurm_mb(self):
        row = parse_scontrol_nodes(
            "NodeName=n01 Arch=x86_64 CPUAlloc=8 CPUTot=64 AllocMem=32768 "
            "RealMemory=256000 State=ALLOCATED CfgTRES=cpu=64,mem=256000M "
            "AllocTRES=cpu=8,mem=32768M Gres=(null) Secret=should-not-escape\n"
        )[0]
        self.assertEqual(row, node("n01"))
        self.assertEqual(set(row), {
            "name", "cpu_allocated", "cpu_total", "memory_allocated_mb",
            "memory_total_mb", "gpu_allocated", "gpu_total", "state",
        })

    def test_generic_gpu_tres_takes_precedence_over_typed_counts(self):
        row = parse_scontrol_nodes(node_line(
            cfg="cpu=64,gres/gpu=4,gres/gpu:a100=4,gres/gpu:v100=2",
            alloc="cpu=8,gres/gpu=1,gres/gpu:a100=1,gres/gpu:v100=1",
        ))[0]
        self.assertEqual((row["gpu_total"], row["gpu_allocated"]), (4, 1))

    def test_typed_gpu_tres_sum_when_generic_count_is_absent(self):
        row = parse_scontrol_nodes(node_line(
            cfg="cpu=64,gres/gpu:a100=4,gres/gpu:v100=2",
            alloc="cpu=8,gres/gpu:a100=1,gres/gpu:v100=1",
        ))[0]
        self.assertEqual((row["gpu_total"], row["gpu_allocated"]), (6, 2))

    def test_gres_is_fallback_and_no_gpu_is_zero(self):
        fallback = parse_scontrol_nodes(node_line(
            cfg="cpu=64,mem=256000M", alloc="cpu=8,mem=32768M",
            gres="gpu:a100:2(S:0-1),gpu:v100:1(S:2)",
        ))[0]
        self.assertEqual((fallback["gpu_total"], fallback["gpu_allocated"]), (3, 0))
        empty = parse_scontrol_nodes(node_line())[0]
        self.assertEqual((empty["gpu_total"], empty["gpu_allocated"]), (0, 0))

    def test_empty_or_omitted_alloctres_means_no_allocated_gpu(self):
        idle = parse_scontrol_nodes(node_line(cpu_alloc="0", alloc=""))[0]
        omitted = parse_scontrol_nodes(node_line(cpu_alloc="0", alloc=None))[0]
        cpu_only = parse_scontrol_nodes(node_line(
            alloc="cpu=6,mem=16384M",
        ))[0]
        self.assertEqual(idle["gpu_allocated"], 0)
        self.assertEqual(omitted["gpu_allocated"], 0)
        self.assertEqual(cpu_only["gpu_allocated"], 0)

    def test_missing_or_invalid_required_values_are_errors(self):
        with self.assertRaises(ClusterDataError):
            parse_scontrol_nodes(node_line().replace("CPUAlloc=8 ", ""))
        with self.assertRaises(ClusterDataError):
            parse_scontrol_nodes(node_line(cpu_alloc="unknown"))
        with self.assertRaises(ClusterDataError):
            parse_scontrol_nodes(b"\xff\x00")

    def test_limits_response_size_and_rejects_more_than_four_nodes(self):
        with self.assertRaises(ClusterDataError):
            parse_scontrol_nodes(b"x" * (MAX_OUTPUT_BYTES + 1))
        rows = parse_scontrol_nodes("\n".join(node_line(f"n{i:02d}") for i in range(4)))
        self.assertEqual(len(rows), 4)
        with self.assertRaises(ClusterDataError):
            parse_scontrol_nodes("\n".join(node_line(f"n{i:02d}") for i in range(5)))


class ClusterMonitorTests(unittest.TestCase):
    def test_fixed_ssh_command_is_batch_only_and_bounded(self):
        completed = type("Completed", (), {"returncode": 0, "stdout": node_line().encode(), "stderr": b"ignored"})()
        with patch("agent_dashboard.cluster._ssh_executable", return_value="/mock/ssh"), \
                patch("agent_dashboard.cluster.subprocess.run", return_value=completed) as run:
            output = _run_ssh("cluster-a")
        args, kwargs = run.call_args
        command = args[0]
        self.assertEqual(command[0], "/mock/ssh")
        self.assertEqual(command[1], "-T")
        self.assertIn("BatchMode=yes", command)
        self.assertIn("ConnectTimeout=8", command)
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("NumberOfPasswordPrompts=0", command)
        self.assertIn("PasswordAuthentication=no", command)
        self.assertIn("KbdInteractiveAuthentication=no", command)
        self.assertEqual(command[-2:], ["cluster-a", "scontrol show nodes --oneliner"])
        self.assertEqual(kwargs["timeout"], 18)
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertNotIn("capture_output", kwargs)
        self.assertEqual(output, node_line().encode())

    def test_oversized_or_failed_ssh_output_never_becomes_snapshot_text(self):
        too_large = type("Completed", (), {
            "returncode": 0, "stdout": b"x" * (MAX_OUTPUT_BYTES + 1), "stderr": b"secret",
        })()
        with patch("agent_dashboard.cluster._ssh_executable", return_value="ssh"), \
                patch("agent_dashboard.cluster.subprocess.run", return_value=too_large):
            with self.assertRaises(ClusterQueryError) as raised:
                _run_ssh("cluster-a")
        self.assertNotIn("secret", raised.exception.detail)

    def test_manual_refresh_is_throttled_to_fifteen_seconds(self):
        monitor = ClusterMonitor(None, threading.Event())
        monitor.configure(True, "cluster-a", 60)
        with patch("agent_dashboard.cluster.time.monotonic", side_effect=[100.0, 114.9, 115.0]):
            self.assertTrue(monitor.request_refresh())
            self.assertFalse(monitor.request_refresh())
            self.assertTrue(monitor.request_refresh())

    def test_success_cache_is_allowlisted_and_scoped_to_host_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cluster.json"
            monitor = ClusterMonitor(path, threading.Event())
            monitor.configure(True, "cluster-a", 60)
            rows = four_nodes("gpu")
            rows[0].update(gpu_total=4, gpu_allocated=1)
            with patch.object(monitor, "_query", return_value=rows):
                monitor._refresh_once("cluster-a", monitor._generation)
            first = monitor.snapshot()
            self.assertTrue(first["known"])
            self.assertFalse(first["stale"])
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(set(saved), {"version", "source_id", "updated_at", "nodes"})
            self.assertNotIn("cluster-a", path.read_text(encoding="utf-8"))
            self.assertEqual(set(saved["nodes"][0]), set(node("gpu-a")))

            restored = ClusterMonitor(path, threading.Event())
            restored.configure(True, "cluster-a", 60)
            cached = restored.snapshot()
            self.assertTrue(cached["known"])
            self.assertTrue(cached["stale"])
            self.assertEqual(cached["updated_at"], first["updated_at"])

            restored.configure(True, "cluster-b", 60)
            switched = restored.snapshot()
            self.assertFalse(switched["known"])
            self.assertEqual(switched["nodes"], [])
            self.assertIsNone(switched["updated_at"])

    def test_cache_symlink_is_not_read_or_replaced_when_symlinks_are_available(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "target.json"
            link = Path(folder) / "cluster.json"
            target.write_text(json.dumps({
                "version": 1,
                "source_id": hashlib.sha256(b"cluster-a").hexdigest(),
                "updated_at": "2026-01-01T00:00:00+00:00",
                "nodes": four_nodes("cached"),
            }), encoding="utf-8")
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink creation unavailable: {type(exc).__name__}")
            target_before = target.read_bytes()

            monitor = ClusterMonitor(link, threading.Event())
            monitor.configure(True, "cluster-a", 60)
            self.assertFalse(monitor.snapshot()["known"])
            with patch.object(monitor, "_query", return_value=four_nodes("fresh")):
                monitor._refresh_once("cluster-a", monitor._generation)

            self.assertTrue(monitor.snapshot()["known"])
            self.assertTrue(link.is_symlink())
            self.assertEqual(target.read_bytes(), target_before)

    def test_nonregular_cache_path_is_not_read_or_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cluster-cache"
            path.mkdir()
            monitor = ClusterMonitor(path, threading.Event())
            monitor.configure(True, "cluster-a", 60)
            self.assertFalse(monitor.snapshot()["known"])
            with patch.object(monitor, "_query", return_value=four_nodes("fresh")):
                monitor._refresh_once("cluster-a", monitor._generation)
            self.assertTrue(path.is_dir())

    def test_failure_keeps_last_good_rows_and_original_update_time(self):
        monitor = ClusterMonitor(None, threading.Event())
        monitor.configure(True, "cluster-a", 60)
        with patch.object(monitor, "_query", return_value=four_nodes("n")):
            monitor._refresh_once("cluster-a", monitor._generation)
        before = monitor.snapshot()
        with patch.object(monitor, "_query", side_effect=ClusterQueryError("Slurm 查询失败")):
            monitor._refresh_once("cluster-a", monitor._generation)
        after = monitor.snapshot()
        self.assertEqual(after["nodes"], before["nodes"])
        self.assertEqual(after["updated_at"], before["updated_at"])
        self.assertTrue(after["stale"])
        self.assertEqual(after["detail"], "更新失败，保留上次数据")

    def test_partial_query_keeps_four_last_good_rows_and_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cluster.json"
            monitor = ClusterMonitor(path, threading.Event())
            monitor.configure(True, "cluster-a", 60)
            with patch.object(monitor, "_query", return_value=four_nodes("good")):
                monitor._refresh_once("cluster-a", monitor._generation)
            before = monitor.snapshot()
            saved_before = json.loads(path.read_text(encoding="utf-8"))

            with patch.object(monitor, "_query", return_value=[node("partial")]):
                monitor._refresh_once("cluster-a", monitor._generation)
            after = monitor.snapshot()
            saved_after = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(after["known"])
            self.assertTrue(after["stale"])
            self.assertEqual(len(after["nodes"]), 4)
            self.assertEqual(after["nodes"], before["nodes"])
            self.assertEqual(after["updated_at"], before["updated_at"])
            self.assertEqual(saved_after, saved_before)

    def test_worker_waits_until_enabled_and_interval_change_wakes_it(self):
        stop = threading.Event()
        monitor = ClusterMonitor(None, stop)
        calls: list[str] = []
        first = threading.Event()
        second = threading.Event()

        def query(host):
            calls.append(host)
            (first if len(calls) == 1 else second).set()
            return four_nodes(host)

        with patch.object(monitor, "_query", side_effect=query):
            monitor.start()
            self.assertFalse(first.wait(0.05))
            monitor.configure(False, "cluster-a", 3600)
            self.assertFalse(first.wait(0.05))
            monitor.configure(True, "cluster-a", 3600)
            self.assertTrue(first.wait(1))
            deadline = time.monotonic() + 1
            while monitor.snapshot()["checking"] and time.monotonic() < deadline:
                threading.Event().wait(0.005)
            with monitor.lock:
                monitor._last_poll_finished_monotonic -= 16
            monitor.configure(True, "cluster-a", 15)
            self.assertTrue(second.wait(1))
            self.assertEqual(calls, ["cluster-a", "cluster-a"])
            deadline = time.monotonic() + 1
            while monitor.snapshot()["checking"] and time.monotonic() < deadline:
                threading.Event().wait(0.005)
            second.clear()
            monitor.configure(True, "cluster-a", 30)
            monitor.configure(True, "cluster-a", 15)
            self.assertFalse(second.wait(0.05))
        stop.set()
        monitor._wake.set()
        if monitor._thread:
            monitor._thread.join(1)

    def test_host_switch_discards_an_inflight_old_host_result(self):
        stop = threading.Event()
        monitor = ClusterMonitor(None, stop)
        old_started = threading.Event()
        release_old = threading.Event()
        new_finished = threading.Event()

        def query(host):
            if host == "cluster-a":
                old_started.set()
                release_old.wait(2)
                return four_nodes("old-node")
            new_finished.set()
            return four_nodes("new-node")

        with patch.object(monitor, "_query", side_effect=query):
            monitor.configure(True, "cluster-a", 3600)
            monitor.start()
            self.assertTrue(old_started.wait(1))
            monitor.configure(True, "cluster-b", 3600)
            release_old.set()
            self.assertTrue(new_finished.wait(1))
        stop.set()
        monitor._wake.set()
        if monitor._thread:
            monitor._thread.join(1)
        self.assertEqual([row["name"] for row in monitor.snapshot()["nodes"]], [
            f"new-node-{index:02d}" for index in range(1, 5)
        ])

    def test_demo_uses_four_synthetic_nodes_without_querying_ssh(self):
        monitor = ClusterMonitor(None, threading.Event(), demo=True)
        with patch.object(monitor, "_query", side_effect=AssertionError("SSH must not run")):
            monitor.configure(True, "cluster-a", 30)
            monitor.start()
        snapshot = monitor.snapshot()
        self.assertTrue(snapshot["known"])
        self.assertEqual([row["name"] for row in snapshot["nodes"]], [
            "node-01", "node-02", "node-03", "node-04",
        ])
        self.assertEqual(snapshot["refresh_seconds"], 30)
        self.assertFalse(snapshot["stale"])


if __name__ == "__main__":
    unittest.main()
