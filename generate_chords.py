#!/usr/bin/env python3
"""
Generate chord trees from a starting tonality using weighted harmonic rules.

The generator mixes:
1) user-controlled symbolic rules (Perfect, Plagal, ii-V, Relative, Parallel),
2) dataset priors from cadence statistics CSV,
3) stochastic sampling with temperature,
4) optional modal key moves (D/S/R/P) applied before branching.

Example:
  python3 generate_chords.py --tonality C --target-chords 8 --max-depth 5
  python3 generate_chords.py --tonality g --target-chords 12 --style-strength 0.8 --temperature 0.7
  python3 generate_chords.py --tonality C --target-chords 10 --stats-csv last_line_analysis/JazzStandards_all_cadence.csv
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
from typing import Iterable

from tonality_route import Tonality, apply_op, parse_tonality, shortest_path, tonality_to_str


PC_TO_NAME_FLAT = {
    0: "C",
    1: "Db",
    2: "D",
    3: "Eb",
    4: "E",
    5: "F",
    6: "Gb",
    7: "G",
    8: "Ab",
    9: "A",
    10: "Bb",
    11: "B",
}

ROMAN_INTERVALS = {
    "I": 0,
    "i": 0,
    "bII": 1,
    "II": 2,
    "ii": 2,
    "bIII": 3,
    "III": 4,
    "iii": 4,
    "IV": 5,
    "iv": 5,
    "#IV": 6,
    "V": 7,
    "v": 7,
    "bVI": 8,
    "VI": 9,
    "vi": 9,
    "bVII": 10,
    "VII": 11,
    "#VII": 11,
}


@dataclass(frozen=True)
class Rule:
    name: str
    label: str
    target_degree: str
    new_degree: str
    base_weight: float = 1.0


RULES: tuple[Rule, ...] = (
    Rule("perfect_major", "P.C.", "I", "V", 1.2),
    Rule("perfect_minor", "P.C.m.", "i", "V", 1.1),
    Rule("plagal_major", "Pl.C.M.", "I", "IV", 0.9),
    Rule("plagal_minor_to_major", "Pl.C.m.", "I", "iv", 0.8),
    Rule("plagal_minor", "Pl.C.m.", "i", "iv", 0.8),
    Rule("ii_v_major", "ii-V", "V", "ii", 1.1),
    Rule("ii_v_minor", "ii-V", "V", "ii", 1.0),
    Rule("relative_branch_major", "R.b.", "I", "vi", 0.6),
    Rule("relative_branch_minor", "R.b.", "i", "III", 0.6),
    Rule("parallel_branch_major", "P.b.", "I", "i", 0.35),
    Rule("parallel_branch_minor", "P.b.", "i", "I", 0.35),
)


COMMON_BASIC_PROGRESSIONS: tuple[tuple[str, ...], ...] = (
    ("I", "V", "vi", "IV"),
    ("I", "IV", "V", "I"),
    ("I", "vi", "ii", "V"),
    ("I", "ii", "IV", "V"),
    ("vi", "IV", "I", "V"),
    ("i", "iv", "V", "i"),
    ("ii", "V", "I", "I"),
    ("I", "vi", "ii", "V"),
    ("I", "I", "bIII", "IV"),
    ("I", "III", "IV", "iv"),
    ("ii", "I", "V", "bVII"),
    ("V", "V", "IV", "I"),
    ("I", "I", "I", "I", "IV", "IV", "I", "I", "V", "IV", "I", "I"),
)


def _build_basic_link_set() -> set[tuple[str, str]]:
    links: set[tuple[str, str]] = set()
    for seq in COMMON_BASIC_PROGRESSIONS:
        for a, b in zip(seq, seq[1:]):
            na = _normalize_degree(a)
            nb = _normalize_degree(b)
            if na is None or nb is None:
                continue
            links.add((na, nb))
    return links


def _is_perfect_or_plagal(rule: Rule) -> bool:
    return rule.name.startswith("perfect_") or rule.name.startswith("plagal_")


@dataclass
class Node:
    degree: str
    chord: str
    depth: int
    key_pc: int
    key_mode: str
    left: "Node | None" = None
    right: "Node | None" = None
    via_rule: str = ""
    arrival_modal: str = ""
    generated: bool = True
    id: int = field(default=0)

    @property
    def expandable(self) -> bool:
        return self.generated and self.left is None and self.right is None

    @property
    def display_label(self) -> str:
        return f"{self.degree} [{self.chord}] @{_key_str(self.key_pc, self.key_mode)}"


def _key_str(pc: int, mode: str) -> str:
    return tonality_to_str(Tonality(pc=pc, mode=mode), "flats")


def _normalize_degree(token: str) -> str | None:
    t = (token or "").strip()
    if not t:
        return None
    if t in ROMAN_INTERVALS:
        return t

    m = re.match(r"^([#b]?)([IViv]+)$", t)
    if not m:
        return None
    acc, roman = m.group(1), m.group(2)

    cand_up = f"{acc}{roman.upper()}"
    if cand_up in ROMAN_INTERVALS:
        return cand_up

    cand_low = f"{acc}{roman.lower()}"
    if cand_low in ROMAN_INTERVALS:
        return cand_low

    return None


BASIC_LINKS = _build_basic_link_set()


def _normalize_target_for_mode(degree: str, mode: str) -> str:
    if degree in {"I", "i"}:
        return "i" if mode == "m" else "I"
    return degree


def _roman_to_chord(key_pc: int, degree: str) -> str:
    degree = _normalize_degree(degree)
    if degree is None or degree not in ROMAN_INTERVALS:
        raise ValueError(f"Unsupported degree for generation: {degree}")

    pc = (key_pc + ROMAN_INTERVALS[degree]) % 12
    root = PC_TO_NAME_FLAT[pc]

    # Lightweight quality defaults for readability.
    if degree in {"V", "v", "bII", "II"}:
        quality = "7"
    elif degree and degree[0].islower():
        quality = "m7"
    else:
        quality = "maj7"
    return f"{root}{quality}"


def _load_link_probs(stats_csv: Path) -> tuple[dict[tuple[str, str], float], dict[str, dict[str, float]]]:
    if not stats_csv.exists():
        return {}, {}

    out_counts: dict[str, int] = {}
    in_counts: dict[str, int] = {}
    pair_counts: dict[tuple[str, str], int] = {}

    with stats_csv.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if row.get("category", "") != "single_link":
                continue
            pattern = row.get("pattern", "")
            m = re.match(r"\s*([#b]?[IViv]+)\s*->\s*([#b]?[IViv]+)\s*$", pattern)
            if not m:
                continue

            a = _normalize_degree(m.group(1))
            b = _normalize_degree(m.group(2))
            if a is None or b is None:
                continue

            c = int(float(row.get("count", "0") or 0))
            pair_counts[(a, b)] = pair_counts.get((a, b), 0) + c
            out_counts[a] = out_counts.get(a, 0) + c
            in_counts[b] = in_counts.get(b, 0) + c

    probs: dict[tuple[str, str], float] = {}
    for (a, b), c in pair_counts.items():
        tot = out_counts.get(a, 0)
        probs[(a, b)] = (c / tot) if tot else 0.0

    incoming_probs: dict[str, dict[str, float]] = {}
    for (a, b), c in pair_counts.items():
        tot_in = in_counts.get(b, 0)
        incoming_probs.setdefault(b, {})[a] = (c / tot_in) if tot_in else 0.0

    return probs, incoming_probs


def _mix_weight(rule: Rule, link_probs: dict[tuple[str, str], float], style_strength: float, temperature: float) -> float:
    base = rule.base_weight
    data_prob = link_probs.get((rule.new_degree, rule.target_degree), 0.0)
    mixed = (1.0 - style_strength) * base + style_strength * (1.0 + 8.0 * data_prob)
    mixed = max(mixed, 1e-6)

    t = max(temperature, 1e-4)
    return mixed ** (1.0 / t)


def _apply_basic_cadence_boost(weight: float, pair: tuple[str, str], basic_cadence_strength: float) -> float:
    if basic_cadence_strength <= 0.0:
        return weight
    if pair in BASIC_LINKS:
        # Strong emphasis toward very common textbook/pop transitions.
        return weight * (1.0 + 4.0 * basic_cadence_strength)
    return weight


def _sample_rule(
    rules: list[Rule],
    link_probs: dict[tuple[str, str], float],
    style_strength: float,
    temperature: float,
    basic_cadence_strength: float,
    rng: random.Random,
) -> Rule:
    weights = []
    for r in rules:
        w = _mix_weight(r, link_probs, style_strength, temperature)
        w = _apply_basic_cadence_boost(w, (r.new_degree, r.target_degree), basic_cadence_strength)
        weights.append(w)
    total = sum(weights)
    pick = rng.random() * total
    acc = 0.0
    for r, w in zip(rules, weights):
        acc += w
        if pick <= acc:
            return r
    return rules[-1]


def _data_fallback_rules(target_degree: str, incoming_probs: dict[str, dict[str, float]], existing_pairs: set[tuple[str, str]]) -> list[Rule]:
    out: list[Rule] = []
    incoming = incoming_probs.get(target_degree, {})
    if not incoming:
        return out

    # Keep top incoming transitions for stability/readability.
    ranked = sorted(incoming.items(), key=lambda kv: kv[1], reverse=True)[:8]
    for src, p in ranked:
        pair = (src, target_degree)
        if pair in existing_pairs:
            continue
        out.append(
            Rule(
                name=f"data_{src}_{target_degree}",
                label=f"Data({src}->{target_degree})",
                target_degree=target_degree,
                new_degree=src,
                base_weight=0.25 + 2.0 * p,
            )
        )
    return out


def _sample_modal_op(rng: random.Random, complexity: float, previous_modal: str = "") -> str:
    # Low complexity => D/S preferred; high complexity => R/P preferred.
    ops = ["D", "S", "R", "P"]
    weights = {
        "D": max(0.05, 1.35 - 0.75 * complexity),
        "S": max(0.05, 1.15 - 0.60 * complexity),
        "R": max(0.05, 0.20 + 1.30 * complexity),
        "P": max(0.05, 0.20 + 1.10 * complexity),
    }

    inverse = {"D": "S", "S": "D", "R": "R", "P": "P"}
    banned = inverse.get(previous_modal, "")
    pool = [op for op in ops if op != banned]
    if not pool:
        pool = ops[:]

    total = sum(weights[op] for op in pool)
    pick = rng.random() * total
    acc = 0.0
    for op in pool:
        acc += weights[op]
        if pick <= acc:
            return op
    return pool[-1]


def _tonality_distance(a: Tonality, b: Tonality) -> int:
    if a == b:
        return 0
    _nodes, ops = shortest_path(a, b)
    return len(ops)


def _sample_modal_op_with_drift(
    current_ton: Tonality,
    origin_ton: Tonality,
    rng: random.Random,
    complexity: float,
    tonal_drift: float,
    previous_modal: str = "",
) -> str:
    """
    Sample one modal op while biasing toward/away from the original tonality.
    tonal_drift in [0,1]:
      0 => strong stay-near-origin bias (far moves strongly suppressed)
      1 => no distance penalty (free modulation)
    """
    ops = ["D", "S", "R", "P"]
    weights = {
        "D": max(0.05, 1.35 - 0.75 * complexity),
        "S": max(0.05, 1.15 - 0.60 * complexity),
        "R": max(0.05, 0.20 + 1.30 * complexity),
        "P": max(0.05, 0.20 + 1.10 * complexity),
    }

    inverse = {"D": "S", "S": "D", "R": "R", "P": "P"}
    banned = inverse.get(previous_modal, "")
    pool = [op for op in ops if op != banned]
    if not pool:
        pool = ops[:]

    drift = max(0.0, min(1.0, tonal_drift))
    scored: list[tuple[str, float]] = []
    for op in pool:
        tnext = apply_op(current_ton, op)
        dist = _tonality_distance(origin_ton, tnext)
        # Distance penalty: low drift heavily penalizes far keys.
        distance_factor = 1.0 if dist == 0 else (drift ** dist)
        w = max(1e-6, weights[op] * distance_factor)
        scored.append((op, w))

    total = sum(w for _, w in scored)
    pick = rng.random() * total
    acc = 0.0
    for op, w in scored:
        acc += w
        if pick <= acc:
            return op
    return scored[-1][0]


def _iter_expandable(root: Node) -> Iterable[Node]:
    stack = [root]
    while stack:
        cur = stack.pop()
        if cur.expandable:
            yield cur
        if cur.right is not None:
            stack.append(cur.right)
        if cur.left is not None:
            stack.append(cur.left)


def _ascii_tree(node: Node, prefix: str = "", is_last: bool = True) -> list[str]:
    connector = "└─ " if is_last else "├─ "
    line = f"{prefix}{connector}{node.display_label}"
    if node.via_rule:
        line += f"  [{node.via_rule}]"
    out = [line]
    children = [c for c in (node.left, node.right) if c is not None]
    for i, ch in enumerate(children):
        child_last = i == len(children) - 1
        child_prefix = prefix + ("   " if is_last else "│  ")
        out.extend(_ascii_tree(ch, child_prefix, child_last))
    return out


def _forest_tex(node: Node) -> str:
    if node.left is None and node.right is None:
        return f"[{node.display_label}]"
    left = _forest_tex(node.left) if node.left else ""
    right = _forest_tex(node.right) if node.right else ""
    edge = node.left.via_rule if node.left else ""
    return (
        f"[{node.display_label} "
        f"{left}, edge label={{node[midway, left,font=\\tiny]{{\\textit{{{edge}}}}}}} "
        f"{right}]"
    )


def _dot_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _tree_to_dot(root: Node) -> str:
    lines: list[str] = []
    lines.append("digraph ChordTree {")
    lines.append('  rankdir=TB;')
    lines.append('  graph [splines=true, overlap=false];')
    lines.append('  node [shape=box, style="rounded,filled", fillcolor="#fff8e1", color="#6b4f1d", fontname="Helvetica", fontsize=11];')
    lines.append('  edge [fontname="Helvetica", fontsize=10, color="#444444"];')

    stack = [root]
    while stack:
        cur = stack.pop()
        nid = f"n{cur.id}"
        label = _dot_escape(cur.display_label)
        fill = "#fff8e1" if cur.generated else "#f2f2f2"
        lines.append(f'  {nid} [label="{label}", fillcolor="{fill}"];')

        if cur.left is not None:
            lid = f"n{cur.left.id}"
            elabel = _dot_escape(cur.left.via_rule or "")
            lines.append(f'  {nid} -> {lid} [label="{elabel}", color="#1f6feb", fontcolor="#1f6feb", penwidth=1.6];')
            stack.append(cur.left)
        if cur.right is not None:
            rid = f"n{cur.right.id}"
            lines.append(f'  {nid} -> {rid} [style=dashed, color="#9aa0a6"];')
            stack.append(cur.right)

    lines.append("}")
    return "\n".join(lines)


def _render_png_from_dot(dot_source: str, out_png: Path, dpi: int) -> tuple[bool, str]:
    dot_bin = shutil.which("dot")
    if not dot_bin:
        return False, "Graphviz 'dot' not found: PNG not generated."
    dpi = max(72, int(dpi))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [dot_bin, "-Tpng:cairo", f"-Gdpi={dpi}", "-o", str(out_png)],
            input=dot_source.encode("utf-8"),
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        return False, f"Graphviz failed: {exc}"
    return True, ""


def _collect_generated_nodes(root: Node) -> list[Node]:
    out: list[Node] = []
    stack = [root]
    while stack:
        cur = stack.pop()
        if cur.generated:
            out.append(cur)
        if cur.right is not None:
            stack.append(cur.right)
        if cur.left is not None:
            stack.append(cur.left)
    return out


def _collect_leaf_chords_left_to_right(node: Node) -> list[str]:
    if node.left is None and node.right is None:
        return [node.chord]
    out: list[str] = []
    if node.left is not None:
        out.extend(_collect_leaf_chords_left_to_right(node.left))
    if node.right is not None:
        out.extend(_collect_leaf_chords_left_to_right(node.right))
    return out


def _default_stats_csv() -> Path:
    return Path(__file__).resolve().parent / "last_line_analysis" / "JazzStandards_all_cadence.csv"


def _next_available_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    i = 1
    while True:
        cand = path.parent / f"{stem}_{i}{suffix}"
        if not cand.exists():
            return cand
        i += 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate chord trees from a tonality using weighted harmonic rules."
    )
    parser.add_argument("--tonality", required=True, help="Starting tonality, e.g. C, g, Eb, d.")
    parser.add_argument("--target-chords", type=int, default=8, help="Target number of generated chords (default: 8).")
    parser.add_argument("--max-depth", type=int, default=64, help="Maximum branching depth (default: 64).")
    parser.add_argument(
        "--branching-mode",
        choices=["left", "mixed"],
        default="left",
        help="Branching strategy: left (deterministic chain) or mixed (random expandable node).",
    )
    parser.add_argument(
        "--style-strength",
        type=float,
        default=0.75,
        help="0..1, how strongly to follow dataset cadence statistics (default: 0.75).",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.9,
        help="Sampling temperature (>0). Lower = more deterministic (default: 0.9).",
    )
    parser.add_argument(
        "--modulation-strength",
        type=float,
        default=0.25,
        help="0..1 probability of applying a modal key move before each branching step (default: 0.25).",
    )
    parser.add_argument(
        "--modulation-complexity",
        type=float,
        default=0.5,
        help="0..1 modal preference: low favors D/S, high favors R/P (default: 0.5).",
    )
    parser.add_argument(
        "--tonal-drift",
        type=float,
        default=0.5,
        help="0..1 distance from original tonality: 0 stays near origin, 1 allows far modulation (default: 0.5).",
    )
    parser.add_argument(
        "--stats-csv",
        default="",
        help="Cadence stats CSV path (default: last_line_analysis/JazzStandards_all_cadence.csv).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Optional random seed (0 = random from system).",
    )
    parser.add_argument("--out-dir", default="generated_chords", help="Output directory for reports.")
    parser.add_argument("--basename", default="", help="Optional base output name.")
    parser.add_argument("--png", action="store_true", help="Also render a tree PNG with Graphviz.")
    parser.add_argument("--png-dpi", type=int, default=900, help="PNG DPI for --png output (default: 900).")
    parser.add_argument(
        "--initial-cadence-bias",
        type=float,
        default=0.9,
        help="0..1 probability that first generated cadence is forced to perfect/plagal (default: 0.9).",
    )
    parser.add_argument(
        "--basic-cadence-strength",
        type=float,
        default=0.0,
        help="0..1 boost for very common cadential links from standard progressions (default: 0.0).",
    )
    parser.add_argument(
        "--basic-cadence-mode",
        action="store_true",
        help="Preset for simpler cadential output (equivalent to --initial-cadence-bias 1.0 --basic-cadence-strength 0.8 unless explicitly overridden).",
    )
    args = parser.parse_args()

    if args.target_chords < 1:
        raise ValueError("--target-chords must be >= 1")
    if args.max_depth < 0:
        raise ValueError("--max-depth must be >= 0")
    if not (0.0 <= args.style_strength <= 1.0):
        raise ValueError("--style-strength must be in [0,1]")
    if args.temperature <= 0:
        raise ValueError("--temperature must be > 0")
    if not (0.0 <= args.modulation_strength <= 1.0):
        raise ValueError("--modulation-strength must be in [0,1]")
    if not (0.0 <= args.modulation_complexity <= 1.0):
        raise ValueError("--modulation-complexity must be in [0,1]")
    if not (0.0 <= args.tonal_drift <= 1.0):
        raise ValueError("--tonal-drift must be in [0,1]")
    if not (0.0 <= args.initial_cadence_bias <= 1.0):
        raise ValueError("--initial-cadence-bias must be in [0,1]")
    if not (0.0 <= args.basic_cadence_strength <= 1.0):
        raise ValueError("--basic-cadence-strength must be in [0,1]")

    cli_args = set(sys.argv[1:])
    if args.basic_cadence_mode:
        # Preset defaults only if user did not explicitly pass these options.
        if "--initial-cadence-bias" not in cli_args:
            args.initial_cadence_bias = 1.0
        if "--basic-cadence-strength" not in cli_args:
            args.basic_cadence_strength = 0.8

    ton = parse_tonality(args.tonality)
    origin_ton = Tonality(pc=ton.pc, mode=ton.mode)
    root_degree = "i" if ton.mode == "m" else "I"
    root = Node(
        degree=root_degree,
        chord=_roman_to_chord(ton.pc, root_degree),
        depth=0,
        key_pc=ton.pc,
        key_mode=ton.mode,
        id=1,
    )
    next_id = 2

    rng = random.Random(args.seed) if args.seed else random.Random()

    stats_csv = Path(args.stats_csv) if args.stats_csv else _default_stats_csv()
    if not stats_csv.is_absolute():
        stats_csv = Path(__file__).resolve().parent / stats_csv
    link_probs, incoming_probs = _load_link_probs(stats_csv)

    rule_trace: list[str] = []
    generated_count = 1
    frontier = root
    step_idx = 0

    stop_reason = ""
    while generated_count < args.target_chords:
        if args.branching_mode == "left":
            candidates = [frontier] if frontier.expandable and frontier.depth < args.max_depth else []
        else:
            candidates = [n for n in _iter_expandable(root) if n.depth < args.max_depth]

        if not candidates:
            stop_reason = f"no expandable nodes available under max-depth={args.max_depth}"
            break

        node = candidates[0] if args.branching_mode == "left" else rng.choice(candidates)

        # Optional modal key move before applying cadence rule.
        modal_op = ""
        local_pc = node.key_pc
        local_mode = node.key_mode
        # Keep the final resolution anchored to the chosen tonality:
        # first split must stay in the original key.
        if step_idx == 0:
            effective_modulation_strength = 0.0
        else:
            effective_modulation_strength = args.modulation_strength * args.tonal_drift
        if rng.random() < effective_modulation_strength:
            modal_op = _sample_modal_op_with_drift(
                current_ton=Tonality(pc=node.key_pc, mode=node.key_mode),
                origin_ton=origin_ton,
                rng=rng,
                complexity=args.modulation_complexity,
                tonal_drift=args.tonal_drift,
                previous_modal=node.arrival_modal,
            )
            tnext = apply_op(Tonality(pc=node.key_pc, mode=node.key_mode), modal_op)
            local_pc = tnext.pc
            local_mode = tnext.mode

        target_degree = _normalize_target_for_mode(node.degree, local_mode)

        applicable = [r for r in RULES if r.target_degree == target_degree]
        existing_pairs = {(r.new_degree, r.target_degree) for r in applicable}
        applicable.extend(_data_fallback_rules(target_degree, incoming_probs, existing_pairs))
        if not applicable:
            stop_reason = f"no applicable rules found for local target degree '{target_degree}'"
            break

        chosen_pool = applicable
        if step_idx == 0:
            # Enforce a strict cadential closure at the global tonic:
            # the root expansion must be perfect/plagal when available.
            cadential = [r for r in applicable if _is_perfect_or_plagal(r)]
            if cadential:
                chosen_pool = cadential

        chosen = _sample_rule(
            chosen_pool,
            link_probs=link_probs,
            style_strength=args.style_strength,
            temperature=args.temperature,
            basic_cadence_strength=args.basic_cadence_strength,
            rng=rng,
        )

        left_degree = chosen.new_degree
        left = Node(
            degree=left_degree,
            chord=_roman_to_chord(local_pc, left_degree),
            depth=node.depth + 1,
            key_pc=local_pc,
            key_mode=local_mode,
            via_rule=(f"R_{modal_op} + {chosen.label}" if modal_op else chosen.label),
            arrival_modal=modal_op,
            generated=True,
            id=next_id,
        )
        next_id += 1

        right = Node(
            degree=target_degree,
            chord=_roman_to_chord(local_pc, target_degree),
            depth=node.depth + 1,
            key_pc=local_pc,
            key_mode=local_mode,
            arrival_modal=modal_op,
            generated=False,
            id=next_id,
        )
        next_id += 1

        node.left = left
        node.right = right

        if modal_op:
            rule_trace.append(
                f"R_{modal_op}: {_key_str(node.key_pc, node.key_mode)} -> {_key_str(local_pc, local_mode)} ; "
                f"{chosen.label}: {left.degree} -> {target_degree}  ({left.chord} -> {right.chord})"
            )
        else:
            rule_trace.append(
                f"{chosen.label}: {left.degree} -> {target_degree}  ({left.chord} -> {right.chord})"
            )

        generated_count += 1
        frontier = left
        step_idx += 1

    generated_nodes = _collect_generated_nodes(root)
    chords_linear = [n.chord for n in sorted(generated_nodes, key=lambda n: n.id)]
    final_progression = _collect_leaf_chords_left_to_right(root)

    # Keep the final two chords as a same-key cadential closure.
    # Root expansion is constrained to perfect/plagal and modulation is disabled at step 1.
    if root.left is not None and root.degree in {"I", "i"} and root.left.degree in {"V", "IV", "iv"}:
        cadence_tail = [root.left.chord, root.chord]
        if len(final_progression) >= 2:
            final_progression = final_progression[:-2] + cadence_tail
        else:
            final_progression = cadence_tail

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = Path(__file__).resolve().parent / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    base = args.basename.strip() if args.basename.strip() else f"gen_{args.tonality}_{args.target_chords}"
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base)
    out_txt = _next_available_path(out_dir / f"{base}.txt")

    report: list[str] = []
    report.append("Chord Generation Report")
    report.append("=======================")
    report.append(f"Tonality: {args.tonality}")
    report.append(f"Target chords: {args.target_chords}")
    report.append(f"Generated chords: {generated_count}")
    report.append(f"Max depth: {args.max_depth}")
    report.append(f"Branching mode: {args.branching_mode}")
    report.append(f"Style strength: {args.style_strength:.3f}")
    report.append(f"Temperature: {args.temperature:.3f}")
    report.append(f"Modulation strength: {args.modulation_strength:.3f}")
    report.append(f"Modulation complexity: {args.modulation_complexity:.3f}")
    report.append(f"Tonal drift: {args.tonal_drift:.3f}")
    report.append(f"Initial cadence bias: {args.initial_cadence_bias:.3f}")
    report.append(f"Basic cadence strength: {args.basic_cadence_strength:.3f}")
    report.append(f"Stats CSV: {stats_csv}")
    if args.seed:
        report.append(f"Seed: {args.seed}")
    if generated_count < args.target_chords:
        reason = stop_reason if stop_reason else "stopped before target with no explicit reason"
        report.append(f"Early stop: {reason}")

    report.append("")
    report.append("Generated Chord List (in generation order)")
    report.append("------------------------------------------")
    report.append(" | ".join(chords_linear))

    report.append("")
    report.append("Rule Trace")
    report.append("----------")
    if rule_trace:
        for i, r in enumerate(rule_trace, start=1):
            report.append(f"{i:>2}. {r}")
    else:
        report.append("(no rules applied)")

    report.append("")
    report.append("ASCII Tree")
    report.append("----------")
    report.extend(_ascii_tree(root))

    report.append("")
    report.append("Forest Snippet")
    report.append("--------------")
    report.append(r"\begin{forest}")
    report.append(_forest_tex(root))
    report.append(r"\end{forest}")
    report.append("")
    report.append("Final Chord Progression (Readable)")
    report.append("----------------------------------")
    report.append(" -> ".join(final_progression))
    report.append("")

    out_txt.write_text("\n".join(report), encoding="utf-8")
    print(f"Generation report written to: {out_txt}")
    if generated_count < args.target_chords:
        print(f"Warning: generated {generated_count}/{args.target_chords} chords ({stop_reason}).")

    if args.png:
        out_png = out_txt.with_suffix(".png")
        dot_src = _tree_to_dot(root)
        ok, msg = _render_png_from_dot(dot_src, out_png, args.png_dpi)
        if ok:
            print(f"Tree PNG written to: {out_png}")
        else:
            print(f"Warning: {msg}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
