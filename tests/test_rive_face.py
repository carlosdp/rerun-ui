from __future__ import annotations

from pathlib import Path
import shutil
import threading
import time

import pytest
import rerun as rr

from rerun_ui import (
    AnimatedFaceHandle,
    FaceQuad,
    RiveFrameRenderer,
    attach_rive_face,
    create_rive_renderer,
)

ROOT = Path(__file__).resolve().parents[1]
TEST_RIV_PATH = ROOT / "tests" / "assets" / "state_machine_test.riv"
NODE_RIVE_RUNTIME_DIR = ROOT / "rerun_ui" / "_rive_node"


class RecordingSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.time_calls: list[tuple[str, dict[str, object]]] = []

    def log(self, entity_path: str, *components: object, **kwargs: object) -> None:
        self.calls.append((entity_path, components, kwargs))

    def set_time(self, timeline: str, **kwargs: object) -> None:
        self.time_calls.append((timeline, kwargs))


class FakeRenderer:
    def __init__(self) -> None:
        self.advanced: list[float] = []
        self.inputs: list[tuple[str, str, object]] = []
        self.closed = 0
        self.render_calls = 0
        self.advance_event = threading.Event()

    def set_bool(self, input_name: str, value: bool) -> None:
        self.inputs.append(("bool", input_name, value))

    def set_number(self, input_name: str, value: float) -> None:
        self.inputs.append(("number", input_name, value))

    def fire_trigger(self, input_name: str) -> None:
        self.inputs.append(("trigger", input_name, None))

    def advance(self, dt_s: float) -> None:
        self.advanced.append(dt_s)
        self.advance_event.set()

    def render_rgba(self) -> tuple[bytes, int, int]:
        self.render_calls += 1
        channel = self.render_calls % 255
        return (bytes([channel, 255, 0, 255] * 4), 2, 2)

    def close(self) -> None:
        self.closed += 1


def _component_kinds(components: tuple[object, ...]) -> list[str]:
    kinds: list[str] = []
    for component in components:
        descriptor = getattr(component, "component_descriptor", None)
        if callable(descriptor):
            kinds.append(str(descriptor()))
        else:
            kinds.append(type(component).__name__)
    return kinds


def _make_quad() -> FaceQuad:
    return FaceQuad(
        entity_path="/robot/head/animated_face",
        parent_entity="/robot/head",
        center_xyz=(0.0, 0.0, 0.0),
        u_axis_xyz=(1.0, 0.0, 0.0),
        v_axis_xyz=(0.0, 1.0, 0.0),
        size_m=(0.2, 0.1),
    )


def _node_rive_runtime_installed() -> bool:
    if shutil.which("node") is None:
        return False
    return (
        (NODE_RIVE_RUNTIME_DIR / "renderer.mjs").exists()
        and (NODE_RIVE_RUNTIME_DIR / "package.json").exists()
        and (NODE_RIVE_RUNTIME_DIR / "node_modules" / "@rive-app" / "canvas-advanced" / "rive.wasm").exists()
        and (NODE_RIVE_RUNTIME_DIR / "node_modules" / "skia-canvas").exists()
    )


@pytest.mark.skipif(
    not _node_rive_runtime_installed(),
    reason="requires `npm install --prefix rerun_ui/_rive_node` to bootstrap the Node Rive runtime",
)
def test_create_rive_renderer_renders_real_rive_frames() -> None:
    renderer = create_rive_renderer(
        riv_path=str(TEST_RIV_PATH),
        artboard="MyArtboard",
        state_machine="StateMachine",
        texture_size=(64, 64),
    )

    try:
        rgba_initial, width, height = renderer.render_rgba()
        assert width == 64
        assert height == 64
        assert len(rgba_initial) == width * height * 4
        assert any(rgba_initial)

        renderer.set_number("MyNum", 5.0)
        renderer.fire_trigger("MyTrig")
        renderer.advance(0.1)
        rgba_after_trigger, width_after_trigger, height_after_trigger = renderer.render_rgba()
        assert width_after_trigger == 64
        assert height_after_trigger == 64
        assert len(rgba_after_trigger) == len(rgba_initial)

        renderer.set_bool("MyBool", True)
        renderer.advance(0.1)
        rgba_after_bool, width_after_bool, height_after_bool = renderer.render_rgba()
        assert width_after_bool == 64
        assert height_after_bool == 64
        assert rgba_after_bool != rgba_initial
    finally:
        renderer.close()
        renderer.close()


def test_rive_face_public_api_exports() -> None:
    assert AnimatedFaceHandle is not None
    assert RiveFrameRenderer is not None
    assert attach_rive_face is not None
    assert create_rive_renderer is not None


def test_attach_rive_face_manual_advance_updates_surface() -> None:
    recording = RecordingSpy()
    renderer = FakeRenderer()

    handle = attach_rive_face(_make_quad(), renderer=renderer, recording=recording)

    assert isinstance(handle, AnimatedFaceHandle)

    handle.advance(0.25, sim_time_s=2.0)

    assert renderer.advanced == [0.25]
    assert renderer.render_calls == 1
    assert recording.time_calls == [("sim_time", {"duration": 2.0})]
    assert _component_kinds(recording.calls[-1][1]) == [
        "Mesh3D:albedo_texture_buffer",
        "Mesh3D:albedo_texture_format",
    ]


def test_attach_rive_face_forwards_input_setters() -> None:
    handle = attach_rive_face(_make_quad(), renderer=FakeRenderer(), recording=RecordingSpy())

    handle.set_bool("is_blinking", True)
    handle.set_number("look_x", 0.5)
    handle.fire_trigger("smile")

    assert handle._renderer.inputs == [
        ("bool", "is_blinking", True),
        ("number", "look_x", 0.5),
        ("trigger", "smile", None),
    ]



def test_start_stop_and_close_lifecycle_is_safe_and_idempotent() -> None:
    recording = RecordingSpy()
    renderer = FakeRenderer()
    handle = attach_rive_face(_make_quad(), renderer=renderer, recording=recording)

    handle.stop()
    handle.start(fps=120.0)
    assert renderer.advance_event.wait(timeout=1.0)

    first_thread = handle._thread
    handle.start(fps=120.0)
    assert handle._thread is first_thread

    handle.stop()
    assert handle._thread is None
    advanced_after_stop = len(renderer.advanced)
    time.sleep(0.05)
    assert len(renderer.advanced) == advanced_after_stop

    handle.close()
    handle.close()

    assert renderer.closed == 1
    clear_calls = [call for call in recording.calls if isinstance(call[1][0], rr.Clear)]
    assert len(clear_calls) == 1



def test_advance_after_close_raises() -> None:
    handle = attach_rive_face(_make_quad(), renderer=FakeRenderer(), recording=RecordingSpy())
    handle.close()

    with pytest.raises(RuntimeError, match="closed"):
        handle.advance(0.1)
