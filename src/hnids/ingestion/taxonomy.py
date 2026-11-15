"""Raw-label to class taxonomy (ADR-0002). Versioned and embedded in every bundle."""

from __future__ import annotations

import re

TAXONOMY_VERSION = 1

BENIGN = "BENIGN"

# Canonical raw label (normalized by :func:`canonical_raw_label`) -> class.
_RAW_TO_CLASS: dict[str, str] = {
    "benign": BENIGN,
    "dos hulk": "DoS",
    "dos goldeneye": "DoS",
    "dos slowloris": "DoS",
    "dos slowhttptest": "DoS",
    "ddos": "DDoS",
    "portscan": "PortScan",
    "ftp-patator": "BruteForce",
    "ssh-patator": "BruteForce",
    "web attack - brute force": "WebAttack",
    "web attack - xss": "WebAttack",
    "web attack - sql injection": "WebAttack",
    "bot": "Bot",
    "heartbleed": "Rare",
    "infiltration": "Rare",
}

CLASSES: tuple[str, ...] = (
    BENIGN,
    "DoS",
    "DDoS",
    "PortScan",
    "BruteForce",
    "WebAttack",
    "Bot",
    "Rare",
)

# en-dash (cp1252 0x96 / U+2013), its latin-1 mis-decode U+0096, and U+FFFD replacement char.
_DASH_CODEPOINTS = (0x2013, 0x2014, 0x0096, 0xFFFD)
_DASHES = re.compile("[" + "".join(map(chr, _DASH_CODEPOINTS)) + "]")
_SPACES = re.compile(r"\s+")


def canonical_raw_label(raw: str) -> str:
    """Normalize encoding artifacts, dashes, case and whitespace."""
    text = _DASHES.sub("-", raw)
    text = _SPACES.sub(" ", text).strip().lower()
    return re.sub(r"\s*-\s*", " - ", text) if text.startswith("web attack") else text


def map_label(raw: str) -> str | None:
    """Return the taxonomy class for a raw label, or ``None`` if unknown."""
    return _RAW_TO_CLASS.get(canonical_raw_label(raw))


def class_index() -> dict[str, int]:
    return {name: i for i, name in enumerate(CLASSES)}
