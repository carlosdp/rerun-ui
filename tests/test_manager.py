from __future__ import annotations

import types
import unittest
from unittest import mock

from rerun_ui._manager import _ViewerManager
from rerun_ui._types import PointerEventType, ViewerStatus


class ViewerManagerTest(unittest.TestCase):
    def test_spawn_viewer_is_idempotent_for_custom_connection(self) -> None:
        manager = _ViewerManager()

        def _mark_connected(_timeout: float) -> None:
            with manager._lock:
                manager._status = ViewerStatus.CUSTOM_CONNECTED
                manager._sock = mock.MagicMock()

        with mock.patch.object(manager, "_ensure_version_compat"), mock.patch.object(
            manager, "_recover_or_spawn", side_effect=_mark_connected
        ) as recover:
            status1 = manager.spawn_viewer(connect_sdk=False)
            status2 = manager.spawn_viewer(connect_sdk=False)

        self.assertEqual(status1, ViewerStatus.CUSTOM_CONNECTED)
        self.assertEqual(status2, ViewerStatus.CUSTOM_CONNECTED)
        self.assertEqual(recover.call_count, 1)

    def test_recover_uses_plain_existing_viewer_without_spawning(self) -> None:
        manager = _ViewerManager()

        with mock.patch.object(manager, "_try_attach_custom", return_value=False), mock.patch.object(
            manager, "_is_port_open", return_value=True
        ), mock.patch.object(manager, "_spawn_subprocess_if_needed") as spawn:
            manager._recover_or_spawn(launch_timeout_s=0.5)

        self.assertEqual(manager._status, ViewerStatus.PLAIN_CONNECTED)
        spawn.assert_not_called()

    def test_button_callback_dispatch(self) -> None:
        manager = _ViewerManager()
        clicked: list[str] = []

        manager._button_callbacks["b1"] = lambda: clicked.append("ok")
        manager._dispatch_event({"type": "button_clicked", "button_id": "b1"})

        self.assertEqual(clicked, ["ok"])

    def test_callback_exception_isolated(self) -> None:
        manager = _ViewerManager()
        called: list[str] = []

        def fail() -> None:
            raise RuntimeError("boom")

        manager._button_callbacks["bad"] = fail
        manager._button_callbacks["good"] = lambda: called.append("good")

        manager._dispatch_event({"type": "button_clicked", "button_id": "bad"})
        manager._dispatch_event({"type": "button_clicked", "button_id": "good"})

        self.assertEqual(called, ["good"])

    def test_pointer_click_callback_dispatch(self) -> None:
        manager = _ViewerManager()
        received: list[tuple[float, float]] = []

        manager.handle_3d_view_click(lambda event: received.append(event.pointer_view))
        manager._dispatch_event(
            {
                "type": "pointer_3d",
                "event_kind": "click",
                "button": "primary",
                "view_id": "ViewId(1234)",
                "space_origin": "/world",
                "pointer_ui": [100.0, 120.0],
                "pointer_view": [10.0, 20.0],
                "ray_origin": [1.0, 2.0, 3.0],
                "ray_direction": [0.0, 0.0, -1.0],
                "projected_position": [1.5, 2.5, 3.5],
                "drag_delta": None,
            }
        )

        self.assertEqual(received, [(10.0, 20.0)])

    def test_pointer_event_is_ignored_when_payload_invalid(self) -> None:
        manager = _ViewerManager()
        called: list[str] = []

        manager.handle_3d_view_click(lambda _event: called.append("ok"))
        manager._dispatch_event(
            {
                "type": "pointer_3d",
                "event_kind": "click",
                "button": "primary",
                "view_id": "ViewId(1234)",
                "space_origin": "/world",
                "pointer_ui": [100.0],
                "pointer_view": [10.0, 20.0],
                "ray_origin": [1.0, 2.0, 3.0],
                "ray_direction": [0.0, 0.0, -1.0],
                "projected_position": [1.5, 2.5, 3.5],
                "drag_delta": None,
            }
        )

        self.assertEqual(called, [])

    def test_sync_pointer_config_enabled(self) -> None:
        manager = _ViewerManager()
        manager._pointer_callbacks[PointerEventType.CLICK].append(lambda _event: None)

        with mock.patch.object(manager, "_send_command_locked") as send:
            manager._sync_pointer_locked()

        send.assert_called_once_with({"type": "set_pointer_config", "enabled": True})

    def test_strict_version_mismatch_raises(self) -> None:
        manager = _ViewerManager()
        fake_rerun = types.SimpleNamespace(__version__="0.29.0")

        with mock.patch("rerun_ui._manager._native_expected_rerun_major_minor", return_value="0.30"), mock.patch.dict(
            "sys.modules", {"rerun": fake_rerun}
        ):
            with self.assertRaises(RuntimeError):
                manager._ensure_version_compat()


if __name__ == "__main__":
    unittest.main()
