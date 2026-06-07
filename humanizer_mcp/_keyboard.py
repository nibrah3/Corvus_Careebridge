"""
Humanized keyboard input.

Timing model: ex-Gaussian (exponnorm) inter-keystroke intervals
              + bigram speed factors from English corpus
              + fatigue: 0.05% slowdown per character typed
              + QWERTY physical neighbor typos at configurable error rate
              + realistic key hold duration (not instantaneous press/release)
Unicode:      pynput for ASCII/common chars; SendInput+KEYEVENTF_UNICODE for
              non-ASCII characters that pynput cannot map (e.g. U+2009 thin space,
              em-dash, smart quotes). No admin required on Windows.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import random
import sys
import time

from ._profile import BehaviorProfile
from ._distributions import (
    sample_iki,
    sample_hold,
    bigram_factor,
    typo_neighbor,
)

# ── Backend ───────────────────────────────────────────────────────────────────

_kb = None
_Key = None
_IS_WINDOWS = sys.platform == "win32"


def _ensure_kb() -> None:
    global _kb, _Key
    if _kb is not None:
        return
    try:
        from pynput.keyboard import Controller as _KC, Key as _K
        _kb = _KC()
        _Key = _K
    except Exception as exc:
        raise ImportError(f"pynput keyboard not available: {exc}. pip install pynput")


# ── Windows SendInput Unicode fallback ───────────────────────────────────────
# Used for any character pynput cannot map via VkKeyScan (ordinal > 127 that
# has no virtual key code on the current keyboard layout, e.g. U+2009 thin space).

if _IS_WINDOWS:
    _KEYEVENTF_UNICODE = 0x0004
    _KEYEVENTF_KEYUP   = 0x0002
    _INPUT_KEYBOARD    = 1

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk",         ctypes.wintypes.WORD),
            ("wScan",       ctypes.wintypes.WORD),
            ("dwFlags",     ctypes.wintypes.DWORD),
            ("time",        ctypes.wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_ulong),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT)]

    class _INPUT_STRUCT(ctypes.Structure):
        _anonymous_ = ("_u",)
        _fields_    = [("type", ctypes.wintypes.DWORD), ("_u", _INPUT_UNION)]

    def _sendinput_unicode(ch: str, hold_secs: float) -> None:
        """Deliver one Unicode character via SendInput+KEYEVENTF_UNICODE (key-down + key-up)."""
        code = ord(ch) & 0xFFFF   # BMP only — surrogate pairs need two calls
        inp_dn = _INPUT_STRUCT()
        inp_dn.type = _INPUT_KEYBOARD
        inp_dn.ki.wVk    = 0
        inp_dn.ki.wScan  = code
        inp_dn.ki.dwFlags = _KEYEVENTF_UNICODE
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp_dn), ctypes.sizeof(inp_dn))
        time.sleep(hold_secs)
        inp_up = _INPUT_STRUCT()
        inp_up.type = _INPUT_KEYBOARD
        inp_up.ki.wVk    = 0
        inp_up.ki.wScan  = code
        inp_up.ki.dwFlags = _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(inp_up))
else:
    def _sendinput_unicode(ch: str, hold_secs: float) -> None:
        pass   # non-Windows: no-op, pynput handles it


# ── Key injection primitives ──────────────────────────────────────────────────

def _press_char(char: str, hold_secs: float) -> None:
    """
    Press and release a single character with realistic hold duration.

    Non-ASCII (ordinal > 127) always goes through SendInput+KEYEVENTF_UNICODE
    on Windows — bypasses pynput which silently maps some chars to wrong
    codepoints (e.g. U+2009 thin space becomes U+0020 regular space).
    """
    _ensure_kb()
    if _IS_WINDOWS and ord(char) > 127:
        _sendinput_unicode(char, hold_secs)
        return
    try:
        _kb.press(char)
        time.sleep(hold_secs)
        _kb.release(char)
    except Exception:
        if _IS_WINDOWS:
            _sendinput_unicode(char, hold_secs)
        else:
            try:
                _kb.type(char)
            except Exception:
                pass


def _backspace(hold_secs: float) -> None:
    _ensure_kb()
    _kb.press(_Key.backspace)
    time.sleep(hold_secs)
    _kb.release(_Key.backspace)


def _press_key(key, hold_secs: float) -> None:
    """Press any pynput Key with hold duration."""
    _ensure_kb()
    _kb.press(key)
    time.sleep(hold_secs)
    _kb.release(key)


# ── High-level type() ─────────────────────────────────────────────────────────

def type_text(
    text: str,
    profile: BehaviorProfile | None = None,
    rng: random.Random | None = None,
) -> None:
    """
    Type text character-by-character with full human timing model:
      - ex-Gaussian IKI with bigram acceleration and fatigue
      - QWERTY-adjacent typos followed by backspace correction
      - Realistic key hold duration per character
      - Inter-word pauses slightly longer than inter-letter pauses
    """
    if not text:
        return
    if profile is None:
        profile = BehaviorProfile.default()
    if rng is None:
        rng = random.Random()

    prev_char = ""

    for i, char in enumerate(text):
        fatigue = profile.fatigue_factor()
        hold = sample_hold(profile.hold_k, profile.hold_scale)

        # Typo: with error_rate probability, press a QWERTY neighbor first
        if char.lower() in "abcdefghijklmnopqrstuvwxyz" and rng.random() < profile.error_rate:
            wrong = typo_neighbor(char, rng)
            if wrong and wrong != char.lower():
                _press_char(wrong, hold * 0.8)
                # Typo detection latency: 80-350ms before user notices and corrects
                detection_delay = max(0.08, min(0.35, rng.gauss(0.18, 0.06)))
                time.sleep(detection_delay)
                _backspace(hold * 0.7)
                # Brief pause after correction — cognitive reset
                time.sleep(max(0.03, rng.gauss(0.06, 0.02)))

        # Type the correct character (Unicode fallback handled inside _press_char)
        _press_char(char, hold)

        profile.chars_typed += 1

        # IKI: ex-Gaussian base × bigram factor × fatigue
        if i < len(text) - 1:
            bf = bigram_factor(prev_char, char) if prev_char else 1.0
            iki = sample_iki(profile.iki_k, profile.iki_scale, fatigue) * bf

            # Inter-word pause: space character gets a slightly longer pause
            if char == " ":
                iki *= rng.uniform(1.15, 1.45)

            # Post-punctuation pause: comma, period, etc.
            if char in ".,;:!?":
                iki *= rng.uniform(1.3, 1.8)

            time.sleep(iki)

        prev_char = char


def press_key(
    key_name: str,
    profile: BehaviorProfile | None = None,
    rng: random.Random | None = None,
) -> None:
    """
    Press a named key (enter, tab, escape, backspace, etc.) with hold duration.
    key_name: standard pynput Key name string.
    """
    if profile is None:
        profile = BehaviorProfile.default()
    if rng is None:
        rng = random.Random()

    hold = sample_hold(profile.hold_k, profile.hold_scale)
    _ensure_kb()

    key_map = {
        "enter":     _Key.enter,
        "tab":       _Key.tab,
        "escape":    _Key.esc,
        "backspace": _Key.backspace,
        "delete":    _Key.delete,
        "home":      _Key.home,
        "end":       _Key.end,
        "pageup":    _Key.page_up,
        "pagedown":  _Key.page_down,
        "up":        _Key.up,
        "down":      _Key.down,
        "left":      _Key.left,
        "right":     _Key.right,
        "f5":        _Key.f5,
    }

    key = key_map.get(key_name.lower())
    if key is None:
        return  # unknown key — skip silently

    _press_key(key, hold)


def hotkey(
    *keys: str,
    profile: BehaviorProfile | None = None,
    rng: random.Random | None = None,
) -> None:
    """
    Press a key combination (e.g. hotkey('ctrl', 'a')).
    Keys are pressed sequentially with small delays, not simultaneously.
    """
    if profile is None:
        profile = BehaviorProfile.default()
    if rng is None:
        rng = random.Random()

    _ensure_kb()
    modifier_map = {
        "ctrl":  _Key.ctrl,
        "shift": _Key.shift,
        "alt":   _Key.alt,
        "win":   _Key.cmd,
    }

    parsed = []
    for k in keys:
        mk = modifier_map.get(k.lower())
        parsed.append(mk if mk else k)

    hold = sample_hold(profile.hold_k, profile.hold_scale)
    inter_key_delay = max(0.015, rng.gauss(0.030, 0.008))

    # Press all keys down with slight delays between each
    for key in parsed:
        _kb.press(key)
        time.sleep(inter_key_delay)

    time.sleep(hold)

    # Release in reverse order
    for key in reversed(parsed):
        _kb.release(key)
        time.sleep(inter_key_delay * 0.5)
