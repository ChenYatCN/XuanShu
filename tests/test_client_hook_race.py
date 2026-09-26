import unittest
from unittest.mock import Mock, patch

from wizwalker.client_handler import ClientHandler


class ClientHookRaceTests(unittest.TestCase):
    def test_unready_window_is_left_unmanaged_for_retry(self):
        client = object()
        constructor = Mock(side_effect=[TypeError("Invalid argument: 0"), client])
        handler = ClientHandler(client_cls=constructor)

        with patch("wizwalker.client_handler.utils.get_all_wizard_handles", return_value=[42]), \
             patch("wizwalker.client_handler.utils.get_pid_from_handle", side_effect=[0, 123, 123]):
            self.assertEqual(handler.get_new_clients(), [])
            constructor.assert_not_called()
            self.assertEqual(handler.get_new_clients(), [])
            self.assertEqual(handler._managed_handles, [])
            self.assertEqual(handler.clients, [])
            self.assertEqual(handler.get_new_clients(), [client])

        self.assertEqual(handler._managed_handles, [42])
        self.assertEqual(handler.clients, [client])
        self.assertEqual(constructor.call_count, 2)


if __name__ == "__main__":
    unittest.main()
