#!/usr/bin/env python3
"""
Map a chord symbol to a Roman numeral grade in a given tonality.

Examples:
  chord_to_grade("F", "F") -> "I"
  chord_to_grade("C", "F") -> "V"
  chord_to_grade("F", "C") -> "IV"
"""

from __future__ import annotations

import re
import sys

# Enharmonic spellings mapped to pitch classes.
NOTE_TO_PC = {
    "C": 0,
    "B#": 0,
    "C#": 1,
    "DB": 1,
    "D": 2,
    "D#": 3,
    "EB": 3,
    "E": 4,
    "FB": 4,
    "E#": 5,
    "F": 5,
    "F#": 6,
    "GB": 6,
    "G": 7,
    "G#": 8,
    "AB": 8,
    "A": 9,
    "A#": 10,
    "BB": 10,
    "B": 11,
    "CB": 11,
}

# Chromatic distance -> Roman degree in a major-scale reference.
MAJOR_DEGREE = {
    0: "I",
    1: "bII",
    2: "II",
    3: "bIII",
    4: "III",
    5: "IV",
    6: "#IV",
    7: "V",
    8: "bVI",
    9: "VI",
    10: "bVII",
    11: "VII",
}

MINOR_DEGREE = dict(MAJOR_DEGREE)
# Minor-mode diatonic spelling overrides.
MINOR_DEGREE[3] = "III"
MINOR_DEGREE[8] = "VI"
MINOR_DEGREE[10] = "VII"
# Keep chromatic leading tone distinct.
MINOR_DEGREE[11] = "#VII"

ROOT_RE = re.compile(r"^\s*([A-Ga-g])([#b♯♭]?)(.*)$")


def _normalize_note(token: str) -> str:
    token = token.strip()
    if not token:
        raise ValueError("Empty note/chord token.")
    token = token.replace("♯", "#").replace("♭", "b")
    letter = token[0].upper()
    accidental = token[1:] if len(token) > 1 else ""
    accidental = accidental.replace("b", "B")
    note = f"{letter}{accidental}"
    if note not in NOTE_TO_PC:
        raise ValueError(f"Unsupported note '{token}'.")
    return note


def chord_root(chord_symbol: str) -> str:
    """
    Extract root note from a chord symbol.
    Accepts values like: C, F#, Bb, G7, Dm7, Abmaj9, C/E
    """
    m = ROOT_RE.match(chord_symbol or "")
    if not m:
        raise ValueError(f"Invalid chord symbol '{chord_symbol}'.")
    letter, accidental, _rest = m.groups()
    return _normalize_note(letter + accidental)


def _chord_rest(chord_symbol: str) -> str:
    m = ROOT_RE.match(chord_symbol or "")
    if not m:
        raise ValueError(f"Invalid chord symbol '{chord_symbol}'.")
    rest = (m.group(3) or "").strip()
    # Ignore slash bass notation (e.g., CMA7/G -> CMA7).
    if "/" in rest:
        rest = rest.split("/", 1)[0].strip()
    return rest


def _quality_normalized(chord_symbol: str) -> str:
    rest = _chord_rest(chord_symbol).lower().replace(" ", "")
    # Ignore parenthetical alterations like (#9), (b13), (add9), etc.
    rest = re.sub(r"\([^)]*\)", "", rest)
    return rest




def is_minor_chord(chord_symbol: str) -> bool:
    """
    Heuristic minor-quality detector from chord symbol.
    Minor examples: Dm, Dm7, Dmin7, D-7
    Not minor: D, D7, Dmaj7, DMA7
    """
    rest = _quality_normalized(chord_symbol)
    if not rest:
        return False

    # Common major markers that start with m/M.
    lower = rest
    if lower.startswith(("maj", "ma")):
        return False

    # Half-diminished shorthand using 0 (e.g., B07) behaves as m7b5.
    if lower.startswith("07") or lower.startswith("0/7"):
        return True

    # Common minor markers.
    if lower.startswith(("m", "min", "-")):
        return True

    return False

def is_half_diminished(chord_symbol: str) -> bool:
    """
    Detect half-diminished quality (e.g., Dm7(b5), Dø7, Dm7b5, D07).
    """
    raw_rest = _chord_rest(chord_symbol)
    low = raw_rest.lower().replace(" ", "")
    no_paren = _quality_normalized(chord_symbol)
    return (
        ("m7(b5)" in low)
        or ("m7b5" in low)
        or ("ø" in raw_rest)
        or ("m7b5" in no_paren)
        or low.startswith("07")
        or no_paren.startswith("07")
    )


def is_diminished_chord(chord_symbol: str) -> bool:
    """Detect diminished quality (e.g., Bdim, Bo, Bdim7, B°7)."""
    raw_rest = _chord_rest(chord_symbol)
    low = raw_rest.lower().replace(" ", "")
    no_paren = _quality_normalized(chord_symbol)

    if is_half_diminished(chord_symbol):
        return False

    return (
        ("dim" in low)
        or ("°" in raw_rest)
        or low.startswith("o")
        or no_paren.startswith("o")
    )


def is_sus_chord(chord_symbol: str) -> bool:
    """Detect suspended chords like Csus, Csus4, C7sus, C9sus."""
    q = _quality_normalized(chord_symbol)
    return "sus" in q



def _apply_quality_case(roman_grade: str, minor: bool) -> str:
    if not minor:
        return roman_grade
    return "".join(ch.lower() if ch.isalpha() else ch for ch in roman_grade)


def chord_to_grade(chord_symbol: str, tonality: str, tonality_mode: str = "M") -> str:
    """
    Convert chord root to Roman grade inside `tonality`.
    """
    chord_note = chord_root(chord_symbol)
    key_note = _normalize_note(tonality)

    delta = (NOTE_TO_PC[chord_note] - NOTE_TO_PC[key_note]) % 12
    mode_token_raw = str(tonality_mode).strip()
    mode_token_low = mode_token_raw.lower()
    is_minor_mode = (mode_token_raw == "m") or (mode_token_low in {"min", "minor"})
    degree_map = MINOR_DEGREE if is_minor_mode else MAJOR_DEGREE
    roman = degree_map[delta]

    # Suspended chords: keep degree function; on scale degree II use ii color by convention.
    if is_sus_chord(chord_symbol):
        if roman.upper() == "II":
            return "ii"
        return roman

    lower_quality = is_minor_chord(chord_symbol) or is_diminished_chord(chord_symbol)
    return _apply_quality_case(roman, lower_quality)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("Usage: python chord_grade.py <CHORD> <TONALITY>")
        print("Example: python chord_grade.py F C   # IV")
        return 1
    chord, tonality = argv[1], argv[2]
    try:
        print(chord_to_grade(chord, tonality))
    except ValueError as exc:
        print(f"Error: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
