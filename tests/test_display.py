import threading
import time
import unittest
from unittest.mock import Mock, patch

from agent_dashboard.display import (
    DisplayIOError,
    DisplayNotFoundError,
    MAX_JPEG_BYTES,
    USBDisplay,
    _FILE_ATTRIBUTE_NORMAL,
    _FILE_FLAG_OVERLAPPED,
    _build_ly_frame,
    _create_device_file,
)


def handshake(model_byte=2, sub_byte=4):
    response = bytearray(512)
    response[0] = 0x03
    response[1] = 0xFF
    response[8] = 0x01
    response[20] = model_byte
    response[22] = sub_byte
    return bytes(response)


def ack():
    return bytes((0x03, 0xFF, 0, 0, 0, 0, 0, 0, 0x01)) + bytes(503)


class FakeBackend:
    path = r"\\?\USB#VID_0416&PID_5408#mock"

    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.writes = []
        self.read_requests = []
        self.closed = False
        self.active_writes = 0
        self.max_active_writes = 0
        self._lock = threading.Lock()

    def write(self, data, timeout_ms):
        with self._lock:
            self.active_writes += 1
            self.max_active_writes = max(self.max_active_writes, self.active_writes)
        time.sleep(0.003)
        self.writes.append((bytes(data), timeout_ms))
        with self._lock:
            self.active_writes -= 1

    def read(self, length, timeout_ms):
        self.read_requests.append((length, timeout_ms))
        return self.replies.pop(0)

    def close(self):
        self.closed = True


class DisplayProtocolTests(unittest.TestCase):
    def test_frame_uses_496_byte_payload_and_pads_ly_to_four_chunks(self):
        jpeg = b"\xff\xd8" + bytes(range(256)) * 2 + b"\xff\xd9"
        frame = _build_ly_frame(jpeg)

        self.assertEqual(len(frame), 4 * 512)
        self.assertEqual(frame[:2], b"\x01\xff")
        self.assertEqual(int.from_bytes(frame[2:6], "little"), len(jpeg))
        self.assertEqual(int.from_bytes(frame[6:8], "little"), 496)
        self.assertEqual(frame[8], 1)
        self.assertEqual(int.from_bytes(frame[9:11], "little"), 2)
        self.assertEqual(int.from_bytes(frame[11:13], "little"), 0)
        self.assertEqual(frame[16:512], jpeg[:496])
        self.assertEqual(int.from_bytes(frame[512 + 6:512 + 8], "little"), len(jpeg) - 496)
        self.assertEqual(frame[512 + 16:512 + 16 + len(jpeg) - 496], jpeg[496:])
        self.assertEqual(frame[3 * 512:], bytes(512))

    def test_exact_chunk_multiple_gets_empty_terminator(self):
        jpeg = b"\xff\xd8" + bytes(492) + b"\xff\xd9"  # 496 bytes
        frame = _build_ly_frame(jpeg)

        self.assertEqual(len(frame), 4 * 512)
        self.assertEqual(int.from_bytes(frame[9:11], "little"), 2)
        self.assertEqual(int.from_bytes(frame[512 + 6:512 + 8], "little"), 0)
        self.assertEqual(frame[512 + 16:512 + 32], bytes(16))

    def test_connect_performs_handshake_and_reports_panel_resolution(self):
        backend = FakeBackend([handshake(model_byte=2), ack()])
        display = USBDisplay()

        with patch("agent_dashboard.display._open_winusb_device", return_value=backend):
            status = display.connect()
            self.assertEqual(status["state"], "connected")
            self.assertTrue(status["connected"])
            self.assertEqual((status["width"], status["height"]), (1920, 462))
            self.assertEqual(len(backend.writes), 1)
            self.assertEqual(len(backend.writes[0][0]), 2048)
            self.assertEqual(backend.writes[0][0][:9], bytes((2, 0xFF, 0, 0, 0, 0, 0, 0, 1)))

            display.send_jpeg(b"\xff\xd8test-image\xff\xd9")
            self.assertEqual(len(backend.writes), 2)
            self.assertEqual(len(backend.writes[1][0]), 2048)
            self.assertEqual(backend.read_requests, [(512, 1000), (512, 1000)])
            self.assertEqual(display.status(), status)

        display.close()
        self.assertTrue(backend.closed)
        self.assertFalse(display.status()["connected"])
        self.assertEqual(display.status()["state"], "closed")

    def test_create_file_uses_winusb_overlapped_and_exclusive_flags(self):
        kernel32 = Mock()
        kernel32.CreateFileW.return_value = 123

        handle = _create_device_file(kernel32, FakeBackend.path)

        self.assertEqual(handle, 123)
        args = kernel32.CreateFileW.call_args.args
        self.assertEqual(args[2], 0)  # exclusive; a second sender cannot open it
        self.assertEqual(args[5], _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OVERLAPPED)

    def test_handshake_model_byte_selects_shared_id_11_3_size(self):
        backend = FakeBackend([handshake(model_byte=5)])
        display = USBDisplay()

        with patch("agent_dashboard.display._open_winusb_device", return_value=backend):
            status = display.connect()

        self.assertEqual((status["width"], status["height"]), (1920, 400))
        display.close()

    def test_bad_handshake_releases_backend_and_reports_error(self):
        backend = FakeBackend([bytes(512)])
        display = USBDisplay()

        with patch("agent_dashboard.display._open_winusb_device", return_value=backend):
            status = display.connect()

        self.assertFalse(status["connected"])
        self.assertEqual(status["state"], "error")
        self.assertTrue(backend.closed)

    def test_missing_device_is_a_diagnostic_status(self):
        display = USBDisplay()
        with patch("agent_dashboard.display._open_winusb_device", side_effect=DisplayNotFoundError("missing")):
            status = display.connect()
        self.assertEqual(status["state"], "not_found")
        self.assertIn("missing", status["detail"])

    def test_non_target_ids_are_rejected_before_enumeration(self):
        display = USBDisplay(pid=0x5409)

        with patch("agent_dashboard.display._open_winusb_device") as open_device:
            status = display.connect()

        self.assertEqual(status["state"], "unsupported")
        open_device.assert_not_called()

    def test_frame_failure_closes_handles_and_updates_status(self):
        backend = FakeBackend([handshake(model_byte=2), ack()])
        display = USBDisplay()

        with patch("agent_dashboard.display._open_winusb_device", return_value=backend):
            display.connect()
            backend.write = lambda data, timeout_ms: (_ for _ in ()).throw(TimeoutError("timed out"))
            with self.assertRaises(DisplayIOError):
                display.send_jpeg(b"\xff\xd8test-image\xff\xd9")

        self.assertTrue(backend.closed)
        self.assertEqual(display.status()["state"], "error")
        self.assertFalse(display.status()["connected"])

    def test_frame_ack_is_read_without_assuming_unverified_bytes(self):
        backend = FakeBackend([handshake(model_byte=2), bytes(512)])
        display = USBDisplay()

        with patch("agent_dashboard.display._open_winusb_device", return_value=backend):
            self.assertTrue(display.connect()["connected"])
            display.send_jpeg(b"\xff\xd8test-image\xff\xd9")

        self.assertFalse(backend.closed)
        self.assertTrue(display.status()["connected"])
        display.close()

    def test_invalid_or_oversize_jpeg_is_rejected_without_usb_io(self):
        backend = FakeBackend([handshake(model_byte=2)])
        display = USBDisplay()

        with patch("agent_dashboard.display._open_winusb_device", return_value=backend):
            display.connect()
            with self.assertRaises(ValueError):
                display.send_jpeg(b"not a jpeg")
            oversized = b"\xff\xd8" + bytes(MAX_JPEG_BYTES - 1) + b"\xff\xd9"
            with self.assertRaises(ValueError):
                display.send_jpeg(oversized)

        self.assertEqual(len(backend.writes), 1)  # handshake only
        display.close()

    def test_concurrent_frames_are_serialized(self):
        backend = FakeBackend([handshake(model_byte=2), ack(), ack()])
        display = USBDisplay()

        with patch("agent_dashboard.display._open_winusb_device", return_value=backend):
            display.connect()
            image = b"\xff\xd8test-image\xff\xd9"
            workers = [threading.Thread(target=display.send_jpeg, args=(image,)) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()

        self.assertEqual(backend.max_active_writes, 1)
        self.assertEqual(len(backend.writes), 3)  # handshake + two complete frames
        display.close()


if __name__ == "__main__":
    unittest.main()
