from __future__ import annotations

import unittest
from unittest import mock

from rerun_ui._manager import _ViewerManager
from rerun_ui._types import ViewerStatus


class ReconnectBehaviorTest(unittest.TestCase):
    def test_recover_if_needed_calls_recover_on_disconnected(self) -> None:
        manager = _ViewerManager()
        manager._auto_recover_enabled = True
        manager._status = ViewerStatus.DISCONNECTED

        with mock.patch.object(manager, "_recover_or_spawn") as recover:
            manager._recover_if_needed()

        recover.assert_called_once()

    def test_handle_disconnected_sets_plain_when_grpc_alive(self) -> None:
        manager = _ViewerManager()
        manager._connection_id = 4
        manager._status = ViewerStatus.CUSTOM_CONNECTED

        with mock.patch.object(manager, "_is_port_open", return_value=True):
            manager._handle_disconnected(4)

        self.assertEqual(manager._status, ViewerStatus.PLAIN_CONNECTED)

    def test_handle_disconnected_ignores_stale_connection(self) -> None:
        manager = _ViewerManager()
        manager._connection_id = 7
        manager._status = ViewerStatus.CUSTOM_CONNECTED

        with mock.patch.object(manager, "_is_port_open", return_value=False):
            manager._handle_disconnected(6)

        self.assertEqual(manager._status, ViewerStatus.CUSTOM_CONNECTED)

    def test_add_button_triggers_on_demand_recovery(self) -> None:
        manager = _ViewerManager()
        manager._auto_recover_enabled = True
        manager._status = ViewerStatus.DISCONNECTED

        with mock.patch.object(manager, "_recover_or_spawn") as recover:
            manager.add_button("Test", lambda: None)

        recover.assert_called_once()


if __name__ == "__main__":
    unittest.main()
