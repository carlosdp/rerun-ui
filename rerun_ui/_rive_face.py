from __future__ import annotations

import base64
from collections import deque
import json
import logging
import math
from os import PathLike
from pathlib import Path
import shutil
import subprocess
import threading
import time
from typing import Any, Protocol

from ._face_surface import FacePatchMesh, FaceQuad, FaceSurface, create_face_surface

LOGGER = logging.getLogger(__name__)

try:
    from ._rerun_ui import NativeRiveRenderer as _NativeRiveRenderer
except ImportError:  # pragma: no cover - exercised only when the extension is unavailable
    _NativeRiveRenderer = None


class RiveFrameRenderer(Protocol):
    def set_bool(self, input_name: str, value: bool) -> None: ...

    def set_number(self, input_name: str, value: float) -> None: ...

    def fire_trigger(self, input_name: str) -> None: ...

    def advance(self, dt_s: float) -> None: ...

    def render_rgba(self) -> tuple[bytes, int, int]: ...

    def close(self) -> None: ...


class _NodeRiveRenderer:
    backend_kind = "node"

    def __init__(
        self,
        *,
        riv_path: str | PathLike[str],
        artboard: str,
        state_machine: str,
        texture_size: tuple[int, int] = (256, 256),
        node_executable: str = "node",
    ) -> None:
        self._lock = threading.RLock()
        self._closed = False
        self._stderr_tail: deque[str] = deque(maxlen=200)
        self._runtime_dir = _node_runtime_dir()
        self._node_executable = _resolve_node_executable(node_executable)
        self._riv_path = _validate_riv_path(riv_path)
        self._artboard = _validate_name(artboard, field_name="artboard")
        self._state_machine = _validate_name(state_machine, field_name="state_machine")
        self._width, self._height = _validate_texture_size(texture_size)

        _require_node_runtime_installed(self._runtime_dir)

        self._process = subprocess.Popen(
            [
                self._node_executable,
                str(self._runtime_dir / "renderer.mjs"),
                json.dumps(
                    {
                        "riv_path": str(self._riv_path),
                        "artboard": self._artboard,
                        "state_machine": self._state_machine,
                        "width": self._width,
                        "height": self._height,
                    }
                ),
            ],
            cwd=str(self._runtime_dir),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._stderr_thread = threading.Thread(
            target=self._collect_stderr,
            name="rerun-ui-rive-node-stderr",
            daemon=True,
        )
        self._stderr_thread.start()
        ready = self._read_response(context="start up")
        self._input_types = dict(ready.get("inputs", {}))

    def set_bool(self, input_name: str, value: bool) -> None:
        if not isinstance(value, bool):
            raise TypeError("value must be a bool")
        self._request({"op": "set_bool", "input_name": str(input_name), "value": value})

    def set_number(self, input_name: str, value: float) -> None:
        numeric_value = float(value)
        if not math.isfinite(numeric_value):
            raise ValueError("value must be finite")
        self._request({"op": "set_number", "input_name": str(input_name), "value": numeric_value})

    def fire_trigger(self, input_name: str) -> None:
        self._request({"op": "fire_trigger", "input_name": str(input_name)})

    def advance(self, dt_s: float) -> None:
        dt = float(dt_s)
        if not math.isfinite(dt) or dt < 0.0:
            raise ValueError("dt_s must be a finite, non-negative number")
        self._request({"op": "advance", "dt_s": dt})

    def render_rgba(self) -> tuple[bytes, int, int]:
        response = self._request({"op": "render"})
        width = int(response["width"])
        height = int(response["height"])
        rgba = base64.b64decode(response["rgba_b64"])
        expected_size = width * height * 4
        if len(rgba) != expected_size:
            raise RuntimeError(
                f"Rive renderer returned {len(rgba)} bytes for a {width}x{height} frame; expected {expected_size}"
            )
        return rgba, width, height

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            process = self._process

        try:
            if process.poll() is None and process.stdin is not None and process.stdout is not None:
                try:
                    self._write_request({"op": "close"})
                    self._read_response(context="close")
                except Exception:
                    LOGGER.debug("Failed to close Node Rive renderer cleanly", exc_info=True)
        finally:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except Exception:
                    pass
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)

            self._stderr_thread.join(timeout=1.0)

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._ensure_open()
            self._write_request(payload)
            return self._read_response(context=payload.get("op", "request"))

    def _write_request(self, payload: dict[str, Any]) -> None:
        process = self._process
        stdin = process.stdin
        if stdin is None:
            raise RuntimeError(self._process_error_message("Node Rive renderer stdin is unavailable"))

        try:
            stdin.write(f"{json.dumps(payload, separators=(',', ':'))}\n")
            stdin.flush()
        except BrokenPipeError as exc:
            raise RuntimeError(self._process_error_message("Node Rive renderer terminated unexpectedly")) from exc

    def _read_response(self, *, context: str) -> dict[str, Any]:
        process = self._process
        stdout = process.stdout
        if stdout is None:
            raise RuntimeError(self._process_error_message("Node Rive renderer stdout is unavailable"))

        line = stdout.readline()
        if not line:
            raise RuntimeError(self._process_error_message(f"Node Rive renderer failed to {context}"))

        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                self._process_error_message(f"Node Rive renderer returned invalid JSON while trying to {context}: {line!r}")
            ) from exc

        if not response.get("ok"):
            raise RuntimeError(response.get("error") or self._process_error_message(f"Node Rive renderer failed to {context}"))
        return response

    def _collect_stderr(self) -> None:
        stderr = self._process.stderr
        if stderr is None:
            return
        for line in stderr:
            message = line.rstrip()
            if message:
                self._stderr_tail.append(message)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Rive renderer is closed")

    def _process_error_message(self, prefix: str) -> str:
        exit_code = self._process.poll()
        stderr_tail = "\n".join(self._stderr_tail)
        if exit_code is not None:
            message = f"{prefix} (exit code {exit_code})"
        else:
            message = prefix
        if stderr_tail:
            message = f"{message}\nNode stderr tail:\n{stderr_tail}"
        return message

    def __del__(self) -> None:  # pragma: no cover - best-effort cleanup
        try:
            self.close()
        except Exception:
            pass


class AnimatedFaceHandle:
    def __init__(self, surface: FaceSurface, renderer: RiveFrameRenderer) -> None:
        self._surface = surface
        self._renderer = renderer
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False

    def set_bool(self, input_name: str, value: bool) -> None:
        with self._lock:
            self._ensure_open()
            self._renderer.set_bool(input_name, value)

    def set_number(self, input_name: str, value: float) -> None:
        with self._lock:
            self._ensure_open()
            self._renderer.set_number(input_name, value)

    def fire_trigger(self, input_name: str) -> None:
        with self._lock:
            self._ensure_open()
            self._renderer.fire_trigger(input_name)

    def advance(self, dt_s: float, *, sim_time_s: float | None = None) -> None:
        dt = float(dt_s)
        if not math.isfinite(dt) or dt < 0.0:
            raise ValueError("dt_s must be a finite, non-negative number")

        with self._lock:
            self._ensure_open()
            renderer = self._renderer
            surface = self._surface

        renderer.advance(dt)
        rgba, width, height = renderer.render_rgba()
        surface.update_rgba(rgba, width, height, sim_time_s=sim_time_s)

    def start(self, fps: float = 30.0) -> None:
        fps_value = float(fps)
        if not math.isfinite(fps_value) or fps_value <= 0.0:
            raise ValueError("fps must be a finite, positive number")

        with self._lock:
            self._ensure_open()
            if self._thread is not None and self._thread.is_alive():
                return

            self._stop_event.clear()
            interval_s = 1.0 / fps_value
            thread = threading.Thread(
                target=self._run_loop,
                args=(interval_s,),
                name="rerun-ui-animated-face",
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            self._stop_event.set()

        thread.join(timeout=2.0)

        with self._lock:
            if thread.is_alive():
                LOGGER.warning("Animated face background loop did not stop cleanly within timeout")
            elif self._thread is thread:
                self._thread = None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True

        self.stop()
        errors: list[Exception] = []
        try:
            self._surface.close()
        except Exception as exc:  # pragma: no cover - defensive error aggregation
            errors.append(exc)

        try:
            self._renderer.close()
        except Exception as exc:  # pragma: no cover - defensive error aggregation
            errors.append(exc)

        if errors:
            raise errors[0]

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("animated face handle is closed")

    def _run_loop(self, interval_s: float) -> None:
        next_tick = time.monotonic()
        while not self._stop_event.is_set():
            now = time.monotonic()
            sleep_s = next_tick - now
            if sleep_s > 0.0 and self._stop_event.wait(timeout=sleep_s):
                break

            try:
                self.advance(interval_s)
            except RuntimeError:
                if self._closed:
                    break
                LOGGER.exception("Animated face background loop stopped after runtime error")
                break
            except Exception:
                LOGGER.exception("Animated face background loop failed")
                break

            next_tick = max(next_tick + interval_s, time.monotonic())

        with self._lock:
            if self._thread is threading.current_thread():
                self._thread = None


def create_rive_renderer(
    *,
    riv_path: str | PathLike[str],
    artboard: str,
    state_machine: str,
    texture_size: tuple[int, int] = (256, 256),
    backend: str = "native",
    node_executable: str = "node",
) -> RiveFrameRenderer:
    normalized_backend = _normalize_backend_name(backend)
    if normalized_backend == "native":
        return _create_native_rive_renderer(
            riv_path=riv_path,
            artboard=artboard,
            state_machine=state_machine,
            texture_size=texture_size,
        )
    if normalized_backend == "node":
        return _NodeRiveRenderer(
            riv_path=riv_path,
            artboard=artboard,
            state_machine=state_machine,
            texture_size=texture_size,
            node_executable=node_executable,
        )
    raise ValueError(f"Unsupported Rive backend '{backend}'. Expected 'native' or 'node'.")


def _create_native_rive_renderer(
    *,
    riv_path: str | PathLike[str],
    artboard: str,
    state_machine: str,
    texture_size: tuple[int, int],
) -> RiveFrameRenderer:
    if _NativeRiveRenderer is None:
        raise RuntimeError(
            "The native Rive backend is unavailable because the rerun_ui native extension is not importable. "
            "Run `maturin develop` after initializing the pinned `third_party/rive-cpp` submodule."
        )

    validated_path = _validate_riv_path(riv_path)
    validated_artboard = _validate_name(artboard, field_name="artboard")
    validated_state_machine = _validate_name(state_machine, field_name="state_machine")
    width, height = _validate_texture_size(texture_size)
    return _NativeRiveRenderer(
        riv_path=str(validated_path),
        artboard=validated_artboard,
        state_machine=validated_state_machine,
        width=width,
        height=height,
    )


def attach_rive_face(
    target: FaceQuad | FacePatchMesh,
    *,
    renderer: RiveFrameRenderer,
    recording=None,
) -> AnimatedFaceHandle:
    surface = create_face_surface(target, recording=recording)
    return AnimatedFaceHandle(surface=surface, renderer=renderer)


def _normalize_backend_name(backend: str) -> str:
    if not isinstance(backend, str):
        raise TypeError("backend must be a string")
    normalized = backend.strip().lower()
    if not normalized:
        raise ValueError("backend must not be empty")
    return normalized


def _node_runtime_dir() -> Path:
    return Path(__file__).resolve().parent / "_rive_node"


def _resolve_node_executable(node_executable: str) -> str:
    executable = shutil.which(node_executable)
    if executable is None:
        raise RuntimeError(
            f"Node.js executable '{node_executable}' was not found. Install Node.js 24+ and ensure it is on PATH."
        )
    return executable


def _require_node_runtime_installed(runtime_dir: Path) -> None:
    required_paths = [
        runtime_dir / "renderer.mjs",
        runtime_dir / "package.json",
        runtime_dir / "node_modules" / "@rive-app" / "canvas-advanced" / "rive.wasm",
        runtime_dir / "node_modules" / "skia-canvas",
    ]
    if all(path.exists() for path in required_paths):
        return

    npm_executable = shutil.which("npm") or "npm"
    raise RuntimeError(
        "The bundled Node-based Rive runtime is not bootstrapped yet. "
        f"Run `{npm_executable} install --prefix {runtime_dir}` to install @rive-app/canvas-advanced and skia-canvas."
    )


def _validate_riv_path(riv_path: str | PathLike[str]) -> Path:
    path = Path(riv_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Rive asset not found: {path}")
    return path


def _validate_name(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _validate_texture_size(texture_size: tuple[int, int]) -> tuple[int, int]:
    if len(texture_size) != 2:
        raise ValueError("texture_size must contain exactly two values")
    width = int(texture_size[0])
    height = int(texture_size[1])
    if width <= 0 or height <= 0:
        raise ValueError("texture_size values must be positive")
    return width, height
