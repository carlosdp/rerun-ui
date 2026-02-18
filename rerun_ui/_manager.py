from __future__ import annotations

import json
import logging
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from ._types import Key, ViewerStatus

LOGGER = logging.getLogger(__name__)

try:
    from . import _rerun_ui as _native
except Exception as native_import_error:  # pragma: no cover - exercised in integration env
    _native = None
    _NATIVE_IMPORT_ERROR = native_import_error
else:
    _NATIVE_IMPORT_ERROR = None


def _native_protocol_version() -> int:
    if _native is None:
        return 1
    return int(_native.protocol_version())


def _native_expected_rerun_major_minor() -> str:
    if _native is None:
        return "0.29"
    return str(_native.expected_rerun_major_minor())


def _parse_major_minor(version: str) -> tuple[int, int] | None:
    match = re.match(r"\s*(\d+)\.(\d+)", version)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


@dataclass(frozen=True)
class _ButtonSpec:
    id: str
    label: str


class _ViewerManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()

        self._status = ViewerStatus.DISCONNECTED
        self._grpc_port = 9876
        self._control_port = 9877
        self._launch_timeout_s = 8.0

        self._proc: subprocess.Popen[Any] | None = None
        self._owns_process = False

        self._sock: socket.socket | None = None
        self._connection_id = 0

        self._event_queue: queue.Queue[tuple[int, dict[str, Any]]] = queue.Queue()
        self._dispatcher_stop = threading.Event()
        self._dispatcher_thread = threading.Thread(
            target=self._dispatcher_loop,
            name="rerun-ui-dispatcher",
            daemon=True,
        )
        self._dispatcher_thread.start()

        self._reader_thread: threading.Thread | None = None

        self._buttons: list[_ButtonSpec] = []
        self._button_callbacks: dict[str, Callable[[], None]] = {}
        self._keyboard_callback: Callable[[list[Key]], None] | None = None
        self._keyboard_poll_hz: float = 30.0

        self._sdk_target: str | None = None
        self._version_checked = False
        self._auto_recover_enabled = False

    def spawn_viewer(
        self,
        grpc_port: int = 9876,
        control_port: int = 9877,
        connect_sdk: bool = True,
        launch_timeout_s: float = 8.0,
    ) -> ViewerStatus:
        self._ensure_version_compat()

        with self._lock:
            self._grpc_port = int(grpc_port)
            self._control_port = int(control_port)
            self._launch_timeout_s = float(launch_timeout_s)
            self._auto_recover_enabled = True

            already_custom = self._status == ViewerStatus.CUSTOM_CONNECTED and self._sock is not None

        if not already_custom:
            self._recover_or_spawn(launch_timeout_s)

        if connect_sdk:
            self._connect_sdk()

        with self._lock:
            return self._status

    def add_button(self, label: str, callback: Callable[[], None]) -> str:
        if not callable(callback):
            raise TypeError("callback must be callable")

        button_id = uuid.uuid4().hex
        with self._lock:
            self._buttons.append(_ButtonSpec(id=button_id, label=str(label)))
            self._button_callbacks[button_id] = callback

        self._recover_if_needed()

        with self._lock:
            if self._status == ViewerStatus.CUSTOM_CONNECTED:
                self._sync_buttons_locked()

        return button_id

    def handle_keyboard_input(
        self,
        callback: Callable[[list[Key]], None],
        poll_hz: float = 30.0,
    ) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable")

        with self._lock:
            self._keyboard_callback = callback
            self._keyboard_poll_hz = max(1.0, float(poll_hz))

        self._recover_if_needed()

        with self._lock:
            if self._status == ViewerStatus.CUSTOM_CONNECTED:
                self._sync_keyboard_locked()

    def is_custom_ui_available(self) -> bool:
        self._recover_if_needed()
        with self._lock:
            return self._status == ViewerStatus.CUSTOM_CONNECTED

    def disconnect(self) -> None:
        try:
            import rerun as rr

            rr.disconnect()
        except Exception:
            LOGGER.debug("Failed to disconnect rerun SDK", exc_info=True)

        with self._lock:
            self._close_socket_locked()
            self._status = ViewerStatus.DISCONNECTED
            self._connection_id += 1

            if self._owns_process and self._proc is not None:
                if self._proc.poll() is None:
                    self._proc.terminate()
                    try:
                        self._proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        self._proc.kill()
                self._proc = None
                self._owns_process = False

            self._sdk_target = None

    def _recover_if_needed(self) -> None:
        with self._lock:
            should_recover = self._auto_recover_enabled and self._status == ViewerStatus.DISCONNECTED
            timeout = self._launch_timeout_s
            should_reconnect_sdk = self._sdk_target is not None

        if should_recover:
            try:
                self._recover_or_spawn(timeout)
                if should_reconnect_sdk:
                    self._connect_sdk(force=True)
            except Exception:
                LOGGER.warning("rerun_ui failed to recover viewer on demand", exc_info=True)

    def _recover_or_spawn(self, launch_timeout_s: float) -> None:
        if self._try_attach_custom():
            return

        if self._is_port_open(self._grpc_port):
            with self._lock:
                self._status = ViewerStatus.PLAIN_CONNECTED
            return

        self._ensure_native_available()
        self._spawn_subprocess_if_needed()

        deadline = time.monotonic() + max(0.5, float(launch_timeout_s))
        while time.monotonic() < deadline:
            if self._try_attach_custom():
                return

            with self._lock:
                if self._proc is not None and self._proc.poll() is not None:
                    raise RuntimeError(
                        f"Custom viewer process exited early with code {self._proc.returncode}",
                    )

            time.sleep(0.1)

        if self._is_port_open(self._grpc_port):
            with self._lock:
                self._status = ViewerStatus.PLAIN_CONNECTED
            return

        raise RuntimeError("Timed out waiting for custom viewer to start")

    def _try_attach_custom(self) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)

        try:
            sock.connect(("127.0.0.1", self._control_port))
            self._send_json_line(
                sock,
                {
                    "type": "hello",
                    "protocol_version": _native_protocol_version(),
                    "client_name": "rerun_ui_python",
                },
            )
            ack = self._recv_json_line(sock, timeout_s=2.0)

            if ack.get("type") != "hello_ack" or not ack.get("custom_ui", False):
                return False

            sock.settimeout(None)
            with self._lock:
                self._close_socket_locked()
                self._sock = sock
                self._connection_id += 1
                connection_id = self._connection_id
                self._status = ViewerStatus.CUSTOM_CONNECTED

            self._start_reader_thread(sock, connection_id)

            with self._lock:
                self._sync_state_locked()

            return True
        except OSError:
            return False
        except Exception:
            LOGGER.debug("rerun_ui custom attach failed", exc_info=True)
            return False
        finally:
            with self._lock:
                if self._sock is not sock:
                    try:
                        sock.close()
                    except OSError:
                        pass

    def _start_reader_thread(self, sock: socket.socket, connection_id: int) -> None:
        reader = threading.Thread(
            target=self._reader_loop,
            args=(sock, connection_id),
            name=f"rerun-ui-reader-{connection_id}",
            daemon=True,
        )
        reader.start()
        with self._lock:
            self._reader_thread = reader

    def _reader_loop(self, sock: socket.socket, connection_id: int) -> None:
        try:
            with sock.makefile("r", encoding="utf-8", newline="\n") as reader:
                for line in reader:
                    payload = line.strip()
                    if not payload:
                        continue
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        LOGGER.warning("rerun_ui dropped invalid event payload: %s", payload)
                        continue
                    self._event_queue.put((connection_id, event))
        except OSError:
            LOGGER.debug("rerun_ui reader disconnected", exc_info=True)
        finally:
            self._event_queue.put((connection_id, {"type": "_disconnected"}))

    def _dispatcher_loop(self) -> None:
        while not self._dispatcher_stop.is_set():
            try:
                connection_id, event = self._event_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            event_type = event.get("type")
            if event_type == "_disconnected":
                self._handle_disconnected(connection_id)
                continue

            with self._lock:
                if connection_id != self._connection_id:
                    continue

            self._dispatch_event(event)

    def _dispatch_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")

        if event_type == "button_clicked":
            button_id = str(event.get("button_id", ""))
            with self._lock:
                callback = self._button_callbacks.get(button_id)

            if callback is not None:
                try:
                    callback()
                except Exception:
                    LOGGER.exception("rerun_ui button callback failed")

        elif event_type == "keyboard_state":
            raw_keys = event.get("pressed_keys", [])
            if not isinstance(raw_keys, list):
                return

            with self._lock:
                callback = self._keyboard_callback

            if callback is not None:
                keys: list[Key] = []
                for raw in raw_keys:
                    if not isinstance(raw, str):
                        continue
                    try:
                        keys.append(Key(raw))
                    except ValueError:
                        continue

                try:
                    callback(keys)
                except Exception:
                    LOGGER.exception("rerun_ui keyboard callback failed")

    def _handle_disconnected(self, connection_id: int) -> None:
        with self._lock:
            if connection_id != self._connection_id:
                return

            self._close_socket_locked()
            self._status = (
                ViewerStatus.PLAIN_CONNECTED if self._is_port_open(self._grpc_port) else ViewerStatus.DISCONNECTED
            )

    def _sync_state_locked(self) -> None:
        self._sync_buttons_locked()
        self._sync_keyboard_locked()

    def _sync_buttons_locked(self) -> None:
        self._send_command_locked(
            {
                "type": "set_buttons",
                "buttons": [{"id": item.id, "label": item.label} for item in self._buttons],
            }
        )

    def _sync_keyboard_locked(self) -> None:
        self._send_command_locked(
            {
                "type": "set_keyboard_config",
                "enabled": self._keyboard_callback is not None,
                "poll_hz": float(self._keyboard_poll_hz),
            }
        )

    def _send_command_locked(self, command: dict[str, Any]) -> bool:
        if self._sock is None or self._status != ViewerStatus.CUSTOM_CONNECTED:
            return False

        try:
            self._send_json_line(self._sock, command)
            return True
        except OSError:
            self._status = ViewerStatus.DISCONNECTED
            self._close_socket_locked()
            return False

    def _spawn_subprocess_if_needed(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return

            args = [
                sys.executable,
                "-m",
                "rerun_ui._viewer_host",
                "--grpc-port",
                str(self._grpc_port),
                "--control-port",
                str(self._control_port),
            ]
            self._proc = subprocess.Popen(args)
            self._owns_process = True

    def _connect_sdk(self, force: bool = False) -> None:
        target = f"rerun+http://127.0.0.1:{self._grpc_port}/proxy"
        with self._lock:
            if not force and self._sdk_target == target:
                return

        import rerun as rr

        rr.connect_grpc(target)

        with self._lock:
            self._sdk_target = target

    def _ensure_native_available(self) -> None:
        if _native is None:
            raise RuntimeError(
                "rerun_ui native extension is not available; install with maturin before spawning viewer"
            ) from _NATIVE_IMPORT_ERROR

    def _ensure_version_compat(self) -> None:
        with self._lock:
            if self._version_checked:
                return

        expected_text = _native_expected_rerun_major_minor()
        expected = _parse_major_minor(expected_text)
        if expected is None:
            raise RuntimeError(f"Invalid expected rerun version from native extension: {expected_text}")

        try:
            import rerun as rr
        except Exception as exc:  # pragma: no cover - runtime dependency
            raise RuntimeError("rerun package is required for rerun_ui") from exc

        actual = _parse_major_minor(getattr(rr, "__version__", ""))
        if actual is None:
            raise RuntimeError(f"Unable to parse rerun.__version__: {getattr(rr, '__version__', '')!r}")

        if actual != expected:
            raise RuntimeError(
                "rerun_ui version mismatch: expected rerun "
                f"{expected[0]}.{expected[1]}, found {actual[0]}.{actual[1]}. "
                "Install matching rerun and rerun_ui builds."
            )

        with self._lock:
            self._version_checked = True

    def _close_socket_locked(self) -> None:
        sock = self._sock
        self._sock = None

        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    @staticmethod
    def _send_json_line(sock: socket.socket, payload: dict[str, Any]) -> None:
        message = json.dumps(payload, separators=(",", ":")) + "\n"
        sock.sendall(message.encode("utf-8"))

    @staticmethod
    def _recv_json_line(sock: socket.socket, timeout_s: float) -> dict[str, Any]:
        sock.settimeout(timeout_s)
        data = bytearray()

        while b"\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                raise OSError("socket closed before receiving JSON line")
            data.extend(chunk)

        line, _, _rest = data.partition(b"\n")
        decoded = line.decode("utf-8")
        parsed = json.loads(decoded)
        if not isinstance(parsed, dict):
            raise ValueError("Expected JSON object message")
        return parsed

    @staticmethod
    def _is_port_open(port: int) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.2)
        try:
            return sock.connect_ex(("127.0.0.1", int(port))) == 0
        finally:
            sock.close()


_MANAGER = _ViewerManager()


def spawn_viewer(
    grpc_port: int = 9876,
    control_port: int = 9877,
    connect_sdk: bool = True,
    launch_timeout_s: float = 8.0,
) -> ViewerStatus:
    return _MANAGER.spawn_viewer(
        grpc_port=grpc_port,
        control_port=control_port,
        connect_sdk=connect_sdk,
        launch_timeout_s=launch_timeout_s,
    )


def add_button(label: str, callback: Callable[[], None]) -> str:
    return _MANAGER.add_button(label, callback)


def handle_keyboard_input(callback: Callable[[list[Key]], None], poll_hz: float = 30.0) -> None:
    _MANAGER.handle_keyboard_input(callback=callback, poll_hz=poll_hz)


def is_custom_ui_available() -> bool:
    return _MANAGER.is_custom_ui_available()


def disconnect() -> None:
    _MANAGER.disconnect()
