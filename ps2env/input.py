from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from Xlib import X, XK, display, error
from Xlib.ext import xtest
from Xlib.protocol import event


def _window_id(window: Any) -> int:
    return int(getattr(window, "id"))


@dataclass
class X11InputController:
    display_name: str
    window_id: int | None = None
    held_keys: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self._display = display.Display(self.display_name)
        self._root = self._display.screen().root
        self._net_wm_pid = self._display.intern_atom("_NET_WM_PID")
        self._net_active_window = self._display.intern_atom("_NET_ACTIVE_WINDOW")

    def close(self) -> None:
        try:
            self.release_all()
        finally:
            self._display.close()

    def bind_window(self, window_id: int) -> None:
        self.window_id = int(window_id)

    def find_window_by_pid(self, pid: int) -> int | None:
        queue: deque[Any] = deque([self._root])
        matches: list[int] = []
        while queue:
            window = queue.popleft()
            try:
                if self._window_pid(window) == pid:
                    matches.append(_window_id(window))
                children = list(window.query_tree().children)
            except error.XError:
                continue
            queue.extend(children)
        if not matches:
            return None
        return matches[-1]

    def move_resize_window(self, x: int, y: int, width: int, height: int) -> None:
        window = self._require_window()
        try:
            window.configure(x=int(x), y=int(y), width=int(width), height=int(height), border_width=0, stack_mode=X.Above)
            window.raise_window()
            self._display.sync()
        except error.XError as exc:  # pragma: no cover - exercised in integration
            raise RuntimeError(f"Failed to move/resize X11 window {self.window_id}: {exc}") from exc

    def activate_window(self) -> None:
        window = self._require_window()
        try:
            data = [2, X.CurrentTime, 0, 0, 0]
            message = event.ClientMessage(
                window=window,
                client_type=self._net_active_window,
                data=(32, data),
            )
            self._root.send_event(
                message,
                event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask,
            )
            self._display.set_input_focus(window, X.RevertToParent, X.CurrentTime)
            window.raise_window()
            self._display.sync()
        except error.XError:
            # Focus is best-effort. Xdummy sessions often have no WM to honor
            # _NET_ACTIVE_WINDOW, so we still proceed with XTEST delivery.
            try:
                self._display.set_input_focus(window, X.RevertToParent, X.CurrentTime)
                window.raise_window()
                self._display.sync()
            except error.XError:
                return

    def press_key(self, key: str) -> None:
        self.activate_window()
        keycode = self._keycode_for(key)
        xtest.fake_input(self._display, X.KeyPress, keycode)
        self._display.sync()
        self.held_keys.add(key)

    def release_key(self, key: str) -> None:
        if key not in self.held_keys:
            return
        self.activate_window()
        keycode = self._keycode_for(key)
        xtest.fake_input(self._display, X.KeyRelease, keycode)
        self._display.sync()
        self.held_keys.discard(key)

    def tap_key(self, key: str) -> None:
        self.activate_window()
        keycode = self._keycode_for(key)
        xtest.fake_input(self._display, X.KeyPress, keycode)
        xtest.fake_input(self._display, X.KeyRelease, keycode)
        self._display.sync()

    def release_all(self) -> None:
        for key in list(self.held_keys):
            self.release_key(key)

    def _keycode_for(self, key: str) -> int:
        keysym = XK.string_to_keysym(key)
        if keysym == 0:
            raise ValueError(f"Unsupported X11 key symbol: {key}")
        keycode = self._display.keysym_to_keycode(keysym)
        if keycode == 0:
            raise ValueError(f"Unsupported X11 keycode mapping: {key}")
        return int(keycode)

    def _require_window(self) -> Any:
        if self.window_id is None:
            raise RuntimeError("X11 window is not bound.")
        return self._display.create_resource_object("window", int(self.window_id))

    def _window_pid(self, window: Any) -> int | None:
        try:
            prop = window.get_full_property(self._net_wm_pid, X.AnyPropertyType)
        except error.XError:
            return None
        if prop is None or not getattr(prop, "value", None):
            return None
        value = prop.value
        if len(value) < 1:
            return None
        return int(value[0])
