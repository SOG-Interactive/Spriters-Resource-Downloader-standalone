"""Deterministic, Windows-safe file and folder naming."""
from __future__ import annotations

import re
import unicodedata

_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *{f"COM{i}" for i in range(1, 10)},
    *{f"LPT{i}" for i in range(1, 10)},
}
_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
_SLASHES = re.compile(r"\s*[/\\]+\s*")
_WS = re.compile(r"\s+")
MAX_COMPONENT = 120


def sanitize(name: str, fallback: str = "untitled") -> str:
    """Turn arbitrary text into one safe path component.

    Slashes become " - " so "Pokemon Gold / Silver" reads naturally,
    other forbidden characters become "_", whitespace is collapsed,
    trailing dots/spaces are stripped and reserved device names are
    prefixed. The result is never empty.
    """
    text = unicodedata.normalize("NFC", str(name or ""))
    text = _SLASHES.sub(" - ", text)
    text = _BAD_CHARS.sub("_", text)
    text = _WS.sub(" ", text).strip(" .")
    if len(text) > MAX_COMPONENT:
        text = text[:MAX_COMPONENT].rstrip(" .")
    if not text:
        text = fallback
    if text.split(".")[0].upper() in _RESERVED:
        text = "_" + text
    return text


def split_ext(filename: str) -> tuple[str, str]:
    """Return (stem, ext) where ext includes the dot and is lower-cased.

    Only short alphanumeric extensions count, so a title such as
    "Mr. Game & Watch" is not treated as having an extension.
    """
    m = re.search(r"\.([A-Za-z0-9]{1,5})$", filename or "")
    if not m:
        return filename or "", ""
    return filename[: m.start()], "." + m.group(1).lower()


def unique_names(entries: list[tuple[str, str, str]]) -> dict[str, str]:
    """Resolve filename collisions deterministically.

    ``entries`` holds (key, desired_stem, ext). Items whose sanitized stem
    collides (case-insensitively) inside the same list get " (key)" appended,
    so the key (the asset id) disambiguates. Returns key -> final filename.
    """
    counts: dict[str, int] = {}
    stems: dict[str, str] = {}
    for key, stem, _ext in entries:
        s = sanitize(stem)
        stems[key] = s
        counts[s.lower()] = counts.get(s.lower(), 0) + 1
    out: dict[str, str] = {}
    for key, _stem, ext in entries:
        s = stems[key]
        if counts[s.lower()] > 1:
            s = f"{s} ({key})"
        out[key] = s + (ext or "")
    return out
