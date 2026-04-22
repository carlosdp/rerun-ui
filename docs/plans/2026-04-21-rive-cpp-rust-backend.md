# Native Rive C++ Backend via Rust/PyO3 Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Replace the current Node-backed concrete Rive backend with a native backend built on the official Rive C++ runtime, exposed through a Rust wrapper and PyO3 bindings, while preserving the current high-level Python API (`create_rive_renderer`, `attach_rive_face`, `FaceSurface`, `AnimatedFaceHandle`).

**Architecture:** Keep the existing Python face-surface API stable, but move frame generation into the native extension module. Add a small C++ shim around the official Rive runtime that can load a `.riv`, select an artboard and state machine, set inputs, advance time, and render into an RGBA buffer. Wrap that shim from Rust with a narrow FFI boundary, then expose a PyO3-backed Python object that implements the `RiveFrameRenderer` protocol. Keep the current Node backend only as an explicit temporary fallback if needed during migration, but the native backend should become the default path.

**Tech Stack:** Rust (`pyo3`, likely `cc` + manual `extern "C"` FFI or `cxx` if justified), C++17 shim layer, official Rive C++ runtime sources, existing `maturin` build, Python tests, vendored `.riv` asset, local verification via `maturin develop` + `pytest`.

---

## Why this plan exists

The current branch `feat/rive-face-api` now ships a real backend, but it is **Node-backed** (`@rive-app/canvas-advanced` + `skia-canvas`). That solved the immediate need for a verifiable backend, but it is **not** the native architecture Carlos asked for.

This plan is a corrective/native follow-up with these goals:
- no Node subprocess in the steady-state path,
- no Node bootstrap requirement for the default backend,
- native backend owned by the existing Rust extension module.

---

## Target outcome

After this work:

1. `rerun_ui.create_rive_renderer(...)` returns a **native** renderer by default.
2. The native renderer is backed by the official Rive C++ runtime, not JavaScript.
3. The Python API surface remains stable for callers already using the face-surface API.
4. A local test proves that a real `.riv` file can be loaded, advanced, and rendered to RGBA through the native path.
5. The PR clearly states whether the Node path remains as a fallback or is fully removed.

---

## Non-goals

- No changes to the Rust viewer UI / `src/viewer.rs`.
- No custom Rerun visualizers.
- No change to the face-surface entity strategy.
- Do not optimize for zero-copy GPU interop in this PR.
- Do not rewrite the existing face-surface API.

---

## Key design requirements

### Preserve the Python API

The following public Python API should continue to exist:

```python
create_rive_renderer(...)
attach_rive_face(...)
FaceSurface
AnimatedFaceHandle
RiveFrameRenderer
```

### Prefer native backend as default

`create_rive_renderer(...)` should prefer the native implementation.

If a fallback is kept, it must be explicit, e.g.:

```python
create_rive_renderer(..., backend="native")
create_rive_renderer(..., backend="node")
```

If the fallback is retained temporarily, document that clearly and make `native` the default.

### Narrow FFI boundary

Do **not** expose the whole Rive runtime directly through Rust.

Use a very small C-style boundary with operations like:
- create
- destroy
- set_bool
- set_number
- fire_trigger
- advance
- render_rgba

This keeps Rust bindings maintainable.

---

## Proposed file layout

### Rust / native extension changes

**Modify:**
- `Cargo.toml`
- `build.rs`
- `src/lib.rs`

**Create:**
- `src/rive.rs`
- `src/rive_native.rs` or `src/rive_ffi.rs`
- `native/rive_renderer.h`
- `native/rive_renderer.cc`
- `native/include/` if helper headers are needed

### Python surface changes

**Modify:**
- `rerun_ui/_rive_face.py`
- `rerun_ui/__init__.py`
- `README.md`

### Tests / assets

**Modify:**
- `tests/test_rive_face.py`
- `tests/assets/README.md`

**Reuse if possible:**
- `tests/assets/state_machine_test.riv`

### Optional / fallback cleanup

If the native implementation succeeds fully, evaluate whether these should be retained or demoted to fallback-only:
- `rerun_ui/_rive_node/package.json`
- `rerun_ui/_rive_node/package-lock.json`
- `rerun_ui/_rive_node/renderer.mjs`

---

## C++/Rust boundary proposal

### C API exposed by the C++ shim

Create a deliberately tiny interface like:

```cpp
extern "C" {
  struct RiveRendererHandle;

  RiveRendererHandle* rive_renderer_create(
      const char* riv_path,
      const char* artboard,
      const char* state_machine,
      uint32_t width,
      uint32_t height,
      char** error_out);

  void rive_renderer_destroy(RiveRendererHandle* handle);

  bool rive_renderer_set_bool(
      RiveRendererHandle* handle,
      const char* input_name,
      bool value,
      char** error_out);

  bool rive_renderer_set_number(
      RiveRendererHandle* handle,
      const char* input_name,
      float value,
      char** error_out);

  bool rive_renderer_fire_trigger(
      RiveRendererHandle* handle,
      const char* input_name,
      char** error_out);

  bool rive_renderer_advance(
      RiveRendererHandle* handle,
      float dt_s,
      char** error_out);

  bool rive_renderer_render_rgba(
      RiveRendererHandle* handle,
      uint8_t* out_rgba,
      uint32_t out_len,
      char** error_out);

  void rive_renderer_free_error(char* error);
}
```

### Rust wrapper responsibilities

Rust should:
- own the handle lifetime safely,
- convert errors into `PyRuntimeError`,
- copy the RGBA buffer into Python bytes,
- expose a PyO3 class or functions that the Python layer can use.

### Python layer responsibilities

Python should:
- adapt the PyO3 native object to the existing `RiveFrameRenderer` protocol,
- keep `AnimatedFaceHandle` and `FaceSurface` unchanged or minimally changed,
- keep tests simple.

---

## Build strategy

### Preferred build strategy

Extend `build.rs` to:
1. compile the C++ shim with `cc` (or a justified equivalent),
2. link it into the Rust extension,
3. include vendored/offline-available Rive C++ runtime sources or a pinned submodule/subtree.

### Dependency strategy

One of these must be chosen deliberately:

#### Option A — Vendor/pin Rive C++ runtime into repo
**Preferred for reproducibility.**

- Pros: deterministic builds, CI-friendly.
- Cons: larger repo / vendoring work.

#### Option B — Fetch via submodule
- Pros: cleaner repo history.
- Cons: worse DX/CI unless carefully documented.

#### Option C — Build-time fetch
**Avoid if possible.**
- Pros: less committed code.
- Cons: brittle and non-reproducible.

The implementation attempt should strongly prefer **Option A** or **Option B**.

---

## Rendering backend spike requirements

Before committing to the full implementation, prove that the official C++ runtime can actually satisfy this repo’s need:

### Spike question 1
Can the chosen official Rive C++ runtime stack render a `.riv` into an RGBA buffer in a headless/local test environment?

### Spike question 2
Can inputs and state machine changes be driven programmatically from the C++ side?

### Spike question 3
Can that be packaged cleanly enough for `maturin develop` on this machine?

If the answer to any of these is “not yet”, document the blocker precisely instead of hand-waving.

---

## TDD tasks

## Task 1: Add a failing native-backend API test

**Objective:** Prove that the current branch does not yet provide a native-backed `create_rive_renderer` path.

**Files:**
- Modify: `tests/test_rive_face.py`

**Step 1: Write failing test**

Add a targeted test that expects:
- `create_rive_renderer(..., backend="native")` exists, or
- `create_rive_renderer(...)` defaults to native and exposes a way to inspect backend kind, or
- the native backend can render a real `.riv` through the extension module.

Use a real `.riv` asset already in `tests/assets/`.

**Step 2: Run test to verify failure**

```bash
cd /home/agents/.hermes/hermes-agent/rerun-ui
pytest tests/test_rive_face.py::test_create_rive_renderer_native_backend -q
```

Expected: FAIL.

---

## Task 2: Add the native build scaffolding

**Objective:** Prepare the Rust extension to compile and link a C++ renderer shim.

**Files:**
- Modify: `Cargo.toml`
- Modify: `build.rs`
- Modify: `src/lib.rs`
- Create: `src/rive.rs`
- Create: `src/rive_ffi.rs` (or equivalent)
- Create: `native/rive_renderer.h`
- Create: `native/rive_renderer.cc`

**Step 1: Implement minimal failing/native plumbing**

- Add required Rust deps.
- Add build-script compilation for the C++ shim.
- Expose a minimal PyO3 entrypoint that can be imported from Python.

**Step 2: Verify the extension builds**

```bash
cd /home/agents/.hermes/hermes-agent/rerun-ui
source ~/.cargo/env 2>/dev/null || true
maturin develop
```

Expected: build succeeds, or if it fails, stop and debug systematically before proceeding.

---

## Task 3: Implement minimal native renderer lifecycle

**Objective:** Get create/destroy/load working before inputs or rendering.

**Files:**
- Modify: native + Rust bridge files from Task 2
- Modify: `rerun_ui/_rive_face.py`

**Step 1: Implement minimal code**

Support:
- loading the `.riv`
- selecting artboard
- selecting state machine
- returning a usable renderer object to Python

**Step 2: Add/adjust focused test**

Test should verify native renderer creation succeeds against the real asset.

**Step 3: Run test**

```bash
cd /home/agents/.hermes/hermes-agent/rerun-ui
pytest tests/test_rive_face.py::test_create_rive_renderer_native_backend -q
```

Expected: PASS.

---

## Task 4: Implement inputs + advance + render to RGBA

**Objective:** Complete the native `RiveFrameRenderer` behavior.

**Files:**
- Modify: native C++ shim
- Modify: Rust wrapper
- Modify: `rerun_ui/_rive_face.py`
- Modify: `tests/test_rive_face.py`

**Step 1: Implement minimal code**

Support:
- `set_bool`
- `set_number`
- `fire_trigger`
- `advance`
- `render_rgba`

**Step 2: Add failing test first**

Test should verify:
- native renderer returns RGBA bytes of the requested size,
- after input changes + advance, output frame changes in a meaningful way.

**Step 3: Run failing test**

```bash
cd /home/agents/.hermes/hermes-agent/rerun-ui
pytest tests/test_rive_face.py::test_native_rive_renderer_renders_real_frames -q
```

Expected: FAIL initially.

**Step 4: Implement and rerun**

Expected: PASS.

---

## Task 5: Wire native backend into public Python API

**Objective:** Make the native backend the default `create_rive_renderer` path.

**Files:**
- Modify: `rerun_ui/_rive_face.py`
- Modify: `rerun_ui/__init__.py`
- Modify: `README.md`

**Step 1: Implement**

Recommended API:

```python
def create_rive_renderer(
    *,
    riv_path: str,
    artboard: str,
    state_machine: str,
    texture_size: tuple[int, int] = (256, 256),
    backend: str = "native",
) -> RiveFrameRenderer: ...
```

Behavior:
- `backend="native"` uses the Rust/PyO3 path.
- If a Node fallback is retained, `backend="node"` remains available.
- Default should be `native`.

**Step 2: Update README**

Document:
- native backend architecture,
- build/bootstrap steps,
- whether Node remains available as fallback,
- limitations.

---

## Task 6: Full local verification

**Objective:** Prove the branch works locally.

**Step 1: Build/install extension**

```bash
cd /home/agents/.hermes/hermes-agent/rerun-ui
source ~/.cargo/env 2>/dev/null || true
maturin develop
```

**Step 2: Run targeted tests**

```bash
cd /home/agents/.hermes/hermes-agent/rerun-ui
pytest tests/test_rive_face.py -q
pytest tests/test_face_surface.py tests/test_rive_face.py -q
```

**Step 3: Run full suite**

```bash
cd /home/agents/.hermes/hermes-agent/rerun-ui
pytest tests/ -q
```

Expected: PASS.

---

## Task 7: Commit, push, and open a separate PR

**Objective:** Publish this as a separate native-backend attempt.

**Files:**
- Git only

**Step 1: Commit**

Suggested commit message:

```bash
git add -A
git commit -m "feat: add native rive cpp renderer backend"
```

**Step 2: Push branch**

```bash
git push -u origin feat/rive-cpp-rust-backend
```

**Step 3: Open separate PR**

Suggested title:

```bash
feat: add native Rive C++ renderer backend
```

PR body must state clearly:
- this is the native C++ + Rust/PyO3 attempt,
- whether Node fallback remains,
- exact local verification commands,
- any blockers or missing packaging work.

---

## Definition of done

This attempt is complete when one of the following is true:

### Success path
- native backend builds,
- real `.riv` renders through the native path,
- tests pass locally,
- separate PR is open.

### Honest blocked path
If full success is not possible in this environment, the attempt is still acceptable only if it ends with:
- the corrective spec committed,
- the native branch containing the best-faith scaffolding/spike work,
- precise blocker documentation,
- no false claims that native rendering works when it does not.

---

## Critical rules

- Do not quietly fall back to Node while claiming native success.
- Do not keep `backend="native"` wired to Node.
- If native build fails, debug systematically before changing approach.
- If a fallback is retained, make it explicit and documented.
- Be honest in tests, docs, and PR body.
