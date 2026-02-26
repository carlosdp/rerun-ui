#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import threading
import time
from collections import deque

import rerun as rr
import rerun_ui
from rerun_ui import Pointer3DEvent


def _format_event(event: Pointer3DEvent) -> str:
    projected = (
        "none"
        if event.projected_position is None
        else f"({event.projected_position[0]:.3f}, {event.projected_position[1]:.3f}, {event.projected_position[2]:.3f})"
    )
    drag_delta = (
        ""
        if event.drag_delta is None
        else f" drag=({event.drag_delta[0]:.2f}, {event.drag_delta[1]:.2f})"
    )
    return (
        f"{event.event_type.value:<7} {event.button.value:<9} "
        f"view=({event.pointer_view[0]:.1f}, {event.pointer_view[1]:.1f}) "
        f"hit={projected}{drag_delta}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manual test for rerun_ui 3D pointer capture (Cmd + click/drag)"
    )
    parser.add_argument("--grpc-port", type=int, default=19886)
    parser.add_argument("--control-port", type=int, default=19887)
    parser.add_argument(
        "--duration-s",
        type=float,
        default=0.0,
        help="Optional duration in seconds (0 = run until Ctrl+C)",
    )
    args = parser.parse_args()

    rr.init("rerun_ui_pointer_capture_3d", spawn=False)
    status = rerun_ui.spawn_viewer(
        grpc_port=args.grpc_port,
        control_port=args.control_port,
        connect_sdk=True,
        launch_timeout_s=8.0,
    )
    print(f"Viewer status: {status} (grpc={args.grpc_port}, control={args.control_port})")
    if not rerun_ui.is_custom_ui_available():
        print(
            "WARNING: custom UI is not available; pointer callbacks require `custom_connected` status."
        )

    print("Test flow:")
    print("1) Drag without Cmd: camera should orbit/pan as normal.")
    print("2) Hold Cmd and drag: camera should stay still, and [pointer] drag lines should print.")
    print("3) Hold Cmd and click: [pointer] click should print.")

    state_lock = threading.Lock()
    counts = {"press": 0, "release": 0, "click": 0, "drag": 0}
    recent_events: deque[str] = deque(maxlen=8)
    total_drag = 0.0
    status_dirty = True

    def on_pointer(event: Pointer3DEvent) -> None:
        nonlocal status_dirty, total_drag
        line = _format_event(event)
        print("[pointer]", line)
        with state_lock:
            counts[event.event_type.value] += 1
            if event.drag_delta is not None:
                total_drag += math.hypot(event.drag_delta[0], event.drag_delta[1])
            recent_events.appendleft(line)
            status_dirty = True

    rerun_ui.handle_3d_view_press(on_pointer)
    rerun_ui.handle_3d_view_release(on_pointer)
    rerun_ui.handle_3d_view_click(on_pointer)
    rerun_ui.handle_3d_view_drag(on_pointer)

    start = time.monotonic()

    try:
        while True:
            elapsed = time.monotonic() - start
            if args.duration_s > 0 and elapsed >= args.duration_s:
                break

            theta = elapsed * 0.9
            points = []
            colors = []
            for i in range(64):
                t = theta + i * 0.18
                x = math.cos(t)
                y = math.sin(t)
                z = 0.5 * math.sin(t * 0.7)
                points.append([x, y, z])
                colors.append([64, 180 + (i % 75), 255 - (i % 120)])
            rr.log("pointer_scene/points", rr.Points3D(points, colors=colors, radii=0.02))

            with state_lock:
                needs_publish = status_dirty
                if needs_publish:
                    status_dirty = False
                    status_text = (
                        "Cmd+drag should NOT orbit camera when callbacks are active\n"
                        f"counts: press={counts['press']} release={counts['release']} click={counts['click']} drag={counts['drag']}\n"
                        f"total_drag_pixels={total_drag:.1f}\n"
                        "recent:\n"
                        + ("\n".join(recent_events) if recent_events else "  <none>")
                    )

            if needs_publish:
                rr.log("pointer_scene/status", rr.TextDocument(status_text))

            time.sleep(0.05)

    except KeyboardInterrupt:
        pass
    finally:
        rerun_ui.disconnect()

    print("Pointer capture example finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
