# rerun-ui

`rerun-ui` provides a custom Rerun Viewer UI implemented in Rust and exposed as a Python package.

The package:
- runs the viewer in a separate process,
- adds custom controls (buttons + keyboard input),
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
from rerun_ui import Key, ViewerStatus


def on_keys(keys: list[Key]) -> None:
    print("pressed:", keys)


def on_click() -> None:
    print("clicked")


status = rerun_ui.spawn_viewer()
print("viewer status:", status)

rerun_ui.handle_keyboard_input(on_keys, poll_hz=30.0)
rerun_ui.add_button("Some Cool Thing", on_click)
```

### Public functions

- `spawn_viewer(grpc_port=9876, control_port=9877, connect_sdk=True, launch_timeout_s=8.0) -> ViewerStatus`
- `add_button(label, callback) -> str`
- `handle_keyboard_input(callback, poll_hz=30.0) -> None`
- `is_custom_ui_available() -> bool`
- `disconnect() -> None`

## Runtime behavior

- `spawn_viewer` is idempotent.
- If a custom `rerun_ui` viewer is already running on `control_port`, the package attaches to it.
- If only a plain Rerun viewer is running on `grpc_port`, data connection is reused and custom controls remain inactive.
- If no viewer is running, `rerun_ui` spawns its own subprocess (`python -m rerun_ui._viewer_host`).
- If the custom viewer crashes, the next API call triggers reconnect/spawn logic (on-demand recovery).

## Protocol

A local JSON-lines IPC protocol is used on `127.0.0.1:<control_port>`.

Commands:
- `hello`
- `set_buttons`
- `set_keyboard_config`
- `ping`

Events:
- `hello_ack`
- `button_clicked`
- `keyboard_state`
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
