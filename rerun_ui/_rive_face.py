from __future__ import annotations

import logging
import math
import threading
import time
from typing import Protocol

from ._face_surface import FacePatchMesh, FaceQuad, FaceSurface, create_face_surface

LOGGER = logging.getLogger(__name__)


class RiveFrameRenderer(Protocol):
    def set_bool(self, input_name: str, value: bool) -> None: ...

    def set_number(self, input_name: str, value: float) -> None: ...

    def fire_trigger(self, input_name: str) -> None: ...

    def advance(self, dt_s: float) -> None: ...

    def render_rgba(self) -> tuple[bytes, int, int]: ...

    def close(self) -> None: ...


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


def attach_rive_face(
    target: FaceQuad | FacePatchMesh,
    *,
    renderer: RiveFrameRenderer,
    recording=None,
) -> AnimatedFaceHandle:
    surface = create_face_surface(target, recording=recording)
    return AnimatedFaceHandle(surface=surface, renderer=renderer)
