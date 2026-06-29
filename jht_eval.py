#!/usr/bin/env python3
"""
Evaluate this project's analysis against the Jazz Harmony Treebank (JHT).

The script is intentionally lightweight: it reads JHT's treebank.json, converts
JHT chord spellings to the notation accepted by this project, runs the Lambek
analysis on each annotated sequence, and writes per-piece comparison metrics.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any

import lambek_tree
from chord_grade import NOTE_TO_PC, chord_root


CADENCE_TYPES = (
    "perfect_authentic",
    "plagal",
    "ii_to_V",
    "tritone_sub",
    "backdoor",
    "deceptive",
    "descending_fifth",
)


def normalize_jht_chord(chord: str) -> str:
    """Convert common JHT chord spellings to this project's chord parser."""
    out = chord.strip().rstrip("*")
    out = out.replace("-", "b")
    out = out.replace("^", "maj")
    out = out.replace("%", "m7b5")
    out = out.replace("ø", "m7b5")
    out = re.sub(r"o7\b", "dim7", out)
    out = re.sub(r"o\b", "dim", out)
    return out


def _chord_pc(chord: str) -> int | None:
    try:
        return NOTE_TO_PC[chord_root(chord)]
    except ValueError:
        return None


def normalize_jht_key(key: str) -> tuple[str, str]:
    """Return (tonic, mode), where mode is M/m."""
    raw = key.strip()
    mode = "m" if raw and raw[0].islower() else "M"
    tonic = raw.replace("-", "b")
    tonic = tonic[0].upper() + tonic[1:] if tonic else tonic
    return tonic, mode


def key_signature(key: str) -> tuple[int | None, str]:
    tonic, mode = normalize_jht_key(key)
    try:
        return NOTE_TO_PC[chord_root(tonic)], mode
    except ValueError:
        return None, mode


def related_key_relation(a: str, b: str) -> str:
    """
    Return a musically close relation label for two key estimates.

    This is intentionally narrower than "any nearby tonal area": it treats as
    equivalent only exact matches, parallel keys, relative major/minor, and the
    common minor-to-flat-VI major relation (e.g. c minor and Ab major).
    """
    a_pc, a_mode = key_signature(a)
    b_pc, b_mode = key_signature(b)
    if a_pc is None or b_pc is None:
        return ""
    if a_pc == b_pc and a_mode == b_mode:
        return "exact"
    if a_pc == b_pc:
        return "parallel"
    if a_mode == "m" and b_mode == "M":
        if b_pc == (a_pc + 3) % 12:
            return "relative_major"
        if b_pc == (a_pc + 8) % 12:
            return "minor_flat_VI_major"
    if a_mode == "M" and b_mode == "m":
        if a_pc == (b_pc + 3) % 12:
            return "relative_minor"
        if a_pc == (b_pc + 8) % 12:
            return "major_flat_VI_of_minor"
    return ""


def related_key_match(a: str, b: str) -> int:
    return int(bool(related_key_relation(a, b)))


def _plain_latex_label(label: str) -> str:
    out = label
    out = re.sub(r"\\(?:textbf|mathbf|boldsymbol)\{([^{}]*)\}", r"\1", out)
    out = out.replace("$", "")
    out = out.replace("\\", "")
    out = out.replace("{", "").replace("}", "")
    return out.strip()


def inferred_key_path(tree_latex: str, initial_key: str) -> list[str]:
    keys = [initial_key]
    for raw in re.findall(r"\\LeftLabel\{(.*?)\}", tree_latex):
        label = _plain_latex_label(raw)
        if "=" not in label:
            continue
        key = label.rsplit("=", 1)[-1].strip()
        key = key.rstrip(":").strip()
        if key and key != keys[-1]:
            keys.append(key)
    return keys


def _cadence_target_key(source: str, target: str) -> str:
    source = normalize_jht_chord(source)
    target = normalize_jht_chord(target)
    label = classify_cadence(source, target)
    if not label:
        return ""
    if label == "ii_to_V":
        implied = lambek_tree._implied_tonic_from_dominant(target)
        if implied is None:
            return ""
        _ton, _note, key_label = implied
        return key_label
    if label in {"perfect_authentic", "plagal", "tritone_sub", "backdoor", "deceptive"}:
        _ton, _note, key_label = lambek_tree._chord_to_tonality(target)
        return key_label
    return ""


def cadence_events_with_keys(chords: list[str]) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    for source, target in zip(chords, chords[1:]):
        cadence_type = classify_cadence(source, target)
        key = _cadence_target_key(source, target)
        if cadence_type and key:
            events.append((cadence_type, key))
    return events


def final_cadential_key(chords: list[str]) -> str:
    for _cadence_type, key in reversed(cadence_events_with_keys(chords)):
        if key:
            return key
    return ""


def final_form_cadential_key(chords: list[str]) -> str:
    """
    Return the last complete cadence that resolves onto a stable tonic chord.

    This differs from `final_cadential_key`: a final ii--V turnaround may imply
    the next form's key without actually resolving. Here we prioritise the last
    authentic cadence whose target is a non-dominant chord in the written form.
    """
    fallback = ""
    for source, target in reversed(list(zip(chords, chords[1:]))):
        target = normalize_jht_chord(target)
        if lambek_tree._is_dominant_quality(target) or lambek_tree.is_half_diminished(target):
            continue
        cadence_type = classify_cadence(source, target)
        if not cadence_type:
            continue
        key = _cadence_target_key(source, target)
        if not key:
            continue
        if cadence_type == "perfect_authentic":
            return key
        if not fallback and cadence_type == "plagal":
            fallback = key
    return fallback


def opening_reference_key(chords: list[str]) -> str:
    """Infer a chart-level prior from the first stable, non-dominant chord."""
    if not chords:
        return ""
    first = normalize_jht_chord(chords[0])
    if lambek_tree._is_dominant_quality(first):
        return ""
    _tonality, _note, key_label = lambek_tree._chord_to_tonality(first)
    return key_label


def ending_reference_key(chords: list[str]) -> str:
    """Infer the last explicit stable tonic before a possible turnaround."""
    for raw in reversed(chords):
        chord = normalize_jht_chord(raw)
        if lambek_tree._is_dominant_quality(chord) or lambek_tree.is_half_diminished(chord):
            continue
        _tonality, _note, key_label = lambek_tree._chord_to_tonality(chord)
        return key_label
    return ""


def is_final_turnaround(chords: list[str], opening_key: str, closure_key: str) -> bool:
    """
    Detect a likely final turnaround rather than a form-level closing cadence.

    The signal is intentionally simple: a dominant-like final chord or a final
    unresolved dominant region means the last complete cadence may point to a
    local turnaround rather than to the global key.
    """
    if not chords or not opening_key or not closure_key or opening_key == closure_key:
        return False
    final_chord = normalize_jht_chord(chords[-1])
    if lambek_tree._is_dominant_quality(final_chord):
        return True
    final_window = [normalize_jht_chord(ch) for ch in chords[-4:]]
    dominant_count = sum(1 for ch in final_window if lambek_tree._is_dominant_quality(ch))
    return dominant_count >= 2 and closure_key != opening_key


def relative_major_key(minor_key: str, candidates: list[str]) -> str:
    minor_pc, minor_mode = key_signature(minor_key)
    if minor_pc is None or minor_mode != "m":
        return ""
    relative_pc = (minor_pc + 3) % 12
    for candidate in candidates:
        cand_pc, cand_mode = key_signature(candidate)
        if cand_pc == relative_pc and cand_mode == "M":
            return candidate
    return ""


def is_subdominant_of(key: str, tonic_key: str) -> bool:
    key_pc, key_mode = key_signature(key)
    tonic_pc, _tonic_mode = key_signature(tonic_key)
    if key_pc is None or tonic_pc is None or key_mode != "M":
        return False
    return key_pc == (tonic_pc + 5) % 12


def cadential_key_counts(chords: list[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for _cadence_type, key in cadence_events_with_keys(chords):
        counts[key] += 1
    return counts


def most_common_cadential_key(chords: list[str]) -> tuple[str, int, str]:
    counts = cadential_key_counts(chords)
    if not counts:
        return "", 0, ""
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    key, count = ranked[0]
    detail = "; ".join(f"{k}:{v}" for k, v in ranked)
    return key, count, detail


def representative_cadential_key(chords: list[str]) -> tuple[str, str]:
    """
    Choose a form-level key estimate from cadence counts.

    The modal cadential key is useful, but jazz forms often distribute cadences
    across local tonicisations. When the final complete cadence is close to the
    modal count, prefer it as a proxy for form-level closure.
    """
    counts = cadential_key_counts(chords)
    if not counts:
        return "", "none"
    modal_key, modal_count, _detail = most_common_cadential_key(chords)
    closure_key = final_cadential_key(chords)
    if closure_key and counts[closure_key] >= max(1, modal_count - 1):
        return closure_key, "closure_tie_break"
    return modal_key, "modal"


def global_reference_key(
    chords: list[str],
    initial_key: str,
    final_key: str,
    closure_key: str,
) -> tuple[str, float, str, str, str, int]:
    """
    Estimate a global reference key by combining local and cadential evidence.

    This deliberately differs from the parser's operative local key. It is an
    evaluation-facing estimate intended to be compared with JHT's global key.
    """
    scores: Counter[str] = Counter()
    reasons: dict[str, list[str]] = {}

    def add(key: str, points: float, reason: str) -> None:
        if not key:
            return
        scores[key] += points
        reasons.setdefault(key, []).append(f"{reason}:{points:g}")

    events = cadence_events_with_keys(chords)
    event_counts: Counter[str] = Counter(key for _cadence_type, key in events)
    type_counts: dict[str, Counter[str]] = {}
    for cadence_type, key in events:
        type_counts.setdefault(key, Counter())[cadence_type] += 1

    modal_count = max(event_counts.values(), default=0)
    closure_count = event_counts.get(closure_key, 0)
    opening_key = opening_reference_key(chords)
    ending_key = ending_reference_key(chords)
    form_closure_key = final_form_cadential_key(chords)
    turnaround = is_final_turnaround(chords, opening_key, closure_key)
    modal_key = ""
    if event_counts:
        modal_key = sorted(event_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]

    # Initial/final labels are local parser states. They are useful priors, but
    # must not dominate the tune-level reference key through repeated cadences.
    opening_is_form_subdominant = is_subdominant_of(opening_key, form_closure_key)
    opening_prior = 1.5 if opening_is_form_subdominant else 4.0
    add(opening_key, opening_prior, "opening_chord_prior")
    if opening_is_form_subdominant:
        add(form_closure_key, 5.0, "last_stable_form_cadence")
    add(initial_key, 0.4, "initial_prior")
    add(final_key, 0.4, "final_label_prior")

    if closure_key:
        if turnaround:
            closure_bonus = 1.5
            reason = "turnaround_discounted_cadence"
        else:
            closure_bonus = 4.5 if closure_count >= max(1, modal_count - 2) else 2.0
            reason = "last_complete_cadence"
        add(closure_key, closure_bonus, reason)

    for key, count in event_counts.items():
        # Cap raw cadence counts: repeated local tonicisations should provide
        # confidence, but not linearly overwhelm a form-level closing cadence.
        add(key, min(float(count), 4.0), "cadence_count_capped")

        cadence_types = type_counts.get(key, Counter())
        pa_count = cadence_types.get("perfect_authentic", 0)
        plagal_count = cadence_types.get("plagal", 0)
        tritone_count = cadence_types.get("tritone_sub", 0)

        if pa_count:
            add(key, min(pa_count * 0.75, 2.25), "perfect_authentic_capped")
        if plagal_count:
            add(key, min(plagal_count * 0.5, 1.0), "plagal_capped")
        if tritone_count:
            add(key, min(tritone_count * 0.4, 0.8), "tritone_sub_capped")

    if ending_key and ending_key in {opening_key, closure_key}:
        add(ending_key, 3.5, "confirmed_ending_form_prior")

    if events:
        last_type, last_key = events[-1]
        if last_type == "perfect_authentic" and not turnaround:
            add(last_key, 1.0, "final_perfect_authentic")

    if not scores:
        return initial_key, 0.0, "fallback_initial", initial_key, "low", int(turnaround)

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    key, score = ranked[0]

    if opening_key and len(events) <= 2 and key != opening_key:
        opening_score = scores.get(opening_key, 0.0)
        if score - opening_score <= 4.5:
            key, score = opening_key, opening_score
            reasons.setdefault(key, []).append("sparse_opening_tie_break:0")

    if turnaround and opening_key and key != opening_key:
        opening_score = scores.get(opening_key, 0.0)
        if score - opening_score <= 1.0:
            key, score = opening_key, opening_score
            reasons.setdefault(key, []).append("turnaround_opening_tie_break:0")

    if turnaround and modal_key and key != modal_key:
        modal_score = scores.get(modal_key, 0.0)
        if score - modal_score <= 0.5:
            key, score = modal_key, modal_score
            reasons.setdefault(key, []).append("turnaround_modal_tie_break:0")

    if opening_key and opening_key != key:
        opening_pc, _opening_mode = key_signature(opening_key)
        key_pc, _key_mode = key_signature(key)
        opening_score = scores.get(opening_key, 0.0)
        if opening_pc == key_pc and score - opening_score <= 4.0:
            key, score = opening_key, opening_score
            reasons.setdefault(key, []).append("opening_mode_tie_break:0")

    ranked_keys = [k for k, _v in ranked]
    rel_major = relative_major_key(key, ranked_keys)
    if rel_major and (rel_major == closure_key or rel_major == opening_key):
        rel_score = scores.get(rel_major, 0.0)
        if score - rel_score <= 4.5:
            key, score = rel_major, rel_score
            reasons.setdefault(key, []).append("relative_major_tie_break:0")

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    top_score = ranked[0][1]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = top_score - second_score
    confidence = "high"
    if len(events) <= 2 or margin < 1.0 or turnaround:
        confidence = "low"
    elif margin < 2.5 or len(event_counts) >= 6:
        confidence = "medium"

    candidates = "; ".join(f"{k}:{v:g}" for k, v in ranked[:5])
    detail = "; ".join(f"{k}={v:g}({','.join(reasons.get(k, []))})" for k, v in ranked)
    return key, float(score), detail, candidates, confidence, int(turnaround)


def tree_leaves(tree: dict[str, Any]) -> list[str]:
    children = tree.get("children") or []
    if not children:
        return [normalize_jht_chord(str(tree.get("label", ""))).rstrip("*")]
    out: list[str] = []
    for child in children:
        out.extend(tree_leaves(child))
    return out


def collapse_adjacent_duplicates(items: list[str]) -> list[str]:
    if not items:
        return []
    out = [items[0]]
    for item in items[1:]:
        if item != out[-1]:
            out.append(item)
    return out


def lcs_length(a: list[str], b: list[str]) -> int:
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for item_a in a:
        cur = [0] * (len(b) + 1)
        for j, item_b in enumerate(b, start=1):
            if item_a == item_b:
                cur[j] = prev[j - 1] + 1
            else:
                cur[j] = max(prev[j], cur[j - 1])
        prev = cur
    return prev[-1]


def prf(overlap: int, predicted_total: int, reference_total: int) -> tuple[float, float, float]:
    precision = (overlap / predicted_total) if predicted_total else 0.0
    recall = (overlap / reference_total) if reference_total else 0.0
    f1 = (2.0 * precision * recall / (precision + recall)) if precision + recall else 0.0
    return precision, recall, f1


def tree_depth(tree: dict[str, Any]) -> int:
    children = tree.get("children") or []
    if not children:
        return 0
    return 1 + max(tree_depth(child) for child in children)


def tree_branching_count(tree: dict[str, Any]) -> int:
    children = tree.get("children") or []
    return (1 if len(children) >= 2 else 0) + sum(tree_branching_count(child) for child in children)


def classify_cadence(source: str, target: str) -> str:
    """
    Translate chord-to-chord motion into a shared cadence vocabulary.

    This is deliberately conservative: it only emits labels for relationships
    both systems can express through chord roots and common jazz chord quality.
    """
    source = normalize_jht_chord(source)
    target = normalize_jht_chord(target)
    source_pc = _chord_pc(source)
    target_pc = _chord_pc(target)
    if source_pc is None or target_pc is None:
        return ""

    interval = (source_pc - target_pc) % 12
    source_is_dom = lambek_tree._is_dominant_quality(source)
    target_is_dom = lambek_tree._is_dominant_quality(target)
    source_is_minor_predom = lambek_tree._is_minor_seventh_family(source)

    if source_is_dom and interval == 7:
        return "perfect_authentic"
    if source_is_dom and interval == 1:
        return "tritone_sub"
    if source_is_minor_predom and target_is_dom and interval == 7:
        return "ii_to_V"
    if source_is_dom and interval == 10 and lambek_tree.is_minor_chord(target):
        return "deceptive"
    if source_is_dom and interval == 10:
        return "backdoor"
    if not source_is_dom and interval == 5:
        return "plagal"
    if interval == 7:
        return "descending_fifth"
    return ""


def system_cadence_counts(chords: list[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for source, target in zip(chords, chords[1:]):
        label = classify_cadence(source, target)
        if label:
            counts[label] += 1
    return counts


def jht_cadence_counts(tree: dict[str, Any]) -> Counter[str]:
    """
    Extract cadence-like dependency events from JHT constituent trees.

    JHT internal node labels represent harmonic heads. For each internal node,
    each non-identical child label is interpreted as a dependent resolving or
    relating to that head. This maps the treebank into the same vocabulary used
    for this project's sequential analysis.
    """
    counts: Counter[str] = Counter()
    target = normalize_jht_chord(str(tree.get("label", "")))
    children = tree.get("children") or []

    for child in children:
        source = normalize_jht_chord(str(child.get("label", "")))
        if source != target:
            label = classify_cadence(source, target)
            if label:
                counts[label] += 1
        counts.update(jht_cadence_counts(child))

    return counts


def counter_overlap(a: Counter[str], b: Counter[str]) -> int:
    return sum(min(a[k], b[k]) for k in set(a) | set(b))


def f1_score(overlap: int, predicted_total: int, reference_total: int) -> tuple[float, float, float]:
    return prf(overlap, predicted_total, reference_total)


def lambek_depth_from_tree(tree_latex: str) -> int:
    depths = [int(x) for x in re.findall(r"\\sststile\{(\d+)\}\{\}", tree_latex)]
    return max(depths) if depths else 0


def load_jht(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Expected JHT treebank.json to contain a top-level list.")
    return data


def evaluate_piece(piece: dict[str, Any]) -> list[dict[str, Any]]:
    title = str(piece.get("title", ""))
    key = str(piece.get("key", ""))
    raw_chords = [str(c) for c in piece.get("chords", [])]
    chords = [normalize_jht_chord(c) for c in raw_chords]
    trees = piece.get("trees") or []

    if not chords or not trees:
        return []

    rows: list[dict[str, Any]] = []
    for tree_idx, tree_payload in enumerate(trees, start=1):
        ref_tree = tree_payload.get("open_constituent_tree") or tree_payload.get("complete_constituent_tree")
        if not isinstance(ref_tree, dict):
            continue

        ref_leaves = tree_leaves(ref_tree)
        tree_latex, inferred_key = lambek_tree.generate_auto_tree(chords)
        key_path = inferred_key_path(tree_latex, inferred_key)
        final_key = key_path[-1] if key_path else inferred_key
        closure_key = final_cadential_key(chords) or final_key
        modal_cadential_key, modal_cadential_key_count, cadential_key_distribution = most_common_cadential_key(chords)
        modal_cadential_key = modal_cadential_key or closure_key
        representative_key, representative_key_strategy = representative_cadential_key(chords)
        representative_key = representative_key or closure_key
        (
            global_key,
            global_key_score,
            global_key_evidence,
            global_key_candidates,
            global_key_confidence,
            final_turnaround_detected,
        ) = global_reference_key(
            chords,
            inferred_key,
            final_key,
            closure_key,
        )
        lambek_depth = lambek_depth_from_tree(tree_latex)
        ref_key_sig = key_signature(key)
        inferred_key_sig = key_signature(inferred_key)
        final_key_sig = key_signature(final_key)
        closure_key_sig = key_signature(closure_key)
        modal_cadential_key_sig = key_signature(modal_cadential_key)
        representative_key_sig = key_signature(representative_key)
        global_key_sig = key_signature(global_key)
        comparable_leaves = min(len(ref_leaves), len(chords))
        leaf_matches = sum(1 for a, b in zip(ref_leaves, chords) if a == b)
        compressed_ref_leaves = collapse_adjacent_duplicates(ref_leaves)
        compressed_chords = collapse_adjacent_duplicates(chords)
        compressed_comparable = min(len(compressed_ref_leaves), len(compressed_chords))
        compressed_leaf_matches = sum(1 for a, b in zip(compressed_ref_leaves, compressed_chords) if a == b)
        leaf_lcs = lcs_length(ref_leaves, chords)
        leaf_lcs_precision, leaf_lcs_recall, leaf_lcs_f1 = prf(leaf_lcs, len(chords), len(ref_leaves))
        compressed_lcs = lcs_length(compressed_ref_leaves, compressed_chords)
        compressed_lcs_precision, compressed_lcs_recall, compressed_lcs_f1 = prf(
            compressed_lcs,
            len(compressed_chords),
            len(compressed_ref_leaves),
        )
        ref_cadences = jht_cadence_counts(ref_tree)
        sys_cadences = system_cadence_counts(chords)
        cadence_overlap = counter_overlap(sys_cadences, ref_cadences)
        cadence_precision, cadence_recall, cadence_f1 = f1_score(
            cadence_overlap,
            sum(sys_cadences.values()),
            sum(ref_cadences.values()),
        )

        row: dict[str, Any] = {
            "title": title,
            "tree_index": tree_idx,
            "key": key,
            "inferred_key": inferred_key,
            "key_match": int(ref_key_sig == inferred_key_sig),
            "final_inferred_key": final_key,
            "final_key_match": int(ref_key_sig == final_key_sig),
            "cadential_closure_key": closure_key,
            "cadential_closure_key_match": int(ref_key_sig == closure_key_sig),
            "modal_cadential_key": modal_cadential_key,
            "modal_cadential_key_count": modal_cadential_key_count,
            "modal_cadential_key_match": int(ref_key_sig == modal_cadential_key_sig),
            "representative_cadential_key": representative_key,
            "representative_cadential_key_match": int(ref_key_sig == representative_key_sig),
            "representative_cadential_key_strategy": representative_key_strategy,
            "global_reference_key": global_key,
            "global_reference_key_score": global_key_score,
            "global_reference_key_match": int(ref_key_sig == global_key_sig),
            "global_reference_key_related_match": related_key_match(key, global_key),
            "global_reference_key_relation": related_key_relation(key, global_key),
            "global_reference_key_evidence": global_key_evidence,
            "global_reference_key_candidates": global_key_candidates,
            "global_reference_key_confidence": global_key_confidence,
            "final_turnaround_detected": final_turnaround_detected,
            "cadential_key_distribution": cadential_key_distribution,
            "inferred_key_path": " > ".join(key_path),
            "chords": len(chords),
            "jht_leaves": len(ref_leaves),
            "leaf_count_match": int(len(chords) == len(ref_leaves)),
            "leaf_sequence_accuracy": (leaf_matches / comparable_leaves) if comparable_leaves else 0.0,
            "compressed_chords": len(compressed_chords),
            "compressed_jht_leaves": len(compressed_ref_leaves),
            "compressed_leaf_count_match": int(len(compressed_chords) == len(compressed_ref_leaves)),
            "compressed_leaf_sequence_accuracy": (
                compressed_leaf_matches / compressed_comparable if compressed_comparable else 0.0
            ),
            "leaf_lcs_precision": leaf_lcs_precision,
            "leaf_lcs_recall": leaf_lcs_recall,
            "leaf_lcs_f1": leaf_lcs_f1,
            "compressed_leaf_lcs_precision": compressed_lcs_precision,
            "compressed_leaf_lcs_recall": compressed_lcs_recall,
            "compressed_leaf_lcs_f1": compressed_lcs_f1,
            "jht_depth": tree_depth(ref_tree),
            "lambek_depth": lambek_depth,
            "depth_difference": lambek_depth - tree_depth(ref_tree),
            "jht_branching_nodes": tree_branching_count(ref_tree),
            "jht_cadence_events": sum(ref_cadences.values()),
            "lambek_cadence_events": sum(sys_cadences.values()),
            "cadence_overlap": cadence_overlap,
            "cadence_precision": cadence_precision,
            "cadence_recall": cadence_recall,
            "cadence_f1": cadence_f1,
        }
        for cadence_type in CADENCE_TYPES:
            row[f"jht_{cadence_type}"] = ref_cadences[cadence_type]
            row[f"lambek_{cadence_type}"] = sys_cadences[cadence_type]
        rows.append(row)
    return rows


def write_summary(rows: list[dict[str, Any]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "title",
        "tree_index",
        "key",
        "inferred_key",
        "key_match",
        "final_inferred_key",
        "final_key_match",
        "cadential_closure_key",
        "cadential_closure_key_match",
        "modal_cadential_key",
        "modal_cadential_key_count",
        "modal_cadential_key_match",
        "representative_cadential_key",
        "representative_cadential_key_match",
        "representative_cadential_key_strategy",
        "global_reference_key",
        "global_reference_key_score",
        "global_reference_key_match",
        "global_reference_key_related_match",
        "global_reference_key_relation",
        "global_reference_key_evidence",
        "global_reference_key_candidates",
        "global_reference_key_confidence",
        "final_turnaround_detected",
        "cadential_key_distribution",
        "inferred_key_path",
        "chords",
        "jht_leaves",
        "leaf_count_match",
        "leaf_sequence_accuracy",
        "compressed_chords",
        "compressed_jht_leaves",
        "compressed_leaf_count_match",
        "compressed_leaf_sequence_accuracy",
        "leaf_lcs_precision",
        "leaf_lcs_recall",
        "leaf_lcs_f1",
        "compressed_leaf_lcs_precision",
        "compressed_leaf_lcs_recall",
        "compressed_leaf_lcs_f1",
        "jht_depth",
        "lambek_depth",
        "depth_difference",
        "jht_branching_nodes",
        "jht_cadence_events",
        "lambek_cadence_events",
        "cadence_overlap",
        "cadence_precision",
        "cadence_recall",
        "cadence_f1",
    ]
    for cadence_type in CADENCE_TYPES:
        fields.append(f"jht_{cadence_type}")
        fields.append(f"lambek_{cadence_type}")
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Lambek analyses with Jazz Harmony Treebank annotations.")
    parser.add_argument("--jht-json", required=True, help="Path to JHT treebank.json.")
    parser.add_argument("--out", default="jht_evaluation/jht_eval.csv", help="CSV output path.")
    parser.add_argument("--limit", type=int, default=0, help="Optional maximum number of JHT pieces to inspect.")
    args = parser.parse_args()

    jht_path = Path(args.jht_json)
    if not jht_path.is_absolute():
        jht_path = Path(__file__).resolve().parent / jht_path
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = Path(__file__).resolve().parent / out_path

    pieces = load_jht(jht_path)
    if args.limit > 0:
        pieces = pieces[: args.limit]

    rows: list[dict[str, Any]] = []
    skipped = 0
    for piece in pieces:
        try:
            rows.extend(evaluate_piece(piece))
        except Exception:
            skipped += 1

    write_summary(rows, out_path)
    print(f"JHT pieces inspected: {len(pieces)}")
    print(f"Comparable annotated trees: {len(rows)}")
    print(f"Skipped pieces: {skipped}")
    print(f"CSV written to: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
