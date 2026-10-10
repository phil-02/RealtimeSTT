import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import portal_keyboard
from portal_keyboard import (
    KEY_PRESSED,
    KEY_RELEASED,
    NAMED_KEYS,
    PortalError,
    PortalKeyboard,
    char_keysym,
)


class FakeConnection:
    def __init__(self):
        self.closed = False

    def send_and_get_reply(self, message, timeout=None):
        return None

    def close(self):
        self.closed = True


def started_keyboard(call):
    """A PortalKeyboard with a live-looking session and `_call` replaced."""
    keyboard = PortalKeyboard()
    keyboard._conn = FakeConnection()
    keyboard._session = "/org/freedesktop/portal/desktop/session/1_1/s"
    keyboard._call = call
    return keyboard


class CharKeysymTests(unittest.TestCase):
    def test_latin1_characters_are_their_own_keysym(self):
        self.assertEqual(char_keysym("a"), 0x61)
        self.assertEqual(char_keysym("Q"), 0x51)
        self.assertEqual(char_keysym(" "), 0x20)
        self.assertEqual(char_keysym("é"), 0xE9)

    def test_control_characters_map_to_named_keys(self):
        self.assertEqual(char_keysym("\n"), NAMED_KEYS["Return"])
        self.assertEqual(char_keysym("\t"), NAMED_KEYS["Tab"])

    def test_other_characters_use_the_unicode_keysym_range(self):
        self.assertEqual(char_keysym("—"), 0x01002014)
        self.assertEqual(char_keysym("€"), 0x010020AC)


class SendTests(unittest.TestCase):
    def test_type_presses_and_releases_each_character_in_order(self):
        calls = []
        keyboard = started_keyboard(lambda method, sig, *body: calls.append(body[2:]))

        keyboard.type("Hi")

        self.assertEqual(calls, [
            (0x48, KEY_PRESSED), (0x48, KEY_RELEASED),
            (0x69, KEY_PRESSED), (0x69, KEY_RELEASED),
        ])

    def test_key_repeats_a_named_key(self):
        calls = []
        keyboard = started_keyboard(lambda method, sig, *body: calls.append(body[2:]))

        keyboard.key("BackSpace", 2)

        backspace = NAMED_KEYS["BackSpace"]
        self.assertEqual(calls, [(backspace, KEY_PRESSED), (backspace, KEY_RELEASED)] * 2)

    def test_failed_send_drops_the_session_so_the_next_call_starts_again(self):
        def revoked(*_):
            raise PortalError("NotifyKeyboardKeysym failed: invalid session")

        keyboard = started_keyboard(revoked)
        conn = keyboard._conn

        with self.assertRaises(PortalError):
            keyboard.type("x")

        self.assertTrue(conn.closed)
        self.assertIsNone(keyboard._session)
        self.assertIsNone(keyboard._conn)


class RestoreTokenTests(unittest.TestCase):
    def test_token_is_saved_private_and_removed_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            token_file = Path(tmp) / "realtimestt" / "portal-restore-token"
            with patch.object(portal_keyboard, "RESTORE_TOKEN_FILE", token_file):
                portal_keyboard.save_restore_token("abc123")
                self.assertEqual(portal_keyboard.load_restore_token(), "abc123")
                self.assertEqual(stat.S_IMODE(token_file.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(token_file.parent.stat().st_mode), 0o700)

                portal_keyboard.save_restore_token(None)
                self.assertFalse(token_file.exists())
                self.assertIsNone(portal_keyboard.load_restore_token())


if __name__ == "__main__":
    unittest.main()
