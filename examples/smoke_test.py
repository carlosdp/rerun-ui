#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import threading
import time

import rerun as rr
import rerun_ui
from rerun_ui import Key


def main() -> int:
    parser = argparse.ArgumentParser(description="Full smoke test for rerun_ui custom viewer")
    # Use non-default ports so the smoke test reliably spawns the custom viewer
    # instead of attaching to an unrelated plain viewer on 9876.
    parser.add_argument("--grpc-port", type=int, default=19876)
    parser.add_argument("--control-port", type=int, default=19877)
    parser.add_argument("--hz", type=float, default=30.0, help="Logging rate")
    parser.add_argument(
        "--keyboard-poll-hz",
        type=float,
        default=30.0,
        help="Keyboard callback polling rate",
    )
    parser.add_argument(
        "--duration-s",
        type=float,
        default=0.0,
        help="Optional duration in seconds (0 = run until Ctrl+C)",
    )
    args = parser.parse_args()

    rr.init("rerun_ui_smoke_test", spawn=False)

    status = rerun_ui.spawn_viewer(
        grpc_port=args.grpc_port,
        control_port=args.control_port,
        connect_sdk=True,
        launch_timeout_s=8.0,
    )
    print(f"Viewer status: {status} (grpc={args.grpc_port}, control={args.control_port})")

    state_lock = threading.Lock()
    state = {
        "click_count": 0,
        "show_orange": False,
        "pressed_keys": [],
    }

    def on_toggle_color() -> None:
        with state_lock:
            state["click_count"] += 1
            state["show_orange"] = not state["show_orange"]
        print("[button] Toggle Color clicked")

    def on_reset() -> None:
        with state_lock:
            state["click_count"] = 0
            state["show_orange"] = False
        print("[button] Reset clicked")

    def on_keys(keys: list[Key]) -> None:
        with state_lock:
            state["pressed_keys"] = [key.value for key in keys]

    rerun_ui.add_button("Toggle Color", on_toggle_color)
    rerun_ui.add_button("Reset", on_reset)
    rerun_ui.handle_keyboard_input(on_keys, poll_hz=args.keyboard_poll_hz)

    if not rerun_ui.is_custom_ui_available():
        print(
            "Custom UI is not currently available (plain viewer attached). "
            "Close other viewers using these ports or pass --grpc-port/--control-port.",
        )

    start = time.monotonic()
    frame = 0

    try:
        while True:
            elapsed = time.monotonic() - start
            if args.duration_s > 0 and elapsed >= args.duration_s:
                break

            with state_lock:
                click_count = int(state["click_count"])
                show_orange = bool(state["show_orange"])
                pressed_keys = list(state["pressed_keys"])

            theta = elapsed * 1.5
            x = math.cos(theta)
            y = math.sin(theta)
            color = [255, 136, 0] if show_orange else [64, 192, 64]

            rr.set_time("frame", sequence=frame)
            rr.log(
                "smoke/orbiting_point",
                rr.Points2D([[x, y]], colors=[color], radii=[0.06]),
            )
            rr.log(
                "smoke/state",
                rr.TextLog(
                    f"click_count={click_count} keys={pressed_keys if pressed_keys else ['<none>']}"
                ),
            )

            frame += 1
            time.sleep(max(0.001, 1.0 / args.hz))

    except KeyboardInterrupt:
        pass
    finally:
        rerun_ui.shutdown_viewer()

    print("Smoke test finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
