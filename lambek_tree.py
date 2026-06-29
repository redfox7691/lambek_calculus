#!/usr/bin/env python3
"""
Auto-generate Lambek proof trees from a chord sequence.

Default behavior:
- infers tonality and modulations automatically
- writes both .tex and .png in the selected output folder

Example:
  python3 lambek_tree.py --sequence D7 G7 Cmaj7 Am7
"""

from __future__ import annotations

import argparse
from functools import lru_cache
import heapq
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import sys

import cadence_stats
from chord_grade import NOTE_TO_PC, chord_root, chord_to_grade, is_diminished_chord, is_half_diminished, is_minor_chord, is_sus_chord
import simplify_tree_notation as readable_converter
from tonality_route import Tonality, apply_op, shortest_path, tonality_to_str


def _pc_of_note(note: str) -> int:
    # Accept both internal (BB) and display (Bb) spellings.
    return NOTE_TO_PC[chord_root(note)]


def _latex_escape_text(s: str) -> str:
    return (
        s.replace("\\", r"\textbackslash{}")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("#", r"\#")
        .replace("_", r"\_")
        .replace("&", r"\&")
        .replace("%", r"\%")
    )


def _roman_atom(grade: str) -> str:
    return rf"\text{{{_latex_escape_text(grade)}}}"


def _depth_sequent(left: str, right: str, depth: int) -> str:
    return rf"{left}\sststile{{{depth}}}{{}}{right}"


def _normalize_chord_tokens(raw_tokens: list[str]) -> list[str]:
    tokens: list[str] = []
    for token in raw_tokens:
        for part in token.split(","):
            clean = part.strip()
            if clean:
                tokens.append(clean)

    helper_words = {"chord", "chords", "sequence", "progression", "set", "of"}
    while tokens and tokens[0].strip(":").lower() in helper_words:
        tokens.pop(0)
    return tokens


def _format_block(block: list[str]) -> str:
    return " ".join(block)


def _contract_chords(chords: list[str]) -> tuple[list[str], list[str]]:
    """
    Contraction routine:
    - collapse repeated single chords (e.g., G G G -> G)
    - collapse repeated adjacent blocks (e.g., A B C A B C -> A B C)
    Returns (contracted_sequence, report_lines).
    """
    if not chords:
        return chords, []

    out: list[str] = []
    report: list[str] = []
    i = 0
    n = len(chords)

    while i < n:
        best = None  # (span, block_len, reps)
        max_len = (n - i) // 2
        for block_len in range(1, max_len + 1):
            block = chords[i : i + block_len]
            reps = 1
            while i + (reps + 1) * block_len <= n and chords[i + reps * block_len : i + (reps + 1) * block_len] == block:
                reps += 1
            if reps >= 2:
                span = reps * block_len
                cand = (span, block_len, reps)
                if best is None or cand[0] > best[0] or (cand[0] == best[0] and cand[1] < best[1]):
                    best = cand

        if best is not None:
            _span, block_len, reps = best
            block = chords[i : i + block_len]
            out.extend(block)
            if block_len == 1:
                report.append(f"repeated chord '{block[0]}' x{reps} -> x1")
            else:
                report.append(f"repeated block '{_format_block(block)}' x{reps} -> x1")
            i += block_len * reps
        else:
            out.append(chords[i])
            i += 1

    return out, report


def _normalized_quality(chord_symbol: str) -> str:
    # Remove root (and accidental) first, then analyze quality/extensions only.
    q = re.sub(r"^\s*[A-Ga-g][#b♯♭]?", "", chord_symbol or "")
    # Ignore slash bass notation (e.g., MA7/G -> MA7).
    q = q.split("/", 1)[0]
    q = q.strip().lower().replace(" ", "")
    q = re.sub(r"\([^)]*\)", "", q)
    return q


def _is_dominant_of(first_chord: str, second_chord: str) -> bool:
    first = chord_root(first_chord)
    second = chord_root(second_chord)
    return (NOTE_TO_PC[first] - NOTE_TO_PC[second]) % 12 == 7


def _is_tritone_sub_resolution(first_chord: str, second_chord: str) -> bool:
    """
    Tritone-sub dominant resolution: bII7 -> I (e.g., Db7 -> Cmaj7).
    We treat this as dominant-functional for key choice, but keep literal degree
    (typically bII) in the analyzed tonality.
    """
    if not _is_dominant_quality(first_chord):
        return False
    first = chord_root(first_chord)
    second = chord_root(second_chord)
    return (NOTE_TO_PC[first] - NOTE_TO_PC[second]) % 12 == 1


def _is_dominant_quality(chord_symbol: str) -> bool:
    """
    Dominant-like by default for 7/9/11/13 (with optional b/# alterations),
    unless explicit non-dominant quality is specified (maj/ma, minor, sus, dim/half-dim).
    """
    q = _normalized_quality(chord_symbol)

    # Explicit quality markers override dominant assumption.
    if "maj" in q or q.startswith("ma"):
        return False
    if is_minor_chord(chord_symbol):
        return False
    if is_half_diminished(chord_symbol):
        return False
    # Diminished chords often carry dominant-like function.
    if is_diminished_chord(chord_symbol):
        return True
    if "sus" in q:
        return False

    # Dominant extensions/alterations (treat as dominant function).
    has_dom = bool(re.search(r"(?:7|[#b]?9|[#b]?11|[#b]?13)", q))
    if not has_dom:
        return False

    # Avoid treating add-chords (e.g., add9) as dominant by this rule.
    if "add" in q and "7" not in q:
        return False

    return True


def _is_minor_seventh_family(chord_symbol: str) -> bool:
    """Minor-7 or half-diminished family (m7, m7b5, -7)."""
    if is_half_diminished(chord_symbol):
        return True
    low = _normalized_quality(chord_symbol)
    if "maj7" in low or "ma7" in low:
        return False
    return ("m7" in low) or ("min7" in low) or ("-7" in low)


def _detect_ii_v_i_target(chords: list[str], idx: int) -> tuple[Tonality, str] | None:
    """
    Detect local ii-V-I at positions idx, idx+1, idx+2 and return target tonality.
    """
    if idx + 2 >= len(chords):
        return None

    c1, c2, c3 = chords[idx], chords[idx + 1], chords[idx + 2]
    if not _is_minor_seventh_family(c1):
        return None
    if not _is_dominant_quality(c2):
        return None
    if not _is_dominant_of(c2, c3):
        return None

    tonic_note = chord_root(c3)
    t, note, _label = _chord_to_tonality(c3)
    tonic_mode = t.mode
    if chord_to_grade(c1, tonic_note, tonic_mode).upper() != "II":
        return None

    return t, note


def _detect_ii_v_target(chords: list[str], idx: int, next_section_first_chord: str | None = None) -> tuple[Tonality, str] | None:
    """
    Detect ii-V even when I is omitted at section end.
    Priority rule: if a chord can be interpreted as II instead of VII, prefer II.
    """
    if idx + 1 >= len(chords):
        return None

    c1, c2 = chords[idx], chords[idx + 1]
    if not _is_minor_seventh_family(c1):
        return None
    if not _is_dominant_quality(c2):
        return None

    tonic_note = None
    tonic_mode = "M"

    # If explicit I exists, use it.
    if idx + 2 < len(chords):
        c3 = chords[idx + 2]
        if _is_dominant_of(c2, c3):
            tonic_note = chord_root(c3)
            tonic_mode = "m" if is_minor_chord(c3) else "M"

    # If I is omitted, infer tonic from V.
    if tonic_note is None:
        implied = _implied_tonic_from_dominant(c2)
        if implied is None:
            return None
        _t, nn, _nl = implied
        tonic_note = nn

        # Mode preference hierarchy for unresolved ii-V:
        # 1) half-diminished ii strongly suggests minor tonic
        # 2) altered dominants (b9/#9/...) prefer minor
        # 3) next section first chord if same root
        if is_half_diminished(c1) or _dominant_prefers_minor_resolution(c2):
            tonic_mode = "m"
        elif next_section_first_chord is not None:
            try:
                if chord_root(next_section_first_chord) == tonic_note:
                    tonic_mode = "m" if is_minor_chord(next_section_first_chord) else "M"
            except ValueError:
                pass

    if tonic_note is None:
        return None

    # Enforce hierarchy: prefer II interpretation when valid.
    if chord_to_grade(c1, tonic_note, tonic_mode).upper() != "II":
        return None

    tonic = Tonality(pc=_pc_of_note(tonic_note), mode=tonic_mode)
    return tonic, tonic_note


def _dominant_grade(chord_symbol: str) -> str:
    return "v" if is_minor_chord(chord_symbol) else "V"


def _tonic_grade(chord_symbol: str) -> str:
    return "i" if is_minor_chord(chord_symbol) else "I"


def _format_note_label(note: str, minor: bool = False) -> str:
    # Internal notes use B for flat (e.g. EB); display as Eb.
    if len(note) >= 2 and note[1].lower() == "b":
        shown = note[0].upper() + "b"
    elif len(note) >= 2 and note[1] == "#":
        shown = note[0].upper() + "#"
    else:
        shown = note[0].upper()
    if minor:
        return shown[0].lower() + shown[1:]
    return shown


def _chord_to_tonality(chord_symbol: str) -> tuple[Tonality, str, str]:
    root = chord_root(chord_symbol)
    mode = "m" if is_minor_chord(chord_symbol) else "M"
    tonic = Tonality(pc=NOTE_TO_PC[root], mode=mode)
    # display label in paper style: uppercase for major, lowercase for minor
    label = _format_note_label(root, minor=(mode == "m"))
    return tonic, root, label


def _is_major_tonic_quality(chord_symbol: str) -> bool:
    low = _normalized_quality(chord_symbol)
    if is_minor_chord(chord_symbol) or is_half_diminished(chord_symbol):
        return False
    if "maj7" in low or "ma7" in low:
        return True
    if low.endswith("6") or "add6" in low:
        return True
    # Plain major/triad-like chords can also function as tonic; dominant-7 should not.
    if _is_dominant_quality(chord_symbol):
        return False
    return True


def _is_minor_tonic_quality(chord_symbol: str) -> bool:
    low = _normalized_quality(chord_symbol)
    if not is_minor_chord(chord_symbol):
        return False
    return True


def _collect_tonic_bold_keys(chords: list[str]) -> set[Tonality]:
    keys: set[Tonality] = set()
    for ch in chords:
        t, _note, _label = _chord_to_tonality(ch)
        if t.mode == "M" and _is_major_tonic_quality(ch):
            keys.add(t)
        elif t.mode == "m" and _is_minor_tonic_quality(ch):
            keys.add(t)
    return keys


def _format_left_label_key(label: str, bold: bool, math: bool) -> str:
    esc = _latex_escape_text(label)
    if not bold:
        return esc
    if math:
        return rf"\mathbf{{{esc}}}"
    return rf"\textbf{{{esc}}}"


def _is_tonic_priority_quality(chord_symbol: str) -> bool:
    """
    Priority tonic-like qualities requested by user: MA7/maj7, m7, 6.
    """
    low = chord_symbol.strip().lower().replace(" ", "")
    if "maj7" in low or "ma7" in low:
        return True
    if is_minor_chord(chord_symbol) and "7" in low:
        return True
    # Treat plain 6-quality chords as tonic-priority (e.g., Bb6, C6).
    if low.endswith("6") or "add6" in low:
        return True
    return False


def _implied_tonic_from_dominant(chord_symbol: str) -> tuple[Tonality, str, str] | None:
    """
    If chord is dominant-like, infer its implicit tonic (a 5th below / 4th above).
    Used when final tonic is omitted in the input progression.
    """
    if not _is_dominant_quality(chord_symbol):
        return None
    root = chord_root(chord_symbol)
    implied_pc = (NOTE_TO_PC[root] + 5) % 12

    # Prefer flat spellings for consistency with current project defaults.
    PC_TO_NAME_FLAT = {
        0: "C", 1: "Db", 2: "D", 3: "Eb", 4: "E", 5: "F",
        6: "Gb", 7: "G", 8: "Ab", 9: "A", 10: "Bb", 11: "B",
    }
    implied_note = PC_TO_NAME_FLAT[implied_pc]
    tonic = Tonality(pc=implied_pc, mode="M")
    return tonic, implied_note, _format_note_label(implied_note, minor=False)


def _dominant_prefers_minor_resolution(chord_symbol: str) -> bool:
    """
    Altered dominants (b9/#9/b13/#13/alt) tend to resolve to minor tonics.
    """
    low = (chord_symbol or "").lower().replace(" ", "")
    low = low.split("/", 1)[0]
    return bool(re.search(r"(b9|#9|b13|#13|alt)", low))


def _mode_from_tonic_chord(chord_symbol: str) -> str:
    return "m" if is_minor_chord(chord_symbol) else "M"


def _is_major_quality(chord_symbol: str) -> bool:
    return (not is_minor_chord(chord_symbol)) and (not is_half_diminished(chord_symbol)) and (not is_diminished_chord(chord_symbol))


def _quality_fits_grade(grade_up: str, chord_symbol: str, mode: str) -> bool:
    minor = is_minor_chord(chord_symbol)
    halfdim = is_half_diminished(chord_symbol)
    dim = is_diminished_chord(chord_symbol)
    dom = _is_dominant_quality(chord_symbol)

    if mode == "M":
        if grade_up in {"I", "IV"}:
            return _is_major_quality(chord_symbol)
        if grade_up in {"II", "III", "VI"}:
            return minor or halfdim
        if grade_up == "V":
            return dom or _is_major_quality(chord_symbol)
        if grade_up == "VII":
            return dim or halfdim
        return True

    # Minor mode (pragmatic jazz mapping).
    if grade_up in {"I", "IV"}:
        return minor
    if grade_up == "II":
        return halfdim or minor
    if grade_up in {"III", "VI", "VII"}:
        return _is_major_quality(chord_symbol)
    if grade_up == "V":
        return dom or _is_major_quality(chord_symbol)
    return True


def _candidate_key_score(chords: list[str], ton: Tonality, note: str, window: int = 8) -> int:
    score = 0
    for ch in chords[:window]:
        g = chord_to_grade(ch, note, ton.mode)
        g_up = g.upper()

        # Prefer diatonic degrees over chromatic alterations.
        if "#" in g_up or "B" in g_up:
            score -= 2
        else:
            score += 2

        if _quality_fits_grade(g_up, ch, ton.mode):
            score += 1
        else:
            score -= 1

        if _is_dominant_quality(ch) and g_up == "V":
            score += 2
        if _is_tonic_priority_quality(ch) and g_up == "I":
            score += 2
    return score


def _opening_tonic_candidate(chords: list[str], window: int = 8) -> tuple[Tonality, str, str] | None:
    cands: dict[tuple[int, str], tuple[Tonality, str, str]] = {}

    for ch in chords[:window]:
        if _is_tonic_priority_quality(ch):
            t, n, l = _chord_to_tonality(ch)
            cands[(t.pc, t.mode)] = (t, n, l)

    if not cands:
        return None

    best = None
    best_score = -10**9
    for _k, (t, n, l) in cands.items():
        sc = _candidate_key_score(chords, t, n, window=window)
        if sc > best_score:
            best = (t, n, l)
            best_score = sc

    return best


def _choose_implied_tonic_mode(
    chords: list[str],
    implied_note: str,
    dominant_chord: str,
    next_section_first_chord: str | None,
    current_ton: Tonality | None,
) -> str:
    # 1) Local evidence in current section (latest same-root tonic-like chord).
    for cand in reversed(chords[:-1]):
        try:
            if chord_root(cand) == implied_note and not _is_dominant_quality(cand):
                return _mode_from_tonic_chord(cand)
        except ValueError:
            continue

    # 2) If next section starts on the same root, borrow its mode.
    if next_section_first_chord:
        try:
            if chord_root(next_section_first_chord) == implied_note:
                return _mode_from_tonic_chord(next_section_first_chord)
        except ValueError:
            pass

    # 3) Altered dominant colors typically imply minor resolution.
    if _dominant_prefers_minor_resolution(dominant_chord):
        return "m"

    # 4) Keep current mode when already on the same tonic root.
    if current_ton is not None and current_ton.pc == _pc_of_note(implied_note):
        return current_ton.mode

    return "M"


def _infer_initial_tonality(chords: list[str], next_section_first_chord: str | None = None) -> tuple[Tonality, str, str]:
    # Priority 0: opening cadence evidence (ii-V-I or ii-V) should set the section key,
    # so ii is preferred over chromatic alternatives like #iv at section start.
    opening_window = min(max(len(chords) - 1, 0), 8)
    for i in range(opening_window):
        t = _detect_ii_v_i_target(chords, i)
        if t is not None:
            ton, note = t
            return ton, note, _format_note_label(note, minor=(ton.mode == "m"))
        t2 = _detect_ii_v_target(chords, i, next_section_first_chord)
        if t2 is not None:
            ton, note = t2
            return ton, note, _format_note_label(note, minor=(ton.mode == "m"))

    # Priority 1: if opening chords suggest a tonic-like key, choose the best
    # diatonic fit before falling back to tail-dominant inference.
    opening_tonic = _opening_tonic_candidate(chords, window=8)
    if opening_tonic is not None:
        return opening_tonic

    # Priority 2: unresolved trailing dominant implies a missing tonic.
    # Example: ... C7  => implicit F tonic when I is omitted.
    if chords:
        implied = _implied_tonic_from_dominant(chords[-1])
        if implied is not None:
            t, note, _label = implied
            # If the progression clearly starts on that implied tonic root,
            # use its mode directly (user rule: tonic chord quality defines key mode).
            first_root = chord_root(chords[0])
            if first_root == note:
                mode = "m" if is_minor_chord(chords[0]) else "M"
                t = Tonality(pc=t.pc, mode=mode)
                return t, note, _format_note_label(note, minor=(mode == "m"))
            return implied

    # Priority 2: last tonic-priority quality (MA7/maj7, m7, 6).
    for ch in reversed(chords):
        if _is_tonic_priority_quality(ch):
            return _chord_to_tonality(ch)

    # Priority 3: first strong adjacent cadence with dominant-like first chord.
    for i in range(len(chords) - 1):
        if _is_dominant_of(chords[i], chords[i + 1]) and _is_dominant_quality(chords[i]):
            return _chord_to_tonality(chords[i + 1])

    return _chord_to_tonality(chords[-1])


def _should_modulate(prev: str, curr: str, current_key_note: str, current_mode: str) -> bool:
    if not (_is_dominant_of(prev, curr) or _is_tritone_sub_resolution(prev, curr)):
        return False
    if not _is_dominant_quality(prev):
        return False

    # If already clear in current key (V->I or bII->I), do not modulate.
    g_prev = chord_to_grade(prev, current_key_note, current_mode).upper()
    g_curr = chord_to_grade(curr, current_key_note, current_mode).upper()
    if g_curr == "I" and g_prev in {"V", "BII"}:
        return False

    return True


def _route_op_rank(op: str) -> int:
    # Simplicity preference: D/S first, then P, use R only when necessary.
    if op in {"D", "S"}:
        return 0
    if op == "P":
        return 1
    return 2  # R


def _preferred_shortest_path(start: Tonality, goal: Tonality) -> tuple[list[Tonality], list[str]]:
    """
    Among shortest paths, choose the one with minimal R-usage and simpler moves.
    Cost tuple order:
      (steps, r_count, non_ds_count, op_rank_sum)
    """
    if start == goal:
        return [start], []

    ops_order = ["D", "S", "P", "R"]
    start_cost = (0, 0, 0, 0)

    best_cost: dict[Tonality, tuple[int, int, int, int]] = {start: start_cost}
    parent: dict[Tonality, tuple[Tonality, str]] = {}
    pq: list[tuple[tuple[int, int, int, int], int, str, Tonality]] = [(start_cost, start.pc, start.mode, start)]

    while pq:
        cost, _pc, _mode, cur = heapq.heappop(pq)
        if cost != best_cost.get(cur):
            continue
        if cur == goal:
            break

        for op in ops_order:
            nxt = apply_op(cur, op)
            new_cost = (
                cost[0] + 1,
                cost[1] + (1 if op == "R" else 0),
                cost[2] + (0 if op in {"D", "S"} else 1),
                cost[3] + _route_op_rank(op),
            )
            old = best_cost.get(nxt)
            if old is None or new_cost < old:
                best_cost[nxt] = new_cost
                parent[nxt] = (cur, op)
                heapq.heappush(pq, (new_cost, nxt.pc, nxt.mode, nxt))

    if goal not in parent and goal != start:
        # Fallback (should not happen on connected tonal graph)
        return shortest_path(start, goal)

    nodes: list[Tonality] = [goal]
    ops: list[str] = []
    x = goal
    while x != start:
        prev, op = parent[x]
        nodes.append(prev)
        ops.append(op)
        x = prev
    nodes.reverse()
    ops.reverse()
    return nodes, ops


def _route_nodes_ops(from_ton: Tonality, to_ton: Tonality) -> tuple[list[Tonality], list[str]]:
    if from_ton == to_ton:
        return [from_ton], []
    return _preferred_shortest_path(from_ton, to_ton)


def _tonality_label(t: Tonality, spelling: str) -> str:
    return tonality_to_str(t, spelling)


def _simplify_display_ops(display_ops: list[str]) -> list[str]:
    """
    Simplify adjacent inverse-like pairs in label display order only.
    Rules (adjacent only): D/S, S/D, P/P, R/R.
    """
    cancel = {("D", "S"), ("S", "D"), ("P", "P"), ("R", "R")}
    out: list[str] = []
    for op in display_ops:
        if out and (out[-1], op) in cancel:
            out.pop()
        else:
            out.append(op)
    return out


def _minimal_display_ops(base_ton: Tonality, target_ton: Tonality) -> list[str]:
    """
    Compute a canonical shortest relation chain equivalent to the current one,
    from base tonality to target tonality.
    """
    _nodes, ops = _preferred_shortest_path(base_ton, target_ton)
    # Display convention requested by user: newest operation first.
    return list(reversed(ops))




def generate_auto_tree(chords: list[str], next_section_first_chord: str | None = None) -> tuple[str, str]:
    """
    Returns (tree_latex, inferred_start_key_label)
    """
    if not chords:
        raise ValueError("At least one chord is required.")

    current_ton, current_key_note, base_key_label = _infer_initial_tonality(chords, next_section_first_chord)
    base_ton = current_ton
    # Force display normalization (Eb, Ab, etc.) for the initial key label.
    base_key_label = _format_note_label(current_key_note, minor=(current_ton.mode == "m"))
    relation_history: list[str] = []
    spelling = "sharps" if "#" in base_key_label else "flats"

    lines: list[str] = [r"\begin{prooftree}", r"\def\defaultHypSeparation{\hskip 0.07in}"]

    first_grade = chord_to_grade(chords[0], current_key_note, current_ton.mode)
    lines.append(rf"\AxiomC{{$\text{{{_latex_escape_text(chords[0])}}}$}}")
    lines.append(rf"\LeftLabel{{\textbf{{{_latex_escape_text(base_key_label)}:}}}}")
    lines.append(
        rf"\UnaryInfC{{$ {_depth_sequent(_roman_atom(first_grade), _roman_atom(first_grade), 0)} $}}"
    )

    context = _roman_atom(first_grade)
    current_grade = first_grade
    current_grade_expr = _roman_atom(first_grade)
    depth = 0

    for i in range(1, len(chords)):
        prev_ch = chords[i - 1]
        ch = chords[i]

        target_ton, target_note, _target_label = _chord_to_tonality(ch)

        ii_v_i_target = _detect_ii_v_i_target(chords, i)
        ii_v_target = _detect_ii_v_target(chords, i, next_section_first_chord)
        mod_target_ton = None
        mod_target_note = None

        # Priority 1: ii-V-I lookahead (modulate before the ii chord).
        if ii_v_i_target is not None:
            t2, n2 = ii_v_i_target
            if t2 != current_ton:
                mod_target_ton = t2
                mod_target_note = n2
        # Priority 1b: ii-V lookahead (I may be omitted): prefer II over VII.
        elif ii_v_target is not None:
            t2, n2 = ii_v_target
            if t2 != current_ton:
                mod_target_ton = t2
                mod_target_note = n2
        else:
            # Priority 2: unresolved final dominant => implicit missing tonic.
            if i == len(chords) - 1:
                implied_last = _implied_tonic_from_dominant(ch)
                if implied_last is not None:
                    nt, nn, _nl = implied_last
                    mode = _choose_implied_tonic_mode(
                        chords,
                        nn,
                        ch,
                        next_section_first_chord,
                        current_ton,
                    )
                    nt = Tonality(pc=nt.pc, mode=mode)
                    if nt != current_ton:
                        mod_target_ton = nt
                        mod_target_note = nn

            # Priority 3: if current chord is a dominant resolving to next chord,
            # switch key before showing this chord as V/v.
            if (
                mod_target_ton is None
                and i + 1 < len(chords)
                and _is_dominant_quality(ch)
                and (_is_dominant_of(ch, chords[i + 1]) or _is_tritone_sub_resolution(ch, chords[i + 1]))
            ):
                nt, nn, _nl = _chord_to_tonality(chords[i + 1])
                if nt != current_ton:
                    mod_target_ton = nt
                    mod_target_note = nn

            # Priority 4: final explicit tonic-like chord should set final key context.
            final_tonic_anchor = (
                _is_tonic_priority_quality(ch)
                or (
                    not _is_dominant_quality(ch)
                    and not is_sus_chord(ch)
                    and not is_half_diminished(ch)
                )
            )
            if (
                mod_target_ton is None
                and i == len(chords) - 1
                and final_tonic_anchor
                and target_ton != current_ton
            ):
                mod_target_ton = target_ton
                mod_target_note = target_note

            # Priority 5: previous dominant -> current chord cadence.
            if (
                mod_target_ton is None
                and _should_modulate(prev_ch, ch, current_key_note, current_ton.mode)
                and target_ton != current_ton
            ):
                mod_target_ton = target_ton
                mod_target_note = target_note

        # Hard override: always expose final parallel mode switch on last same-root tonic.
        if (
            i == len(chords) - 1
            and target_ton.pc == current_ton.pc
            and target_ton.mode != current_ton.mode
        ):
            mod_target_ton = target_ton
            mod_target_note = target_note

        # Final fallback: if last chord is same-root major/minor tonic with opposite mode,
        # force explicit parallel modulation (K_P) so the key change is visible.
        if (
            mod_target_ton is None
            and i == len(chords) - 1
            and chord_root(ch) == current_key_note
            and ((current_ton.mode == "m" and not is_minor_chord(ch)) or (current_ton.mode == "M" and is_minor_chord(ch)))
        ):
            mod_target_ton, mod_target_note, _ml = _chord_to_tonality(ch)

        if mod_target_ton is not None and mod_target_note is not None:
            nodes, ops = _route_nodes_ops(current_ton, mod_target_ton)
            grade_expr = current_grade_expr
            for idx, op in enumerate(ops):
                step_ton = nodes[idx + 1]
                relation_history.append(op)
                depth += 1

                lines.append(rf"\RightLabel{{(K$_{op}$)}}")
                # Reduce relation chain to the shortest equivalent form.
                display_ops = _minimal_display_ops(base_ton, step_ton)
                # Keep adjacent cancellation as final cosmetic pass.
                display_ops = _simplify_display_ops(display_ops)
                rel_tag = "".join([f"R_{{{x}}}" for x in display_ops])
                step_key_raw = _tonality_label(step_ton, spelling)
                base_key_math = _latex_escape_text(base_key_label)
                step_key_math = _latex_escape_text(step_key_raw)
                # Highlight only true modulation destination (not transit keys).
                step_label_bold = step_ton == mod_target_ton
                if rel_tag:
                    expr = rf" {rel_tag}{base_key_math} = {step_key_math} "
                    if step_label_bold:
                        lines.append(rf"\LeftLabel{{$\boldsymbol{{{expr}}}$:}}")
                    else:
                        lines.append(rf"\LeftLabel{{$ {expr} $:}}")
                else:
                    if step_label_bold:
                        lines.append(rf"\LeftLabel{{\textbf{{{_latex_escape_text(step_key_raw)}:}}}}")
                    else:
                        lines.append(rf"\LeftLabel{{{_latex_escape_text(step_key_raw)}:}}")

                boxed_context = rf"\Box_{op}\{{ {context} \}}"
                grade_expr = rf"\Box_{op}{grade_expr}"
                lines.append(
                    rf"\UnaryInfC{{$ {_depth_sequent(boxed_context, grade_expr, depth)} $}}"
                )
                context = boxed_context

            current_ton = mod_target_ton
            current_key_note = mod_target_note
            current_grade_expr = grade_expr

        # Graphical lookahead: if current chord resolves as dominant to next chord,
        # show it immediately as V/v in the upcoming local tonality.
        next_is_dominant_resolution = (
            i + 1 < len(chords)
            and _is_dominant_quality(ch)
            and _is_dominant_of(ch, chords[i + 1])
        )
        next_is_tritone_resolution = (
            i + 1 < len(chords)
            and _is_dominant_quality(ch)
            and _is_tritone_sub_resolution(ch, chords[i + 1])
        )

        if next_is_dominant_resolution:
            prev_grade_for_relation_expr = current_grade_expr
            curr_grade = _dominant_grade(ch)
        elif next_is_tritone_resolution:
            # Keep literal degree in target tonality (e.g., bII), do not force V.
            prev_grade_for_relation_expr = current_grade_expr
            curr_grade = chord_to_grade(ch, current_key_note, current_ton.mode)
        elif i == len(chords) - 1 and _is_dominant_quality(ch):
            # Unresolved ending dominant: display as dominant of implied tonic.
            prev_grade_for_relation_expr = current_grade_expr
            curr_grade = _dominant_grade(ch)
        # If previous chord is dominant of current, keep previous as V/v in relation,
        # but compute current grade from the active tonality context.
        elif _is_dominant_of(prev_ch, ch) and _is_dominant_quality(prev_ch):
            prev_grade_for_relation_expr = _roman_atom(_dominant_grade(prev_ch))
            curr_grade = chord_to_grade(ch, current_key_note, current_ton.mode)
        else:
            prev_grade_for_relation_expr = current_grade_expr
            curr_grade = chord_to_grade(ch, current_key_note, current_ton.mode)

        lines.append(rf"\AxiomC{{$\text{{{_latex_escape_text(ch)}}}$}}")
        lines.append(
            rf"\UnaryInfC{{$ {_depth_sequent(_roman_atom(curr_grade), _roman_atom(curr_grade), 0)} $}}"
        )
        lines.append(r"\RightLabel{$(\backslash_L)$}")

        depth += 1
        context = rf"{context}, {prev_grade_for_relation_expr}\backslash{_roman_atom(curr_grade)}"
        lines.append(
            rf"\BinaryInfC{{$ {_depth_sequent(context, _roman_atom(curr_grade), depth)} $}}"
        )

        current_grade = curr_grade
        current_grade_expr = _roman_atom(curr_grade)

    lines.append(r"\end{prooftree}")
    return "\n".join(lines), base_key_label


def _choose_tree_scale(chord_count: int, tree_latex: str) -> float | None:
    # Heuristic complexity score to decide when to scale the proof tree.
    score = (
        chord_count
        + tree_latex.count(r"\BinaryInfC") // 2
        + tree_latex.count(r"\Box_") // 2
        + len(tree_latex) // 1800
    )
    if score <= 10:
        return None
    if score <= 14:
        return 0.9
    if score <= 18:
        return 0.8
    if score <= 23:
        return 0.7
    if score <= 28:
        return 0.6
    if score <= 35:
        return 0.5
    return 0.4


def _apply_tree_scale(tree_latex: str, scale: float | None) -> str:
    if scale is None:
        return tree_latex
    begin = rf"\begin{{scprooftree}}{{{scale:.1f}}}"
    out = tree_latex.replace(r"\begin{prooftree}", begin, 1)
    out = out.replace(r"\end{prooftree}", r"\end{scprooftree}", 1)
    return out


def _latex_document(tree_latex: str, chord_count: int, scale: float | None) -> str:
    return "\n".join(
        [
            r"\documentclass[a4paper,landscape]{article}",
            r"\usepackage[T1]{fontenc}",
            r"\usepackage[utf8]{inputenc}",
            r"\usepackage[margin=0.6in]{geometry}",
            r"\usepackage{amsmath,amssymb}",
            r"\usepackage{graphicx}",
            r"\usepackage{stmaryrd}",
            r"\usepackage{turnstile}",
            r"\usepackage{bussproofs}",
            r"\newenvironment{scprooftree}[1]%",
            r"{\gdef\scalefactor{#1}\begin{center}\proofSkipAmount \leavevmode}%",
            r"{\scalebox{\scalefactor}{\DisplayProof}\proofSkipAmount \end{center} }",
            r"\pagestyle{empty}",
            r"\begin{document}",
            r"\noindent",
            f"% auto-scale: {scale if scale is not None else "none"}",
            tree_latex,
            r"\end{document}",
            "",
        ]
    )


def _slugify(s: str) -> str:
    s = s.replace("♯", "#").replace("♭", "b")
    s = s.replace("#", "s")
    s = re.sub(r"\s+", "_", s.strip())
    s = re.sub(r"[^A-Za-z0-9._-]", "_", s)
    return s or "tree"


def _next_available_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    i = 2
    while True:
        cand = parent / f"{stem}_{i}{suffix}"
        if not cand.exists():
            return cand
        i += 1


def _next_available_dir(path: Path) -> Path:
    """
    Return first available directory path.
    If path exists, append _1, _2, ...
    """
    if not path.exists():
        return path
    name = path.name
    parent = path.parent
    i = 1
    while True:
        cand = parent / f"{name}_{i}"
        if not cand.exists():
            return cand
        i += 1


def _resolve_out_dir(out_dir_arg: str) -> Path:
    script_dir = Path(__file__).resolve().parent
    out_dir = Path(out_dir_arg)
    if not out_dir.is_absolute():
        out_dir = script_dir / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _pdf_page_size_pt(pdf_path: Path) -> tuple[float, float] | None:
    pdfinfo = shutil.which("pdfinfo")
    if not pdfinfo:
        return None
    try:
        out = subprocess.check_output(
            [pdfinfo, "-f", "1", "-l", "1", str(pdf_path)],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None

    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Page size:"):
            m = re.search(r"([\d.]+)\s*x\s*([\d.]+)\s*pts", line)
            if m:
                return float(m.group(1)), float(m.group(2))
    return None


def _auto_dpi_for_pdf(pdf_path: Path, requested_dpi: int, max_px: int = 18000) -> int:
    size = _pdf_page_size_pt(pdf_path)
    if not size:
        return requested_dpi
    w_pt, h_pt = size
    max_dim_pt = max(w_pt, h_pt)
    if max_dim_pt <= 0:
        return requested_dpi
    max_safe_dpi = int(max_px * 72 / max_dim_pt)
    return max(72, min(requested_dpi, max_safe_dpi))


def _render_png_from_latex(latex_source: str, out_png: Path, dpi: int) -> tuple[bool, str]:
    pdflatex = shutil.which("pdflatex")
    if not pdflatex:
        return False, "pdflatex not found: PNG skipped."

    gs = shutil.which("gs")
    sips = shutil.which("sips")

    with tempfile.TemporaryDirectory(prefix="lambek_render_") as tmp:
        tmpdir = Path(tmp)
        tex_path = tmpdir / "render.tex"
        tex_path.write_text(latex_source, encoding="utf-8")

        try:
            subprocess.run(
                [
                    pdflatex,
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    "-output-directory",
                    str(tmpdir),
                    str(tex_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            tail = "\n".join((exc.stdout or "").splitlines()[-15:])
            return False, f"pdflatex failed: {tail}"

        pdf_path = tmpdir / "render.pdf"
        if not pdf_path.exists():
            return False, "pdflatex did not produce render.pdf"

        effective_dpi = _auto_dpi_for_pdf(pdf_path, dpi)

        if gs:
            try:
                subprocess.run(
                    [
                        gs,
                        "-dSAFER",
                        "-dBATCH",
                        "-dNOPAUSE",
                        "-sDEVICE=pngalpha",
                        f"-r{effective_dpi}",
                        "-dTextAlphaBits=4",
                        "-dGraphicsAlphaBits=4",
                        f"-sOutputFile={out_png}",
                        str(pdf_path),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=True,
                    text=True,
                )
                return True, ""
            except subprocess.CalledProcessError as exc:
                tail = "\n".join((exc.stdout or "").splitlines()[-15:])
                return False, f"ghostscript conversion failed: {tail}"

        if sips:
            try:
                subprocess.run(
                    [sips, "-s", "format", "png", str(pdf_path), "--out", str(out_png)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=True,
                    text=True,
                )
                return True, ""
            except subprocess.CalledProcessError as exc:
                tail = "\n".join((exc.stdout or "").splitlines()[-10:])
                return False, f"sips conversion failed: {tail}"

    return False, "No PDF-to-PNG tool found (gs/sips)."


def _resolve_standards_dir(standards_dir_arg: str) -> Path:
    script_dir = Path(__file__).resolve().parent
    standards_dir = Path(standards_dir_arg)
    if not standards_dir.is_absolute():
        standards_dir = script_dir / standards_dir
    return standards_dir


def _name_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _find_standard_json(standards_dir: Path, standard_name: str) -> Path:
    if not standards_dir.exists():
        raise FileNotFoundError(f"Standards directory not found: {standards_dir}")

    direct = standards_dir / f"{standard_name}.json"
    if direct.exists():
        return direct

    wanted = _name_key(standard_name)
    all_json = sorted(standards_dir.glob("*.json"))
    for p in all_json:
        if _name_key(p.stem) == wanted:
            return p

    sample = ", ".join([p.stem for p in all_json[:8]])
    raise FileNotFoundError(
        f"Standard '{standard_name}' not found in {standards_dir}. "
        f"Examples: {sample}"
    )


def _clean_jazz_chord_token(token: str) -> str:
    token = token.strip()
    # Remove outer wrappers even if split created one-sided parentheses.
    while token.startswith("("):
        token = token[1:].strip()
    while token.endswith(")"):
        token = token[:-1].strip()
    token = token.strip()
    if token.upper().replace(".", "") in {"NC", "N/C"}:
        return ""
    return token


def _expand_parenthetical_alternates(token: str) -> list[str]:
    """
    Expand forms like:
      Em7b5(Gm7)         -> Em7b5, Gm7
      Ebmaj7#11(Am7b5)   -> Ebmaj7#11, Am7b5
      (C7)               -> C7
    Handles imperfect tokens with one-sided parentheses conservatively.
    """
    token = (token or "").strip()
    if not token:
        return []

    # Imperfect one-sided parenthesis tokens.
    if "(" in token and ")" not in token:
        left, right = token.split("(", 1)
        return [x for x in [left.strip(), right.strip()] if x]
    if ")" in token and "(" not in token:
        return [token.replace(")", "").strip()]

    out: list[str] = []
    main = re.sub(r"\([^)]*\)", "", token).strip()
    if main:
        out.append(main)

    for inside in re.findall(r"\(([^)]*)\)", token):
        for alt in inside.split(","):
            a = alt.strip()
            if a:
                out.append(a)

    return out if out else [token]


def _parse_jazz_chords(chords_text: str) -> list[str]:
    if not chords_text:
        return []
    raw = re.split(r"[|,]", chords_text)
    out: list[str] = []
    for part in raw:
        for candidate in _expand_parenthetical_alternates(part):
            clean = _clean_jazz_chord_token(candidate)
            if clean:
                out.append(clean)
    return _normalize_chord_tokens(out)


def _extract_standard_sections(song: dict) -> list[tuple[str, list[str]]]:
    sections = song.get("Sections", [])
    out: list[tuple[str, list[str]]] = []
    for idx, section in enumerate(sections):
        label = section.get("Label", f"S{idx + 1}")
        base = f"{label}_{idx + 1}"

        main = section.get("MainSegment", {})
        main_chords = _parse_jazz_chords(main.get("Chords", ""))
        if main_chords:
            out.append((base, main_chords))

        endings = section.get("Endings", [])
        for eidx, ending in enumerate(endings, start=1):
            ending_chords = _parse_jazz_chords(ending.get("Chords", ""))
            if ending_chords:
                out.append((f"{base}_Ending{eidx}", ending_chords))

    if not out:
        direct = _parse_jazz_chords(str(song.get("Chords", "")))
        if direct:
            out.append(("Section_1", direct))
    return out


def _analyze_standard_json(
    std_json: Path,
    dpi: int,
    basename: str = "",
    out_root_name: str = "standard_outputs",
    overwrite: bool = False,
) -> Path:
    song = json.loads(std_json.read_text(encoding="utf-8"))
    sections = _extract_standard_sections(song)
    if not sections:
        raise ValueError(f"No chord sections found in {std_json.name}.")

    standard_root = _resolve_out_dir(out_root_name)
    standard_title = str(song.get('Title', std_json.stem))
    target = standard_root / _slugify(standard_title)
    if overwrite:
        standard_folder = target
        if standard_folder.exists():
            shutil.rmtree(standard_folder)
        standard_folder.mkdir(parents=True, exist_ok=False)
    else:
        standard_folder = _next_available_dir(target)
        standard_folder.mkdir(parents=True, exist_ok=False)

    print(f"Standard: {standard_title}")
    print(f"Source: {std_json}")
    print(f"Output folder: {standard_folder}")
    for i, (section_name, chords) in enumerate(sections, start=1):
        print()
        print(f"=== Section {i}: {section_name} ===")
        section_base = f"{basename}_{section_name}" if basename else f"{song.get('Title', std_json.stem)}_{section_name}"
        next_first = sections[i][1][0] if i < len(sections) and sections[i][1] else None
        _build_one_tree(chords, standard_folder, dpi, section_base, next_section_first_chord=next_first)

    return standard_folder


def _convert_standard_folder_to_readable(
    source_folder: Path,
    dpi: int,
    out_root_name: str = "standard_outputs_readable",
    overwrite: bool = False,
) -> Path | None:
    tex_files = sorted(source_folder.glob("*.tex"))
    if not tex_files:
        print(f"Warning: no TEX files found for readable conversion in {source_folder}")
        return None

    readable_root = _resolve_out_dir(out_root_name)
    target = readable_root / source_folder.name
    if overwrite:
        readable_folder = target
        if readable_folder.exists():
            shutil.rmtree(readable_folder)
        readable_folder.mkdir(parents=True, exist_ok=False)
    else:
        readable_folder = _next_available_dir(target)
        readable_folder.mkdir(parents=True, exist_ok=False)

    print()
    print(f"Readable output folder: {readable_folder}")
    for tf in tex_files:
        out_tex = readable_folder / tf.name
        readable_converter.simplify_tex_file(tf, out_tex, source_folder.parent)
        out_png = out_tex.with_suffix(".png")
        # Readable exports need higher raster density because formulas are dense.
        # Keep a conservative floor even if caller passes a low dpi.
        ok, msg = readable_converter._render_png_from_tex(out_tex, out_png, max(300, dpi))
        if ok:
            print(f"- {tf.name} -> {out_tex.name} + {out_png.name}")
        else:
            print(f"- {tf.name} -> {out_tex.name} (PNG skipped: {msg})")

    return readable_folder


def _build_one_tree(chords: list[str], out_dir: Path, dpi: int, basename: str = "", next_section_first_chord: str | None = None) -> tuple[Path, Path | None]:
    contracted_chords, contraction_report = _contract_chords(chords)
    if contraction_report:
        print("Contraction applied:")
        for line in contraction_report:
            print(f"- {line}")
        print("Sequence used:", " ".join(contracted_chords))
        print()
    chords = contracted_chords

    tree, key_label = generate_auto_tree(chords, next_section_first_chord=next_section_first_chord)
    scale = _choose_tree_scale(len(chords), tree)
    tree_out = _apply_tree_scale(tree, scale)

    print(tree_out)

    base = _slugify(basename) if basename else _slugify(f"tree_{key_label}_{'_'.join(chords)}")
    out_tex = _next_available_path(out_dir / f"{base}.tex")
    if scale is not None:
        print(f"Tree scale: {scale:.1f}")
    tex_doc = _latex_document(tree_out, len(chords), scale)
    out_tex.write_text(tex_doc, encoding="utf-8")
    print(f"TEX written to: {out_tex}")

    out_png = out_tex.with_suffix(".png")
    ok, msg = _render_png_from_latex(tex_doc, out_png, max(72, dpi))
    if ok:
        print(f"PNG written to: {out_png}")
        return out_tex, out_png

    print(f"Warning: {msg}")
    return out_tex, None



def _extract_last_sequent_from_tree(tree: str) -> str:
    for line in reversed(tree.splitlines()):
        if "\\BinaryInfC{$" in line or "\\UnaryInfC{$" in line:
            m = re.search(r"\{\$\s*(.*?)\s*\$\}", line)
            if m and "\\sststile" in m.group(1):
                return f"$ {m.group(1)} $"
    return "$ $"


@lru_cache(maxsize=512)
def _extract_last_sequent_from_readable_tree(tree: str) -> str:
    """
    Convert a formal tree TEX chunk to readable TEX in-memory, then extract
    the final sequent. Cached to speed up repeated section patterns.
    """
    standards_root = Path(__file__).resolve().parent / "standard_outputs"
    readable_tree = readable_converter.simplify_tex_text(tree, standards_root)
    return _extract_last_sequent_from_tree(readable_tree)


def _strip_math_wrapper(sequent: str) -> str:
    s = (sequent or "").strip()
    if s.startswith("$") and s.endswith("$") and len(s) >= 2:
        return s[1:-1].strip()
    return s


def _latex_math_to_plain(sequent: str) -> str:
    """
    Convert a LaTeX-style sequent into a more readable plain-text form.
    """
    s = _strip_math_wrapper(sequent)

    # Turnstile with depth.
    s = re.sub(r"\\sststile\{(\d+)\}\{\}", r" |- (depth=\1) ", s)

    # Key tags and atoms.
    s = re.sub(r"\\text\{\\textbf\{([^{}]+)\}\}", r"[\1]", s)
    s = re.sub(r"\\text\{([^{}]+)\}", r"\1", s)

    # Operators / braces.
    s = s.replace(r"\backslash", r" \ ")
    s = s.replace(r"\{", "{").replace(r"\}", "}")

    # Normalize spacing around punctuation.
    s = re.sub(r"\s*,\s*", ", ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _last_line_analysis_tex(entries: list[tuple[str, str]], title: str) -> str:
    rows = []
    for sec_name, sequent in entries:
        rows.append(rf'"{_latex_escape_text(sec_name)}" "{sequent}"\\')
    body = "\n".join(rows)
    return rf"""\documentclass[a4paper,11pt]{{article}}
\usepackage[T1]{{fontenc}}
\usepackage[utf8]{{inputenc}}
\usepackage[landscape,margin=1in]{{geometry}}
\usepackage{{amsmath,amssymb}}
\usepackage{{stmaryrd}}
\usepackage{{turnstile}}
\pagestyle{{empty}}
\begin{{document}}
\section*{{Last Line Analysis: {_latex_escape_text(title)}}}
{body}
\end{{document}}
"""


def _last_line_analysis_txt(entries: list[tuple[str, str]], title: str) -> str:
    lines = [f"Last Line Analysis: {title}"]
    for sec_name, sequent in entries:
        lines.append(f"{sec_name}\t{_latex_math_to_plain(sequent)}")
    return "\n".join(lines) + "\n"




def _collect_standard_entries_from_json(std_json: Path) -> tuple[str, list[tuple[str, str]]]:
    song = json.loads(std_json.read_text(encoding="utf-8"))
    sections = _extract_standard_sections(song)
    if not sections:
        return str(song.get('Title', std_json.stem)), []

    title = str(song.get('Title', std_json.stem))
    entries: list[tuple[str, str]] = []
    for i, (section_name, chords) in enumerate(sections, start=1):
        contracted_chords, _ = _contract_chords(chords)
        next_first = sections[i][1][0] if i < len(sections) and sections[i][1] else None
        tree, _ = generate_auto_tree(contracted_chords, next_section_first_chord=next_first)
        section_id = f"{_slugify(title)}_{section_name}"
        entries.append((section_id, _extract_last_sequent_from_readable_tree(tree)))
    return title, entries


def _analyze_many_standards_last_lines(
    target_dir: Path,
    out_root_name: str = "last_line_analysis",
    out_format: str = "tex",
) -> Path:
    # Mode A: dataset folder with many JSON files
    json_files = sorted(target_dir.glob("*.json"))
    # Mode B: folder containing many per-standard subfolders with section .tex files
    std_dirs = sorted([d for d in target_dir.iterdir() if d.is_dir()]) if target_dir.exists() else []

    entries: list[tuple[str, str]] = []
    title = target_dir.name

    if json_files:
        for std_json in json_files:
            std_title, std_entries = _collect_standard_entries_from_json(std_json)
            if not std_entries:
                continue
            for sec_name, sequent in std_entries:
                entries.append((f"{_slugify(std_title)}::{sec_name}", sequent))
    else:
        any_tex = False
        for d in std_dirs:
            tex_files = sorted(d.glob("*.tex"))
            if not tex_files:
                continue
            any_tex = True
            for tf in tex_files:
                content = tf.read_text(encoding="utf-8")
                entries.append((f"{_slugify(d.name)}::{tf.stem}", _extract_last_sequent_from_readable_tree(content)))
        if not any_tex:
            raise ValueError(f"Directory does not contain JSON standards or per-standard TEX folders: {target_dir}")

    if not entries:
        raise ValueError(f"No analyzable standards found in directory: {target_dir}")

    out_root = _resolve_out_dir(out_root_name)
    if out_format == "txt":
        out_file = _next_available_path(out_root / f"{_slugify(title)}_all.txt")
        out_file.write_text(_last_line_analysis_txt(entries, f"{title} (all standards)"), encoding="utf-8")
    else:
        out_file = _next_available_path(out_root / f"{_slugify(title)}_all.tex")
        out_file.write_text(_last_line_analysis_tex(entries, f"{title} (all standards)"), encoding="utf-8")
    print(f"Last-line analysis written to: {out_file}")
    return out_file


def _analyze_standard_last_lines(
    std_json: Path,
    out_root_name: str = "last_line_analysis",
    out_format: str = "tex",
) -> Path:
    title, entries = _collect_standard_entries_from_json(std_json)
    if not entries:
        raise ValueError(f"No chord sections found in {std_json.name}.")

    out_root = _resolve_out_dir(out_root_name)
    if out_format == "txt":
        out_file = _next_available_path(out_root / f"{_slugify(title)}.txt")
        out_file.write_text(_last_line_analysis_txt(entries, title), encoding="utf-8")
    else:
        out_file = _next_available_path(out_root / f"{_slugify(title)}.tex")
        out_file.write_text(_last_line_analysis_tex(entries, title), encoding="utf-8")
    print(f"Last-line analysis written to: {out_file}")
    return out_file




def _analyze_standard_folder_last_lines(
    std_folder: Path,
    out_root_name: str = "last_line_analysis",
    out_format: str = "tex",
) -> Path:
    tex_files = sorted(std_folder.glob("*.tex"))
    if not tex_files:
        raise ValueError(f"No section TEX files found in directory: {std_folder}")

    entries: list[tuple[str, str]] = []
    for tf in tex_files:
        content = tf.read_text(encoding="utf-8")
        entries.append((tf.stem, _extract_last_sequent_from_readable_tree(content)))

    title = std_folder.name
    out_root = _resolve_out_dir(out_root_name)
    if out_format == "txt":
        out_file = _next_available_path(out_root / f"{_slugify(title)}.txt")
        out_file.write_text(_last_line_analysis_txt(entries, title), encoding="utf-8")
    else:
        out_file = _next_available_path(out_root / f"{_slugify(title)}.tex")
        out_file.write_text(_last_line_analysis_tex(entries, title), encoding="utf-8")
    print(f"Last-line analysis written to: {out_file}")
    return out_file


def _write_cadence_csv(txt_path: Path, csv_out_arg: str, include_self: bool = False) -> Path:
    lines = txt_path.read_text(encoding="utf-8").splitlines()
    payloads = cadence_stats._extract_section_payloads(lines)
    links, chains = cadence_stats._count_links_and_chains(payloads, include_self=include_self)

    csv_out = Path(csv_out_arg)
    if not csv_out.is_absolute():
        csv_out = Path(__file__).resolve().parent / csv_out
    cadence_stats._write_csv(csv_out, links, chains)
    print(f"Cadence stats CSV written to: {csv_out}")
    return csv_out


def _main_analyse(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Fast last-line analysis (single standard or whole directory, no tree/PNG generation)."
    )
    parser.add_argument("target", help="Standard title, standard JSON path, standard folder path, or all-standards directory path.")
    parser.add_argument(
        "--standards-dir",
        default="JazzStandards-main/JazzStandards",
        help="Folder containing one JSON file per standard.",
    )
    parser.add_argument(
        "--format",
        choices=["tex", "txt"],
        default="tex",
        help="Output format for last-line analysis (default: tex).",
    )
    parser.add_argument(
        "--cadence-csv",
        default="",
        help="Optional CSV output path for cadence statistics (requires --format txt).",
    )
    parser.add_argument(
        "--include-self",
        action="store_true",
        help="When used with --cadence-csv, include self patterns like I->I and I->I->I.",
    )
    args = parser.parse_args(argv)

    out_file: Path
    target_path = Path(args.target)
    if target_path.exists() and target_path.is_dir():
        if any(target_path.glob("*.json")) or any(p.is_dir() for p in target_path.iterdir()):
            out_file = _analyze_many_standards_last_lines(target_path, "last_line_analysis", args.format)
        else:
            out_file = _analyze_standard_folder_last_lines(target_path, "last_line_analysis", args.format)
    elif target_path.exists() and target_path.is_file() and target_path.suffix.lower() == ".json":
        std_json = target_path
        out_file = _analyze_standard_last_lines(std_json, "last_line_analysis", args.format)
    else:
        standards_dir = _resolve_standards_dir(args.standards_dir)
        std_json = _find_standard_json(standards_dir, args.target)
        out_file = _analyze_standard_last_lines(std_json, "last_line_analysis", args.format)

    if args.cadence_csv:
        if out_file.suffix.lower() != ".txt":
            raise ValueError("--cadence-csv requires --format txt.")
        _write_cadence_csv(out_file, args.cadence_csv, include_self=args.include_self)
    return 0

def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "analyse":
        return _main_analyse(sys.argv[2:])

    parser = argparse.ArgumentParser(
        description="Auto-generate Lambek tree from a chord sequence or JazzStandard and export TEX+PNG."
    )
    src_group = parser.add_mutually_exclusive_group(required=True)
    src_group.add_argument(
        "--sequence",
        nargs="+",
        help="Chord sequence (e.g., D7 G7 Cmaj7 Am7)",
    )
    src_group.add_argument(
        "--standard",
        help="Jazz standard title (from JazzStandards JSON files).",
    )
    src_group.add_argument(
        "--standard-readable",
        help="Analyze one Jazz standard and also export readable TEX/PNG in standard_outputs_readable.",
    )
    src_group.add_argument(
        "--all-standards",
        action="store_true",
        help="Analyze all standards (.json) found in --standards-dir.",
    )
    parser.add_argument(
        "--standards-dir",
        default="JazzStandards-main/JazzStandards",
        help="Folder containing one JSON file per standard.",
    )
    parser.add_argument(
        "--out-dir",
        default="tree_outputs",
        help="Output directory for exported TEX/PNG (relative to script folder unless absolute).",
    )
    parser.add_argument(
        "--basename",
        default="",
        help="Optional base file name (without extension).",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=600,
        help="PNG render DPI (default: 600).",
    )
    parser.add_argument(
        "--readable-dpi",
        type=int,
        default=0,
        help="PNG render DPI for --standard-readable converted outputs (default: use --dpi).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite standard output folders instead of creating indexed folders (recommended for web use).",
    )
    args = parser.parse_args()

    out_dir = _resolve_out_dir(args.out_dir)

    if args.sequence:
        chords = _normalize_chord_tokens(args.sequence)
        if not chords:
            raise ValueError("No valid chord symbols found in input.")
        _build_one_tree(chords, out_dir, args.dpi, args.basename)
        return 0

    standards_dir = _resolve_standards_dir(args.standards_dir)

    if args.all_standards:
        all_json = sorted(standards_dir.glob("*.json"))
        if not all_json:
            raise ValueError(f"No JSON standards found in {standards_dir}.")
        print(f"Analyzing {len(all_json)} standards from: {standards_dir}")
        for idx, std_json in enumerate(all_json, start=1):
            print()
            print(f"##### [{idx}/{len(all_json)}] {std_json.stem} #####")
            _analyze_standard_json(std_json, args.dpi, args.basename, overwrite=args.overwrite)
        return 0

    standard_name = args.standard if args.standard else args.standard_readable
    std_json = _find_standard_json(standards_dir, standard_name)
    standard_folder = _analyze_standard_json(std_json, args.dpi, args.basename, "standard_outputs", overwrite=args.overwrite)
    if args.standard_readable:
        # Default readable conversion to a higher DPI than formal tree export.
        readable_dpi = args.readable_dpi if args.readable_dpi and args.readable_dpi > 0 else max(args.dpi, 600)
        _convert_standard_folder_to_readable(
            standard_folder,
            readable_dpi,
            "standard_outputs_readable",
            overwrite=args.overwrite,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
