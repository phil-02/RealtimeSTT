"""Keyboard input through the XDG RemoteDesktop portal.

On GNOME Wayland, xdotool reaches other windows through Xwayland, which asks
for "Remote Desktop" access on behalf of every X11 client at once and cannot
name the program that wants it. Granting that lets any X11 app synthesize
input for the rest of the login session.

This asks the portal directly, for the keyboard only. The grant belongs to
this process's D-Bus connection and ends when it exits. A restore token saved
in RESTORE_TOKEN_FILE lets later runs start without the prompt, until access
is revoked or the token goes stale (the portal then simply asks again).
"""

import os
import secrets
import threading
from pathlib import Path

from jeepney import DBusAddress, MatchRule, new_method_call
from jeepney.bus_messages import message_bus
from jeepney.io.blocking import Proxy, open_dbus_connection
from jeepney.wrappers import DBusErrorResponse, unwrap_msg

PORTAL = DBusAddress(
    "/org/freedesktop/portal/desktop",
    bus_name="org.freedesktop.portal.Desktop",
    interface="org.freedesktop.portal.RemoteDesktop",
)

# RemoteDesktop device types and persist modes (portal API version 2).
DEVICE_KEYBOARD = 1
PERSIST_UNTIL_REVOKED = 2

# Request.Response codes.
RESPONSE_CANCELLED = 1

KEY_PRESSED = 1
KEY_RELEASED = 0

STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
RESTORE_TOKEN_FILE = STATE_DIR / "realtimestt" / "portal-restore-token"

# X keysyms, the unit NotifyKeyboardKeysym speaks.
NAMED_KEYS = {
    "BackSpace": 0xFF08,
    "Tab": 0xFF09,
    "Return": 0xFF0D,
    "Left": 0xFF51,
    "Right": 0xFF53,
}
CONTROL_CHARS = {"\n": NAMED_KEYS["Return"], "\t": NAMED_KEYS["Tab"]}


class PortalError(RuntimeError):
    """The portal refused, or the keyboard session ended.

    `sent` counts the keysyms of the failed call that did reach the focused
    application, so callers can keep their record of the screen accurate.
    """

    def __init__(self, message, sent=0):
        super().__init__(message)
        self.sent = sent


def char_keysym(char):
    """Map one character to its X keysym.

    Latin-1 printable characters are their own keysym; everything else uses
    the Unicode keysym range, which Mutter maps onto a spare keycode.
    """
    if char in CONTROL_CHARS:
        return CONTROL_CHARS[char]
    codepoint = ord(char)
    if 0x20 <= codepoint <= 0x7E or 0xA0 <= codepoint <= 0xFF:
        return codepoint
    return 0x01000000 | codepoint


def load_restore_token():
    try:
        return RESTORE_TOKEN_FILE.read_text().strip() or None
    except OSError:
        return None


def save_restore_token(token):
    """Keep the token private: whoever holds it can resume keyboard access."""
    if not token:
        RESTORE_TOKEN_FILE.unlink(missing_ok=True)
        return
    RESTORE_TOKEN_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(RESTORE_TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(token)


class PortalKeyboard:
    """A keyboard-only RemoteDesktop session, safe to call from any thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._conn = None
        self._session = None

    def connect(self):
        """Start the session now, so any prompt appears at launch, not mid-dictation."""
        with self._lock:
            if self._session is None:
                self._start()

    def type(self, text):
        return self._send([char_keysym(char) for char in text])

    def key(self, name, count=1):
        return self._send([NAMED_KEYS[name]] * count)

    def close(self):
        with self._lock:
            self._close()

    def _send(self, keysyms):
        """Send keysyms, starting a fresh session if the current one died.

        GNOME Shell closes every RemoteDesktop session when the screen locks
        (main.js inhibits remote access outside screencast-capable session
        modes), and the person can also stop it from the top bar. The first
        keystroke after that fails, so reconnect once -- silently when the
        restore token is still honoured -- and resume where the dead session
        stopped. Returns the number of keysyms sent.
        """
        with self._lock:
            sent = 0
            # Retry only a session that was already running: if starting one
            # fails or is declined, asking again at once would not help.
            retry = self._session is not None
            while True:
                try:
                    if self._session is None:
                        self._start()
                    for keysym in keysyms[sent:]:
                        self._call("NotifyKeyboardKeysym", "oa{sv}iu",
                                   self._session, {}, keysym, KEY_PRESSED)
                        # A delivered press has typed the character; never
                        # resend it, even if the release below fails.
                        sent += 1
                        self._call("NotifyKeyboardKeysym", "oa{sv}iu",
                                   self._session, {}, keysym, KEY_RELEASED)
                    return sent
                except PortalError as error:
                    # Drop the dead session; closing it releases any held key.
                    self._close()
                    if not retry:
                        raise PortalError(str(error), sent) from error
                    retry = False

    def _start(self):
        self._conn = open_dbus_connection(bus="SESSION")
        try:
            results = self._request("CreateSession", "a{sv}", {
                "session_handle_token": ("s", self._token()),
            })
            self._session = results["session_handle"][1]

            select = {
                "types": ("u", DEVICE_KEYBOARD),
                "persist_mode": ("u", PERSIST_UNTIL_REVOKED),
            }
            restore_token = load_restore_token()
            if restore_token:
                select["restore_token"] = ("s", restore_token)
            self._request("SelectDevices", "oa{sv}", self._session, select)

            results = self._request("Start", "osa{sv}", self._session, "", {})
            save_restore_token(results.get("restore_token", ("s", None))[1])
            granted = results.get("devices", ("u", 0))[1]
            if not granted & DEVICE_KEYBOARD:
                raise PortalError("keyboard access was not granted")
        except BaseException:
            self._close()
            raise

    def _close(self):
        if self._conn is None:
            return
        if self._session is not None:
            close = new_method_call(
                DBusAddress(self._session, bus_name=PORTAL.bus_name,
                            interface="org.freedesktop.portal.Session"),
                "Close",
            )
            try:
                self._conn.send_and_get_reply(close, timeout=2)
            except Exception:
                pass  # Closing the connection below ends the session anyway.
        self._conn.close()
        self._conn = None
        self._session = None

    def _call(self, method, signature, *body):
        message = new_method_call(PORTAL, method, signature, body)
        try:
            return unwrap_msg(self._conn.send_and_get_reply(message))
        except (DBusErrorResponse, OSError) as error:
            raise PortalError(f"{method} failed: {error}") from error

    def _request(self, method, signature, *args):
        """Call a portal method that answers through a Request object.

        The Response signal can arrive before the method reply, so subscribe
        to the predictable request path first. The last argument must be the
        options dict, which receives the handle_token.
        """
        token = self._token()
        sender = self._conn.unique_name.lstrip(":").replace(".", "_")
        rule = MatchRule(
            type="signal",
            interface="org.freedesktop.portal.Request",
            member="Response",
            path=f"/org/freedesktop/portal/desktop/request/{sender}/{token}",
        )
        bus = Proxy(message_bus, self._conn)
        bus.AddMatch(rule)
        try:
            with self._conn.filter(rule) as responses:
                args[-1]["handle_token"] = ("s", token)
                self._call(method, signature, *args)
                # No timeout: Start waits on the person answering the prompt.
                code, results = self._conn.recv_until_filtered(responses).body
        finally:
            bus.RemoveMatch(rule)
        if code == RESPONSE_CANCELLED:
            raise PortalError("keyboard access was declined")
        if code != 0:
            raise PortalError(f"{method} was refused by the portal (code {code})")
        return results

    @staticmethod
    def _token():
        return "realtimestt_" + secrets.token_hex(8)
