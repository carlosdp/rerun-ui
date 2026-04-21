# rerun-ui

`rerun-ui` provides a custom Rerun Viewer UI implemented in Rust and exposed as a Python package.

The package:
- runs the viewer in a separate process,
- adds custom controls (buttons + keyboard input),
- logs animated robot face surfaces into the existing 3D scene,
- reconnects on demand after crashes,
- reuses any existing viewer process already bound to the configured ports.

## Installation

This project is a mixed PyO3 + Python package built with `maturin`.

```bash
maturin develop
```

For wheels:

```bash
maturin build --release
```

## API

```python
import rerun_ui
from rerun_ui import Key, Pointer3DEvent, ViewerStatus


def on_keys(keys: list[Key]) -> None:
    print("pressed:", keys)


def on_click() -> None:
    print("clicked")


def on_3d_pointer(event: Pointer3DEvent) -> None:
    print(
        "3d click:",
        event.pointer_view,
        "ray:",
        event.ray_origin,
        event.ray_direction,
        "hit:",
        event.projected_position,
    )


status = rerun_ui.spawn_viewer()
print("viewer status:", status)

rerun_ui.handle_keyboard_input(on_keys, poll_hz=30.0)
rerun_ui.add_button("Some Cool Thing", on_click)
rerun_ui.handle_3d_view_click(on_3d_pointer)
```

### Public functions

- `spawn_viewer(grpc_port=9876, control_port=9877, connect_sdk=True, launch_timeout_s=8.0) -> ViewerStatus`
- `add_button(label, callback) -> str`
- `handle_keyboard_input(callback, poll_hz=30.0) -> None`
- `handle_3d_view_click(callback) -> None`
- `handle_3d_view_press(callback) -> None`
- `handle_3d_view_release(callback) -> None`
- `handle_3d_view_drag(callback) -> None`
- `is_custom_ui_available() -> bool`
- `disconnect() -> None`
- `shutdown_viewer() -> None`

### Animated face surface API

The face-surface API is Python-first and logs a dedicated face patch entity into the existing
Rerun 3D scene. It does **not** require Rust viewer changes.

Public types and helpers:

- `FaceQuad`
- `FacePatchMesh`
- `FaceSurface`
- `AnimatedFaceHandle`
- `RiveFrameRenderer`
- `create_face_surface(target, recording=None) -> FaceSurface`
- `attach_rive_face(target, renderer, recording=None) -> AnimatedFaceHandle`

Use `FaceQuad` when a flat face panel is enough. Use `FacePatchMesh` when the face surface should
follow custom or curved geometry with explicit UVs.

`FaceSurface` logs static mesh and transform data once, then updates the same entity path with fresh
RGBA texture payloads over time.

#### Example: animated face with a simple renderer backend

```python
from __future__ import annotations

import rerun_ui


class SolidColorFace:
    def __init__(self) -> None:
        self._smiling = False

    def set_bool(self, input_name: str, value: bool) -> None:
        if input_name == "smile":
            self._smiling = value

    def set_number(self, input_name: str, value: float) -> None:
        pass

    def fire_trigger(self, input_name: str) -> None:
        if input_name == "blink":
            self._smiling = not self._smiling

    def advance(self, dt_s: float) -> None:
        pass

    def render_rgba(self) -> tuple[bytes, int, int]:
        rgba = [0, 255, 0, 255] if self._smiling else [255, 255, 255, 255]
        return (bytes(rgba * 4), 2, 2)

    def close(self) -> None:
        pass


rerun_ui.spawn_viewer(connect_sdk=True)

target = rerun_ui.FaceQuad(
    entity_path="/robot/head/face",
    parent_entity="/robot/head",
    center_xyz=(0.0, 0.0, 0.0),
    u_axis_xyz=(1.0, 0.0, 0.0),
    v_axis_xyz=(0.0, 1.0, 0.0),
    size_m=(0.2, 0.1),
)

handle = rerun_ui.attach_rive_face(target, renderer=SolidColorFace())
handle.set_bool("smile", True)
handle.advance(1.0 / 30.0, sim_time_s=0.0)
handle.close()
```

#### Rive backend status

This package currently ships the public `RiveFrameRenderer` protocol and a tested animated face
pipeline, but **does not** ship a built-in native Rive renderer backend. The protocol is the seam
for a future concrete backend once packaging and tooling are settled.

### 3D pointer events

`handle_3d_view_*` listeners are fired only from spatial 3D views and provide:
- event kind (`press`, `release`, `click`, `drag`)
- mouse button (`primary`, `secondary`, `middle`)
- 2D pointer coordinates in UI points:
  - `pointer_ui`: absolute UI coordinates
  - `pointer_view`: coordinates relative to the 3D view rect
- 3D raycast projection:
  - `ray_origin`: world-space ray origin
  - `ray_direction`: world-space ray direction
  - `projected_position`: world-space hit point when a pick hit exists
- `view_id`, `space_origin` to identify the source view/space
- `drag_delta` for drag events (UI-point delta since previous frame)
- when one or more 3D pointer listeners are registered, holding `Cmd` in a 3D view routes click/drag
  interactions to these callbacks instead of the viewer camera/selection controls

## Runtime behavior

- `spawn_viewer` is idempotent.
- If a custom `rerun_ui` viewer is already running on `control_port`, the package attaches to it.
- If only a plain Rerun viewer is running on `grpc_port`, data connection is reused and custom controls remain inactive.
- If no viewer is running, `rerun_ui` spawns its own subprocess (`python -m rerun_ui._viewer_host`).
- `disconnect()` only detaches the Python client; it does not close the viewer process.
- `shutdown_viewer()` closes the viewer process if it was spawned by this Python process.
- If the custom viewer crashes, the next API call triggers reconnect/spawn logic (on-demand recovery).

## Protocol

A local JSON-lines IPC protocol is used on `127.0.0.1:<control_port>`.

Commands:
- `hello`
- `set_buttons`
- `set_keyboard_config`
- `set_pointer_config`
- `ping`

Events:
- `hello_ack`
- `button_clicked`
- `keyboard_state`
- `pointer_3d`
- `pong`

## Compatibility

`rerun_ui` enforces strict major/minor version matching against the installed Python `rerun` SDK.
If versions differ, API calls fail fast with an actionable error message.

## Smoke test example

A full smoke test script is included at:

- `examples/smoke_test.py`

Run it after `maturin develop`:

```bash
python examples/smoke_test.py
```

Optional runtime limit:

```bash
python examples/smoke_test.py --duration-s 30
```

## Wheel publishing branch

GitHub Actions workflow:

- `/Users/carlosdp/code/rerun-ui/.github/workflows/build-wheels.yml`

What it does:

- builds abi3 wheels for Linux (`x86_64`) and macOS (`x86_64`, `arm64`)
- publishes only wheel/index artifacts to the `wheels` branch (orphan history)
- writes a simple index under `simple/rerun-ui/index.html`

Consumer usage (automatic platform wheel selection):

```bash
pip install \
  --find-links https://raw.githubusercontent.com/<owner>/<repo>/wheels/simple/rerun-ui/index.html \
  rerun-ui
```
