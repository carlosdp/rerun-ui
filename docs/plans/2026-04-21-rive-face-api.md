# Rive Face Surface API Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add a public `rerun_ui` API that can attach a live animated face surface to robot geometry in the existing Rerun 3D viewer, with a Rive-oriented adapter layer and local tests that verify the logging/update pipeline works.

**Architecture:** Keep the implementation Python-first and reuse the existing Rerun SDK connection managed by `rerun_ui._manager` instead of modifying the native Rust viewer. Model the problem as a live face surface attached to a dedicated face patch entity in the scene; the face surface accepts RGBA frames over time and logs them into the existing 3D scene as either a textured quad or a small UV-mapped mesh patch. Build the Rive-facing API around a pluggable frame-renderer backend so the viewer integration is stable even if the Rive renderer implementation evolves.

**Tech Stack:** Python 3.10+, existing `rerun-sdk` Python dependency, `dataclasses`, `threading`, `typing.Protocol`, unit tests with `pytest`/`unittest.mock`, optional backend abstraction for Rive frame rendering.

---

## Context and scope

This repo already provides a Python API layer that manages a custom Rerun viewer process and calls `rr.connect_grpc(...)` through `rerun_ui._manager`. The 3D viewer rendering itself already comes from Rerun, so the smallest maintainable implementation is to log a face patch entity into the scene rather than trying to add a custom native renderer in `src/viewer.rs` or mutate materials inside a monolithic `Asset3D`.

### In-scope for this PR

- Public Python API for defining a robot face target surface.
- Public Python API for pushing live RGBA textures into that surface.
- Public Rive-oriented API layer for driving that surface from an animation backend.
- Tests that verify entity logging/update behavior and lifecycle.
- README/API docs update.
- A runnable local verification command proving the Python-side API works.

### Prefer in this PR

- A dedicated **face patch entity** in scene space.
- A **Python-only implementation** that does not require Rust changes.
- Testable behavior via mocked/stubbed Rerun logging.

### Avoid in this PR

- Custom Rust visualizers or new native 3D view classes.
- IPC transport of raw animation frames through `src/ipc.rs`.
- Direct selective material mutation inside an imported `Asset3D`.
- Large platform-specific native Rive integration unless it is clearly feasible without destabilizing packaging.

---

## Acceptance criteria

1. `rerun_ui` exports a documented public API for:
   - defining a 3D face quad target,
   - defining a 3D face patch mesh target,
   - creating a live face surface,
   - attaching a Rive-oriented animated face handle.
2. A caller can update the face surface with RGBA frames over time and the implementation logs the expected Rerun entities/components through the existing SDK connection.
3. The Rive-oriented API supports at least:
   - state advancement,
   - start/stop lifecycle,
   - a backend abstraction for frame generation,
   - input setters (`bool`, `number`, `trigger`) or a clearly equivalent API.
4. Unit tests cover the logging/update pipeline, lifecycle cleanup, and public API wiring.
5. Local verification commands run successfully in this repo.
6. The PR description clearly states whether the concrete Rive renderer backend is implemented directly in this PR or abstracted behind a backend/protocol with a tested fake backend.

---

## API design

### Public surface

Add the following public API from `rerun_ui.__init__.py`:

```python
FaceQuad
FacePatchMesh
FaceSurface
AnimatedFaceHandle
RiveFrameRenderer
create_face_surface
attach_rive_face
```

### Proposed data models

Create these in a new module, preferably `rerun_ui/_face_surface.py`:

```python
from dataclasses import dataclass
from typing import Sequence

@dataclass(frozen=True)
class FaceQuad:
    entity_path: str
    parent_entity: str
    center_xyz: tuple[float, float, float]
    u_axis_xyz: tuple[float, float, float]
    v_axis_xyz: tuple[float, float, float]
    size_m: tuple[float, float]
    z_offset_m: float = 0.001

@dataclass(frozen=True)
class FacePatchMesh:
    entity_path: str
    parent_entity: str
    vertices_xyz: Sequence[tuple[float, float, float]]
    triangle_indices: Sequence[tuple[int, int, int]]
    uvs: Sequence[tuple[float, float]]
```

### Proposed runtime objects

```python
class FaceSurface:
    def update_rgba(
        self,
        rgba: bytes | bytearray | memoryview,
        width: int,
        height: int,
        *,
        sim_time_s: float | None = None,
    ) -> None: ...

    def close(self) -> None: ...

class AnimatedFaceHandle:
    def advance(self, dt_s: float, *, sim_time_s: float | None = None) -> None: ...
    def start(self, fps: float = 30.0) -> None: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...
```

### Rive backend protocol

In a new module, preferably `rerun_ui/_rive_face.py`, define a backend protocol that isolates face-surface logging from Rive rendering specifics:

```python
from typing import Protocol

class RiveFrameRenderer(Protocol):
    def set_bool(self, input_name: str, value: bool) -> None: ...
    def set_number(self, input_name: str, value: float) -> None: ...
    def fire_trigger(self, input_name: str) -> None: ...
    def advance(self, dt_s: float) -> None: ...
    def render_rgba(self) -> tuple[bytes, int, int]: ...
    def close(self) -> None: ...
```

### Rive-oriented entrypoint

```python
def attach_rive_face(
    target: FaceQuad | FacePatchMesh,
    *,
    renderer: RiveFrameRenderer,
    recording=None,
) -> AnimatedFaceHandle: ...
```

### Optional convenience constructor

If a concrete renderer backend is feasible in this PR, also add:

```python
def create_rive_renderer(
    *,
    riv_path: str,
    artboard: str,
    state_machine: str,
    texture_size: tuple[int, int] = (256, 256),
) -> RiveFrameRenderer: ...
```

If that is **not** feasible without adding fragile packaging/tooling, do **not** fake it. Keep the backend protocol public and ship a tested fake backend only in tests/examples, while documenting the seam for a future native Rive backend.

---

## Rendering/data-flow requirements

### Face quad path

For `FaceQuad`, the implementation should:

1. ensure `spawn_viewer(..., connect_sdk=True)` semantics are compatible with logging,
2. log a spatial transform anchored under `parent_entity`,
3. log the face image/frame at `entity_path`,
4. keep updates incremental so repeated calls overwrite/update the same entity path instead of creating unbounded entity churn.

### Face patch mesh path

For `FacePatchMesh`, the implementation should:

1. log static mesh topology once,
2. log/update the texture payload on subsequent frame updates,
3. preserve a stable entity path.

### Animation lifecycle

The animated handle should:

- support manual advancement with `advance(dt_s)`,
- optionally support background ticking with `start(fps=...)`,
- stop cleanly on `close()`,
- avoid leaking threads.

---

## Files to modify

### Create

- `docs/plans/2026-04-21-rive-face-api.md`
- `rerun_ui/_face_surface.py`
- `rerun_ui/_rive_face.py`
- `tests/test_face_surface.py`
- `tests/test_rive_face.py`

### Modify

- `rerun_ui/__init__.py`
- `rerun_ui/_manager.py` (only if necessary for SDK/recording access helpers; prefer minimal change)
- `README.md`

### Only if absolutely necessary

- `rerun_ui/_types.py` (if shared types belong here)
- `pyproject.toml` (only if adding a safe, justified runtime dependency)

### Explicitly avoid unless required by evidence

- `src/viewer.rs`
- `src/ipc.rs`
- `vendor/re_view_spatial/**`

---

## Task 1: Add a failing API-level test for the face surface

**Objective:** Define the expected Python API and prove it does not exist yet or does not behave correctly yet.

**Files:**
- Create: `tests/test_face_surface.py`
- Modify: `rerun_ui/__init__.py` (later)
- Modify: `rerun_ui/_manager.py` (later, only if needed)

**Step 1: Write failing tests**

Cover at least:

- importing `FaceQuad`, `FacePatchMesh`, `create_face_surface` from `rerun_ui`,
- creating a face surface from a quad target,
- calling `update_rgba(...)`,
- asserting the expected logging calls were made against a mocked recording or mocked `rerun` SDK.

Example sketch:

```python
def test_face_surface_logs_quad_image_updates(monkeypatch):
    calls = []

    class FakeRecording:
        def log(self, entity_path, *components, **kwargs):
            calls.append((entity_path, components, kwargs))

    target = FaceQuad(
        entity_path="/robot/head/face",
        parent_entity="/robot/head",
        center_xyz=(0.0, 0.0, 0.0),
        u_axis_xyz=(1.0, 0.0, 0.0),
        v_axis_xyz=(0.0, 1.0, 0.0),
        size_m=(0.2, 0.1),
    )

    surface = create_face_surface(target, recording=FakeRecording())
    surface.update_rgba(bytes([255, 0, 0, 255] * 4), 2, 2)

    assert calls
```

**Step 2: Run test to verify failure**

Run:

```bash
cd /home/agents/.hermes/hermes-agent/rerun-ui
pytest tests/test_face_surface.py -q
```

Expected: FAIL because the new API is not implemented yet.

---

## Task 2: Implement the generic face-surface API

**Objective:** Add a Python implementation for stable face patch logging without changing the Rust viewer.

**Files:**
- Create: `rerun_ui/_face_surface.py`
- Modify: `rerun_ui/__init__.py`
- Modify: `rerun_ui/_manager.py` only if a helper for acquiring/ensuring the current recording is necessary

**Step 1: Implement minimal production code**

Requirements:

- Define `FaceQuad` and `FacePatchMesh` dataclasses.
- Implement `FaceSurface`.
- Add `create_face_surface(...)`.
- Validate basic input shape/length.
- Keep the implementation test-friendly by allowing an injected recording object.
- Prefer composition over hidden globals.

**Step 2: Re-run the focused test**

```bash
cd /home/agents/.hermes/hermes-agent/rerun-ui
pytest tests/test_face_surface.py -q
```

Expected: PASS.

**Step 3: Add edge-case tests**

Add cases for:
- invalid RGBA buffer length,
- invalid dimensions,
- closing the surface,
- multiple updates reusing the same entity path.

**Step 4: Run test file again**

```bash
cd /home/agents/.hermes/hermes-agent/rerun-ui
pytest tests/test_face_surface.py -q
```

Expected: PASS.

---

## Task 3: Add a failing test for the animated/Rive-oriented API

**Objective:** Define the behavior of the animation driver independently of any specific backend implementation.

**Files:**
- Create: `tests/test_rive_face.py`
- Create: `rerun_ui/_rive_face.py` (later)

**Step 1: Write failing tests**

Cover at least:

- `attach_rive_face(...)` returning a handle,
- manual `advance(dt_s)` producing a frame update,
- `start()/stop()/close()` lifecycle,
- input setter forwarding (`set_bool`, `set_number`, `fire_trigger`) if these methods are part of the handle.

Use a fake renderer backend in tests:

```python
class FakeRenderer:
    def __init__(self):
        self.advanced = []
        self.inputs = []
    def set_bool(self, name, value): ...
    def set_number(self, name, value): ...
    def fire_trigger(self, name): ...
    def advance(self, dt_s):
        self.advanced.append(dt_s)
    def render_rgba(self):
        return (bytes([0, 255, 0, 255] * 4), 2, 2)
    def close(self):
        pass
```

**Step 2: Run test to verify failure**

```bash
cd /home/agents/.hermes/hermes-agent/rerun-ui
pytest tests/test_rive_face.py -q
```

Expected: FAIL because the animation API does not exist yet.

---

## Task 4: Implement the animated face / Rive adapter layer

**Objective:** Add the animation driver and public Rive-oriented API.

**Files:**
- Create: `rerun_ui/_rive_face.py`
- Modify: `rerun_ui/__init__.py`
- Modify: `README.md`

**Step 1: Implement minimal production code**

Requirements:

- Define `RiveFrameRenderer` protocol.
- Implement `AnimatedFaceHandle`.
- Implement `attach_rive_face(...)`.
- Manual `advance(dt_s)` must call renderer advance + render + `FaceSurface.update_rgba(...)`.
- Background ticking must use a daemon thread plus a stop event.
- `close()` must stop the thread and close both renderer and surface safely.

**Step 2: Re-run focused tests**

```bash
cd /home/agents/.hermes/hermes-agent/rerun-ui
pytest tests/test_rive_face.py -q
```

Expected: PASS.

**Step 3: Add race/lifecycle tests**

At minimum cover:
- `start()` is idempotent,
- `stop()` before `start()` is safe,
- `close()` is idempotent.

**Step 4: Run the test file again**

```bash
cd /home/agents/.hermes/hermes-agent/rerun-ui
pytest tests/test_rive_face.py -q
```

Expected: PASS.

---

## Task 5: Export and document the API

**Objective:** Make the new API discoverable and documented.

**Files:**
- Modify: `rerun_ui/__init__.py`
- Modify: `README.md`

**Step 1: Export the new symbols**

Add imports + `__all__` entries for the public API.

**Step 2: Update README**

Document:
- the face surface concept,
- the quad vs mesh choice,
- a short animated face example using a fake/simple renderer backend,
- any limitations around concrete Rive backend packaging.

**Step 3: Run targeted tests**

```bash
cd /home/agents/.hermes/hermes-agent/rerun-ui
pytest tests/test_face_surface.py tests/test_rive_face.py -q
```

Expected: PASS.

---

## Task 6: Run full local verification

**Objective:** Verify the whole repo still passes locally.

**Files:**
- Test only

**Step 1: Run full suite**

```bash
cd /home/agents/.hermes/hermes-agent/rerun-ui
pytest tests/ -q
```

Expected: PASS.

**Step 2: Review diff**

```bash
cd /home/agents/.hermes/hermes-agent/rerun-ui
git diff --stat
git diff -- docs/plans/2026-04-21-rive-face-api.md rerun_ui/__init__.py rerun_ui/_face_surface.py rerun_ui/_rive_face.py tests/test_face_surface.py tests/test_rive_face.py README.md
```

**Step 3: Commit**

Use a conventional message such as:

```bash
cd /home/agents/.hermes/hermes-agent/rerun-ui
git add docs/plans/2026-04-21-rive-face-api.md rerun_ui/__init__.py rerun_ui/_face_surface.py rerun_ui/_rive_face.py tests/test_face_surface.py tests/test_rive_face.py README.md
git commit -m "feat: add animated face surface API"
```

---

## Task 7: Push and open a PR

**Objective:** Publish the implementation branch and open a PR against `main`.

**Files:**
- GitHub only

**Step 1: Push branch**

```bash
cd /home/agents/.hermes/hermes-agent/rerun-ui
git push -u origin feat/rive-face-api
```

**Step 2: Open PR**

Use `gh pr create` with a summary that explicitly states:
- Python-first implementation
- no Rust viewer changes
- face patch entity approach
- whether concrete Rive rendering backend is included or deferred behind protocol
- test commands run locally

Suggested title:

```bash
feat: add animated face surface API for 3D robot faces
```

Suggested body should include:
- Summary
- API added
- Local verification commands
- Known limitations / follow-ups

---

## Non-negotiable implementation constraints

- Do not claim a concrete built-in Rive backend exists unless it is actually implemented and tested in this PR.
- If a concrete Rive renderer backend is not feasible, ship the stable API seam and make that explicit in docs and PR text.
- Do not touch the native Rust viewer unless the Python-only path is proven insufficient.
- Keep the implementation small and maintainable.
- Tests must not require a live viewer process; use mocks/fakes.

---

## Definition of done

This work is complete when:

- the spec file is committed,
- the new public API is implemented,
- targeted tests pass,
- the full local test suite passes,
- the branch is pushed,
- a GitHub PR is open,
- the PR body accurately describes the delivered scope.
