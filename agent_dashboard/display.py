"""Native WinUSB output for the Thermalright LY LCD (VID:PID 0416:5408).

The wire format below is a clean-room implementation of public protocol facts
documented by trofeo-lcd-rs and thermalright-trcc-linux:
https://github.com/haven80/trofeo-lcd-rs/blob/main/src/lib.rs
https://github.com/Lexonight1/thermalright-trcc-linux/blob/main/src/trcc/adapters/device/ly_lcd.py
Those upstream projects are GPL-3.0-or-later; this file does not copy their
implementation.
The current trofeo-lcd-rs source identifies the 9.16 panel as 1920x462 and a
shared-ID 11.3 panel as 1920x400 when handshake byte 20 is 5.  A MacTR README
also says 1920x480, so the dimensions are exposed in status for the renderer.

No third-party USB DLL is loaded: Windows SetupAPI locates only the requested
VID/PID interface, then the inbox WinUSB API performs bulk transfers.
"""

from __future__ import annotations

import ctypes
import os
import struct
import threading
import uuid
from ctypes import wintypes
from typing import Any


DEFAULT_VID = 0x0416
DEFAULT_PID = 0x5408
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 462
ALT_PANEL_HEIGHT = 400
MAX_JPEG_BYTES = 450_000

_USB_INTERFACE_GUID = uuid.UUID("A5DCBF10-6530-11D2-901F-00C04FB951ED")
_DIGCF_PRESENT = 0x00000002
_DIGCF_DEVICEINTERFACE = 0x00000010
_ERROR_NO_MORE_ITEMS = 259
_ERROR_INSUFFICIENT_BUFFER = 122
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_FLAG_OVERLAPPED = 0x40000000
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

_PIPE_BULK = 2  # USBD_PIPE_TYPE.USBD_PIPE_TYPE_BULK
_PIPE_TRANSFER_TIMEOUT = 3
_HANDSHAKE_TIMEOUT_MS = 1000
_WRITE_TIMEOUT_MS = 5000
_READ_TIMEOUT_MS = 1000
_USB_CHUNK_SIZE = 512
_CHUNK_HEADER_SIZE = 16
_CHUNK_DATA_SIZE = _USB_CHUNK_SIZE - _CHUNK_HEADER_SIZE
_USB_WRITE_SIZE = 4096


class DisplayError(RuntimeError):
    """Base exception for display transport failures."""


class DisplayUnsupportedError(DisplayError):
    """The OS or driver cannot provide the required WinUSB interface."""


class DisplayNotFoundError(DisplayError):
    """No present device matching the requested VID/PID was found."""


class DisplayIOError(DisplayError):
    """The display disconnected or a USB transfer failed."""


def _build_ly_frame(jpeg: bytes) -> bytes:
    """Pack one LY JPEG into 512-byte chunks, padded for 4096-byte writes."""
    size = len(jpeg)
    chunk_count = size // _CHUNK_DATA_SIZE + 1
    used = bytearray(chunk_count * _USB_CHUNK_SIZE)
    final_data_size = size % _CHUNK_DATA_SIZE

    for index in range(chunk_count):
        offset = index * _USB_CHUNK_SIZE
        is_final = index == chunk_count - 1
        data_size = final_data_size if is_final else _CHUNK_DATA_SIZE
        used[offset] = 0x01
        used[offset + 1] = 0xFF
        struct.pack_into("<I", used, offset + 2, size)
        struct.pack_into("<H", used, offset + 6, data_size)
        used[offset + 8] = 0x01  # LY (0416:5408); LY1 uses 2.
        struct.pack_into("<H", used, offset + 9, chunk_count)
        struct.pack_into("<H", used, offset + 11, index)
        source_offset = index * _CHUNK_DATA_SIZE
        data_offset = offset + _CHUNK_HEADER_SIZE
        used[data_offset:data_offset + data_size] = jpeg[source_offset:source_offset + data_size]

    # LY pads chunk count to a multiple of four; this also makes the final
    # USB write either 2048 or 4096 bytes, as in the current upstream sender.
    padded_count = (chunk_count + 3) // 4 * 4
    used.extend(bytes((padded_count - chunk_count) * _USB_CHUNK_SIZE))
    return bytes(used)


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", _GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.c_size_t),
    ]


class _USB_INTERFACE_DESCRIPTOR(ctypes.Structure):
    _fields_ = [
        ("bLength", ctypes.c_ubyte),
        ("bDescriptorType", ctypes.c_ubyte),
        ("bInterfaceNumber", ctypes.c_ubyte),
        ("bAlternateSetting", ctypes.c_ubyte),
        ("bNumEndpoints", ctypes.c_ubyte),
        ("bInterfaceClass", ctypes.c_ubyte),
        ("bInterfaceSubClass", ctypes.c_ubyte),
        ("bInterfaceProtocol", ctypes.c_ubyte),
        ("iInterface", ctypes.c_ubyte),
    ]


class _WINUSB_PIPE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PipeType", ctypes.c_int),
        ("PipeId", ctypes.c_ubyte),
        ("MaximumPacketSize", wintypes.WORD),
        ("Interval", ctypes.c_ubyte),
    ]


def _guid(value: uuid.UUID) -> _GUID:
    return _GUID.from_buffer_copy(value.bytes_le)


def _windows_error(api_name: str) -> OSError:
    code = ctypes.get_last_error()
    detail = ctypes.FormatError(code).strip() if code else "未知的 Windows 错误"
    return OSError(code, f"{api_name}: {detail}")


class _WinUSBDevice:
    """Small owner for one WinUSB interface and its two bulk endpoints."""

    def __init__(
        self,
        kernel32: Any,
        winusb: Any,
        file_handle: Any,
        interface_handle: Any,
        path: str,
        pipe_out: int,
        pipe_in: int,
    ) -> None:
        self._kernel32 = kernel32
        self._winusb = winusb
        self._file_handle = file_handle
        self._interface_handle = interface_handle
        self.path = path
        self.pipe_out = pipe_out
        self.pipe_in = pipe_in

    def _set_timeout(self, pipe_id: int, timeout_ms: int) -> None:
        value = wintypes.DWORD(timeout_ms)
        ok = self._winusb.WinUsb_SetPipePolicy(
            self._interface_handle,
            pipe_id,
            _PIPE_TRANSFER_TIMEOUT,
            ctypes.sizeof(value),
            ctypes.byref(value),
        )
        if not ok:
            raise _windows_error("WinUsb_SetPipePolicy(PIPE_TRANSFER_TIMEOUT)")

    def write(self, data: bytes, timeout_ms: int) -> None:
        self._set_timeout(self.pipe_out, timeout_ms)
        buffer = ctypes.create_string_buffer(data)
        transferred = wintypes.ULONG()
        ok = self._winusb.WinUsb_WritePipe(
            self._interface_handle,
            self.pipe_out,
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
            len(data),
            ctypes.byref(transferred),
            None,
        )
        if not ok:
            raise _windows_error("WinUsb_WritePipe")
        if transferred.value != len(data):
            raise DisplayIOError(
                f"USB bulk 写入不完整：{transferred.value}/{len(data)} 字节"
            )

    def read(self, length: int, timeout_ms: int) -> bytes:
        self._set_timeout(self.pipe_in, timeout_ms)
        buffer = ctypes.create_string_buffer(length)
        transferred = wintypes.ULONG()
        ok = self._winusb.WinUsb_ReadPipe(
            self._interface_handle,
            self.pipe_in,
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
            length,
            ctypes.byref(transferred),
            None,
        )
        if not ok:
            raise _windows_error("WinUsb_ReadPipe")
        return buffer.raw[:transferred.value]

    def close(self) -> None:
        interface = self._interface_handle
        file_handle = self._file_handle
        self._interface_handle = None
        self._file_handle = None
        if interface:
            try:
                self._winusb.WinUsb_Free(interface)
            except Exception:
                pass
        if file_handle and file_handle != _INVALID_HANDLE_VALUE:
            try:
                self._kernel32.CloseHandle(file_handle)
            except Exception:
                pass


def _load_windows_apis() -> tuple[Any, Any, Any]:
    if os.name != "nt":
        raise DisplayUnsupportedError("USB 直连目前只支持 Windows 的 WinUSB 驱动")
    try:
        setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        winusb = ctypes.WinDLL("winusb", use_last_error=True)
    except (AttributeError, OSError) as exc:
        raise DisplayUnsupportedError(f"无法载入 Windows WinUSB API：{exc}") from exc

    setupapi.SetupDiGetClassDevsW.argtypes = [
        ctypes.POINTER(_GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD,
    ]
    setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(_GUID), wintypes.DWORD,
        ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
    ]
    setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
    setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    winusb.WinUsb_Initialize.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    winusb.WinUsb_Initialize.restype = wintypes.BOOL
    winusb.WinUsb_QueryInterfaceSettings.argtypes = [
        ctypes.c_void_p, ctypes.c_ubyte, ctypes.POINTER(_USB_INTERFACE_DESCRIPTOR),
    ]
    winusb.WinUsb_QueryInterfaceSettings.restype = wintypes.BOOL
    winusb.WinUsb_QueryPipe.argtypes = [
        ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_ubyte,
        ctypes.POINTER(_WINUSB_PIPE_INFORMATION),
    ]
    winusb.WinUsb_QueryPipe.restype = wintypes.BOOL
    winusb.WinUsb_SetPipePolicy.argtypes = [
        ctypes.c_void_p, ctypes.c_ubyte, wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p,
    ]
    winusb.WinUsb_SetPipePolicy.restype = wintypes.BOOL
    winusb.WinUsb_WritePipe.argtypes = [
        ctypes.c_void_p, ctypes.c_ubyte, ctypes.POINTER(ctypes.c_ubyte),
        wintypes.ULONG, ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p,
    ]
    winusb.WinUsb_WritePipe.restype = wintypes.BOOL
    winusb.WinUsb_ReadPipe.argtypes = [
        ctypes.c_void_p, ctypes.c_ubyte, ctypes.POINTER(ctypes.c_ubyte),
        wintypes.ULONG, ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p,
    ]
    winusb.WinUsb_ReadPipe.restype = wintypes.BOOL
    winusb.WinUsb_Free.argtypes = [ctypes.c_void_p]
    winusb.WinUsb_Free.restype = wintypes.BOOL
    return setupapi, kernel32, winusb


def _interface_path(set_handle: Any, interface_data: _SP_DEVICE_INTERFACE_DATA,
                    setupapi: Any) -> str:
    required = wintypes.DWORD()
    ctypes.set_last_error(0)
    setupapi.SetupDiGetDeviceInterfaceDetailW(
        set_handle, ctypes.byref(interface_data), None, 0, ctypes.byref(required), None,
    )
    error = ctypes.get_last_error()
    if required.value == 0 or error not in (0, _ERROR_INSUFFICIENT_BUFFER):
        raise _windows_error("SetupDiGetDeviceInterfaceDetailW(size)")

    detail = ctypes.create_string_buffer(required.value)
    # The path starts at byte offset 4 in the variable-size W structure.  The
    # API's cbSize is 8 on 64-bit Windows and 6 on 32-bit Windows.
    ctypes.c_uint32.from_buffer(detail).value = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
    ok = setupapi.SetupDiGetDeviceInterfaceDetailW(
        set_handle, ctypes.byref(interface_data), ctypes.cast(detail, ctypes.c_void_p),
        required.value, ctypes.byref(required), None,
    )
    if not ok:
        raise _windows_error("SetupDiGetDeviceInterfaceDetailW(path)")
    return detail.raw[4:required.value].decode("utf-16-le", errors="ignore").split("\0", 1)[0]


def _open_path(path: str, kernel32: Any, winusb: Any) -> _WinUSBDevice:
    file_handle = _create_device_file(kernel32, path)
    interface_handle = ctypes.c_void_p()
    ok = winusb.WinUsb_Initialize(file_handle, ctypes.byref(interface_handle))
    if not ok:
        error = _windows_error("WinUsb_Initialize")
        kernel32.CloseHandle(file_handle)
        raise DisplayUnsupportedError(
            f"设备接口无法由 WinUSB 打开（可能绑定了其他驱动）：{error}"
        ) from error

    try:
        descriptor = _USB_INTERFACE_DESCRIPTOR()
        if not winusb.WinUsb_QueryInterfaceSettings(interface_handle, 0, ctypes.byref(descriptor)):
            raise _windows_error("WinUsb_QueryInterfaceSettings")

        pipe_out = None
        pipe_in = None
        for index in range(descriptor.bNumEndpoints):
            pipe = _WINUSB_PIPE_INFORMATION()
            if not winusb.WinUsb_QueryPipe(interface_handle, 0, index, ctypes.byref(pipe)):
                raise _windows_error("WinUsb_QueryPipe")
            if pipe.PipeType != _PIPE_BULK:
                continue
            if pipe.PipeId & 0x80:
                pipe_in = pipe.PipeId
            else:
                pipe_out = pipe.PipeId

        if pipe_out is None or pipe_in is None:
            raise DisplayUnsupportedError(
                "WinUSB 已打开设备，但接口没有可配对的 bulk OUT/IN 端点"
            )
        return _WinUSBDevice(
            kernel32, winusb, file_handle, interface_handle, path, pipe_out, pipe_in,
        )
    except Exception:
        try:
            winusb.WinUsb_Free(interface_handle)
        finally:
            kernel32.CloseHandle(file_handle)
        raise


def _create_device_file(kernel32: Any, path: str) -> Any:
    """Open one matching path exclusively, with WinUSB's required overlapped flag.

    WinUsb_Initialize requires FILE_FLAG_OVERLAPPED (Microsoft Learn:
    https://learn.microsoft.com/windows/win32/api/winusb/nf-winusb-winusb_initialize).
    A zero share mode reserves this device path for one sender at a time.
    """
    file_handle = kernel32.CreateFileW(
        path,
        _GENERIC_READ | _GENERIC_WRITE,
        0,  # exclusive: don't let TRCC and this dashboard send concurrently
        None,
        _OPEN_EXISTING,
        _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OVERLAPPED,
        None,
    )
    if not file_handle or file_handle == _INVALID_HANDLE_VALUE:
        raise _windows_error("CreateFileW(WinUSB interface)")
    return file_handle


def _open_winusb_device(vid: int, pid: int) -> _WinUSBDevice:
    """Find and open a WinUSB interface matching only the requested USB IDs."""
    setupapi, kernel32, winusb = _load_windows_apis()
    interface_guid = _guid(_USB_INTERFACE_GUID)
    info_set = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(interface_guid), None, None,
        _DIGCF_PRESENT | _DIGCF_DEVICEINTERFACE,
    )
    if not info_set or info_set == _INVALID_HANDLE_VALUE:
        raise _windows_error("SetupDiGetClassDevsW(USB device interfaces)")

    needle = f"VID_{vid:04X}&PID_{pid:04X}"
    failures: list[Exception] = []
    try:
        index = 0
        while True:
            interface_data = _SP_DEVICE_INTERFACE_DATA()
            interface_data.cbSize = ctypes.sizeof(_SP_DEVICE_INTERFACE_DATA)
            ctypes.set_last_error(0)
            ok = setupapi.SetupDiEnumDeviceInterfaces(
                info_set, None, ctypes.byref(interface_guid), index,
                ctypes.byref(interface_data),
            )
            if not ok:
                error = ctypes.get_last_error()
                if error == _ERROR_NO_MORE_ITEMS:
                    break
                raise _windows_error("SetupDiEnumDeviceInterfaces")
            index += 1

            path = _interface_path(info_set, interface_data, setupapi)
            if needle not in path.upper():
                continue
            try:
                return _open_path(path, kernel32, winusb)
            except (OSError, DisplayError) as exc:
                failures.append(exc)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(info_set)

    if failures:
        detail = "; ".join(str(exc) for exc in failures)
        if any(isinstance(exc, DisplayUnsupportedError) for exc in failures):
            raise DisplayUnsupportedError(detail)
        raise DisplayIOError(detail)
    raise DisplayNotFoundError(f"未找到 USB {vid:04X}:{pid:04X} 设备接口")


class USBDisplay:
    """WinUSB output for a single Thermalright LY display."""

    def __init__(self, vid: int = DEFAULT_VID, pid: int = DEFAULT_PID) -> None:
        self.vid = int(vid)
        self.pid = int(pid)
        self._backend: Any | None = None
        self._lock = threading.Lock()
        self._status: dict[str, Any] = {
            "connected": False,
            "state": "not_connected",
            "detail": "尚未连接",
            "device": "",
            "width": DEFAULT_WIDTH,
            "height": DEFAULT_HEIGHT,
        }

    def _set_status(self, state: str, detail: str, device: str = "",
                    width: int | None = None, height: int | None = None) -> dict[str, Any]:
        self._status = {
            "connected": state == "connected",
            "state": state,
            "detail": detail,
            "device": device,
            "width": DEFAULT_WIDTH if width is None else int(width),
            "height": DEFAULT_HEIGHT if height is None else int(height),
        }
        return dict(self._status)

    def _release_backend(self) -> None:
        backend = self._backend
        self._backend = None
        if backend is not None:
            try:
                backend.close()
            except Exception:
                pass

    def connect(self) -> dict[str, Any]:
        with self._lock:
            if self._backend is not None and self._status["connected"]:
                return dict(self._status)
            self._release_backend()
            if (self.vid, self.pid) != (DEFAULT_VID, DEFAULT_PID):
                return self._set_status(
                    "unsupported",
                    f"当前输出层仅实现 USB {DEFAULT_VID:04X}:{DEFAULT_PID:04X} 的 LY 协议",
                )
            path = ""
            try:
                backend = _open_winusb_device(self.vid, self.pid)
                self._backend = backend
                path = backend.path
                handshake = bytearray(2048)
                handshake[:16] = bytes((
                    0x02, 0xFF, 0, 0, 0, 0, 0, 0,
                    0x01, 0, 0, 0, 0, 0, 0, 0,
                ))
                backend.write(bytes(handshake), _HANDSHAKE_TIMEOUT_MS)
                response = backend.read(512, _HANDSHAKE_TIMEOUT_MS)
                if (len(response) < 37 or response[0] != 0x03
                        or response[1] != 0xFF or response[8] != 0x01):
                    raise DisplayIOError(
                        "LY 握手响应不符合预期："
                        f"长度={len(response)}，头={response[:9].hex(' ')}"
                    )

                raw_model = response[20]
                width = DEFAULT_WIDTH
                height = ALT_PANEL_HEIGHT if raw_model == 5 else DEFAULT_HEIGHT
                pm = 64 + (1 if raw_model <= 3 else raw_model)
                sub = response[22] + 1
                detail = f"WinUSB 已连接；LY 握手 PM={pm} SUB={sub}；{width}×{height}"
                return self._set_status("connected", detail, path, width, height)
            except DisplayNotFoundError as exc:
                self._release_backend()
                return self._set_status("not_found", str(exc), path)
            except DisplayUnsupportedError as exc:
                self._release_backend()
                return self._set_status("unsupported", str(exc), path)
            except Exception as exc:
                self._release_backend()
                return self._set_status("error", str(exc), path)

    def send_jpeg(self, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise TypeError("send_jpeg(data) 需要 bytes")
        if len(data) < 4 or data[:2] != b"\xff\xd8" or data[-2:] != b"\xff\xd9":
            raise ValueError("data 不是完整的 JPEG（缺少 SOI/EOI 标记）")
        if len(data) > MAX_JPEG_BYTES:
            raise ValueError(f"JPEG 为 {len(data)} 字节，超过设备的 {MAX_JPEG_BYTES} 字节上限")

        with self._lock:
            backend = self._backend
            if backend is None or not self._status["connected"]:
                raise DisplayIOError("屏幕尚未连接")
            try:
                frame = _build_ly_frame(data)
                for offset in range(0, len(frame), _USB_WRITE_SIZE):
                    chunk = frame[offset:offset + _USB_WRITE_SIZE]
                    backend.write(chunk, _WRITE_TIMEOUT_MS)
                # The current Rust/Python upstream senders perform the bounded
                # ACK read but do not validate its bytes.  Keep that behavior:
                # captures document the 03 FF 01 form, but other firmware
                # responses are not yet characterized well enough to reject.
                backend.read(512, _READ_TIMEOUT_MS)
            except Exception as exc:
                device = str(self._status.get("device") or "")
                self._release_backend()
                self._set_status("error", f"USB 帧传输失败：{exc}", device)
                if isinstance(exc, DisplayIOError):
                    raise
                raise DisplayIOError(f"USB 帧传输失败：{exc}") from exc

    def close(self) -> None:
        with self._lock:
            device = str(self._status.get("device") or "")
            width = int(self._status.get("width") or DEFAULT_WIDTH)
            height = int(self._status.get("height") or DEFAULT_HEIGHT)
            self._release_backend()
            self._set_status("closed", "USB 连接已关闭", device, width, height)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)
