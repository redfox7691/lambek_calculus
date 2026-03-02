#!/usr/bin/env python3
"""
Compute fastest modulation routes on the 24-key tonality lattice.

The lattice uses four accessibility functions from the paper:
- D: dominant modulation
- S: subdominant modulation
- R: relative major/minor modulation
- P: parallel major/minor modulation

Examples:
  python3 tonality_route.py --from C --to c
  python3 tonality_route.py --from Eb --to c --export --out-dir route_maps
  python3 tonality_route.py --from F# --to c --spelling sharps
"""

from __future__ import annotations

import argparse
import os
from collections import deque
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess


MODE_MAJOR = "M"
MODE_MINOR = "m"

# Circle-of-fifths pitch-class order starting from C.
FIFTHS_ORDER = [0, 7, 2, 9, 4, 11, 6, 1, 8, 3, 10, 5]

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

PC_TO_NAME_SHARP = {
    0: "C",
    1: "C#",
    2: "D",
    3: "D#",
    4: "E",
    5: "F",
    6: "F#",
    7: "G",
    8: "G#",
    9: "A",
    10: "A#",
    11: "B",
}

TONALITY_RE = re.compile(r"^\s*([A-Ga-g])([#b♯♭]?)(.*)\s*$")


@dataclass(frozen=True)
class Tonality:
    pc: int
    mode: str  # "M" or "m"


def _normalize_note_name(letter: str, accidental: str) -> str:
    acc = accidental.replace("♯", "#").replace("♭", "b")
    note = f"{letter.upper()}{acc}"
    note = note.replace("b", "B")
    if note not in NOTE_TO_PC:
        raise ValueError(f"Unsupported note spelling '{letter}{accidental}'.")
    return note


def parse_tonality(token: str) -> Tonality:
    m = TONALITY_RE.match(token or "")
    if not m:
        raise ValueError(f"Invalid tonality '{token}'.")

    letter, accidental, rest = m.groups()
    note = _normalize_note_name(letter, accidental)
    pc = NOTE_TO_PC[note]

    suffix = rest.strip().lower()

    # Minor cases:
    # - lowercase tonic letter, e.g. c, eb
    # - explicit suffix m/min/minor (but not maj/major)
    if letter.islower():
        mode = MODE_MINOR
    elif suffix.startswith("min") or (suffix.startswith("m") and not suffix.startswith("maj")):
        mode = MODE_MINOR
    else:
        mode = MODE_MAJOR

    return Tonality(pc=pc, mode=mode)


def tonality_to_str(t: Tonality, spelling: str) -> str:
    if spelling == "sharps":
        root = PC_TO_NAME_SHARP[t.pc]
    else:
        root = PC_TO_NAME_FLAT[t.pc]

    if t.mode == MODE_MAJOR:
        return root
    return root[0].lower() + root[1:]


def choose_spelling(start_raw: str, goal_raw: str, explicit: str) -> str:
    if explicit in {"flats", "sharps"}:
        return explicit
    if "#" in start_raw or "#" in goal_raw or "♯" in start_raw or "♯" in goal_raw:
        return "sharps"
    return "flats"


def apply_op(t: Tonality, op: str) -> Tonality:
    if op == "D":
        return Tonality((t.pc + 7) % 12, t.mode)
    if op == "S":
        return Tonality((t.pc + 5) % 12, t.mode)
    if op == "P":
        return Tonality(t.pc, MODE_MINOR if t.mode == MODE_MAJOR else MODE_MAJOR)
    if op == "R":
        if t.mode == MODE_MAJOR:
            # major -> relative minor (down minor third)
            return Tonality((t.pc + 9) % 12, MODE_MINOR)
        # minor -> relative major (up minor third)
        return Tonality((t.pc + 3) % 12, MODE_MAJOR)
    raise ValueError(f"Unknown operation '{op}'.")


def all_tonalities() -> list[Tonality]:
    out: list[Tonality] = []
    for pc in range(12):
        out.append(Tonality(pc, MODE_MAJOR))
    for pc in range(12):
        out.append(Tonality(pc, MODE_MINOR))
    return out


def shortest_path(start: Tonality, goal: Tonality) -> tuple[list[Tonality], list[str]]:
    if start == goal:
        return [start], []

    ops_order = ["D", "S", "R", "P"]
    q: deque[Tonality] = deque([start])
    seen: set[Tonality] = {start}
    parent: dict[Tonality, tuple[Tonality, str]] = {}

    while q:
        cur = q.popleft()
        for op in ops_order:
            nxt = apply_op(cur, op)
            if nxt in seen:
                continue
            seen.add(nxt)
            parent[nxt] = (cur, op)
            if nxt == goal:
                # Reconstruct
                nodes: list[Tonality] = [goal]
                ops: list[str] = []
                x = goal
                while x != start:
                    prev, edge = parent[x]
                    ops.append(edge)
                    nodes.append(prev)
                    x = prev
                nodes.reverse()
                ops.reverse()
                return nodes, ops
            q.append(nxt)

    raise RuntimeError("No route found (unexpected on connected lattice).")


def _node_id(t: Tonality) -> str:
    return f"{t.mode}{t.pc}"


def _slugify(token: str) -> str:
    s = token.strip().replace("♯", "#").replace("♭", "b")
    s = s.replace("#", "s")
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^A-Za-z0-9._-]", "_", s)
    return s or "route"


def _next_available_path(path: Path) -> Path:
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent

    i = 2
    while True:
        candidate = parent / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def _best_minor_order(path_nodes: list[Tonality], path_ops: list[str]) -> list[int]:
    """
    Choose a circle-of-fifths rotation for the minor row that shortens R/P path edges.
    Major row remains fixed starting at C.
    """
    base = FIFTHS_ORDER[:]
    major_col = {pc: i for i, pc in enumerate(base)}

    rp_steps: list[tuple[Tonality, Tonality, str]] = []
    for i, op in enumerate(path_ops):
        if op in {"R", "P"}:
            rp_steps.append((path_nodes[i], path_nodes[i + 1], op))

    if not rp_steps:
        return base

    best_order = base
    best_score = (10**9, 10**9)

    for k in range(12):
        order = base[k:] + base[:k]
        minor_col = {pc: i for i, pc in enumerate(order)}

        dists: list[int] = []
        for a, b, _op in rp_steps:
            ca = major_col[a.pc] if a.mode == MODE_MAJOR else minor_col[a.pc]
            cb = major_col[b.pc] if b.mode == MODE_MAJOR else minor_col[b.pc]
            dists.append(abs(ca - cb))

        score = (max(dists), sum(dists))
        if score < best_score:
            best_score = score
            best_order = order

    return best_order


def build_dot(
    start: Tonality,
    goal: Tonality,
    path_nodes: list[Tonality],
    path_ops: list[str],
    spelling: str,
) -> str:
    path_node_set = set(path_nodes)
    path_edges: set[tuple[Tonality, Tonality, str]] = set()
    for i, op in enumerate(path_ops):
        path_edges.add((path_nodes[i], path_nodes[i + 1], op))

    lines: list[str] = []
    lines.append("digraph TonalityLattice {")
    lines.append('  rankdir=TB;')
    lines.append('  splines=false;')
    lines.append('  ranksep=0.8;')
    lines.append('  nodesep=0.35;')
    lines.append('  overlap=false;')
    lines.append('  node [shape=circle, fontsize=11, fontname="Helvetica"];')
    lines.append('  edge [fontsize=9, fontname="Helvetica"];')

    # Create nodes; use group by pitch-class so minor/major share a vertical column.
    for pc in range(12):
        for mode in (MODE_MINOR, MODE_MAJOR):
            t = Tonality(pc, mode)
            nid = _node_id(t)
            label = tonality_to_str(t, spelling)
            attrs = [f'label="{label}"', f'group="g{pc}"']
            if t == start:
                attrs += ['style="filled,bold"', 'fillcolor="#c8f7c5"', 'color="#2b8a3e"']
            elif t == goal:
                attrs += ['style="filled,bold"', 'fillcolor="#ffd8a8"', 'color="#d9480f"']
            elif t in path_node_set:
                attrs += ['style="filled"', 'fillcolor="#ffe066"', 'color="#f08c00"']
            lines.append(f"  {nid} [{', '.join(attrs)}];")

    # Two-row layout in circle-of-fifths order. Major starts from C;
    # minor row rotates to reduce long R/P path jumps.
    # Keep a fixed circle-of-fifths layout for visual consistency across routes.
    minor_order = FIFTHS_ORDER[:]
    minor_row = " ".join(_node_id(Tonality(pc, MODE_MINOR)) for pc in minor_order)
    major_row = " ".join(_node_id(Tonality(pc, MODE_MAJOR)) for pc in FIFTHS_ORDER)
    lines.append(f"  {{ rank=same; {minor_row} }}")
    lines.append(f"  {{ rank=same; {major_row} }}")

    # group="gX" keeps minor/major pairs in shared columns.
    # Add lightweight invisible guides: enforce order within each row,
    # and keep minor row above major row.
    minor_nodes = [_node_id(Tonality(pc, MODE_MINOR)) for pc in minor_order]
    major_nodes = [_node_id(Tonality(pc, MODE_MAJOR)) for pc in FIFTHS_ORDER]
    for a, b in zip(minor_nodes, minor_nodes[1:]):
        lines.append(f"  {a} -> {b} [style=invis, weight=50, constraint=true];")
    for a, b in zip(major_nodes, major_nodes[1:]):
        lines.append(f"  {a} -> {b} [style=invis, weight=50, constraint=true];")
    lines.append(f"  {minor_nodes[0]} -> {major_nodes[0]} [style=invis, minlen=2, weight=80, constraint=true];")

    # Path-focused rendering: draw only edges on the chosen shortest route.
    for src_t, dst_t, op in path_edges:
        src = _node_id(src_t)
        dst = _node_id(dst_t)
        attrs = [
            f'label="{op}"',
            'color="#d00000"',
            'fontcolor="#d00000"',
            'penwidth=2.8',
            'constraint=true',
        ]
        lines.append(f"  {src} -> {dst} [{', '.join(attrs)}];")

    lines.append("}")
    return "\n".join(lines)


def _resolve_output_path(args: argparse.Namespace) -> Path:
    script_dir = Path(__file__).resolve().parent

    if args.out_dot:
        requested_dot = Path(args.out_dot)
        if not requested_dot.is_absolute():
            requested_dot = script_dir / requested_dot
        requested_dot.parent.mkdir(parents=True, exist_ok=True)
        out_dot = _next_available_path(requested_dot)
        return _next_available_path(requested_dot.with_suffix(".png"))

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = script_dir / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.basename:
        basename = _slugify(args.basename)
    else:
        basename = f"route_{_slugify(args.source)}_to_{_slugify(args.target)}"

    return _next_available_path(out_dir / f"{basename}.png")


def _export_png(dot_source: str, out_png: Path, dpi: int) -> tuple[bool, str]:
    dot_bin = shutil.which("dot")
    if not dot_bin:
        return False, "Graphviz 'dot' not found: PNG skipped."

    dpi = max(72, int(dpi))

    try:
        # Use cairo backend + explicit DPI for higher-quality export.
        subprocess.run(
            [dot_bin, "-Tpng:cairo", f"-Gdpi={dpi}", "-o", str(out_png)],
            input=dot_source.encode("utf-8"),
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        return False, f"Graphviz failed ({exc}): PNG skipped."

    return True, ""


def _write_route_tex(out_tex: Path, out_png: Path, source: str, target: str) -> None:
    image_name = os.path.basename(str(out_png))
    tex = "\n".join([
        r"\documentclass[a4paper,landscape]{article}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage[margin=0.6in]{geometry}",
        r"\usepackage{graphicx}",
        r"\usepackage{float}",
        r"\pagestyle{empty}",
        r"\begin{document}",
        rf"\section*{{Tonality Route: {_slugify(source)} to {_slugify(target)}}}",
        r"\begin{figure}[H]",
        r"\centering",
        rf"\includegraphics[width=0.98\linewidth]{{{image_name}}}",
        r"\end{figure}",
        r"\end{document}",
        "",
    ])
    out_tex.write_text(tex, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find shortest modulation route with D/S/R/P on the tonality lattice."
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default="",
        help="Optional positional mode. Use 'png' to export files.",
    )
    parser.add_argument("--from", dest="source", required=True, help="Start tonality, e.g. C, c, Eb, Cm")
    parser.add_argument("--to", dest="target", required=True, help="Target tonality, e.g. G, g, F#, fm")
    parser.add_argument(
        "--spelling",
        choices=["auto", "flats", "sharps"],
        default="auto",
        help="Output key spelling style.",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="Write DOT/SVG files. If omitted, only route text is printed.",
    )
    parser.add_argument(
        "--out-dir",
        default="route_maps",
        help="Directory for generated PNG files (used with --export).",
    )
    parser.add_argument(
        "--basename",
        default="",
        help="Base filename for outputs (without extension).",
    )
    parser.add_argument(
        "--out-dot",
        default="",
        help="Optional explicit path (legacy override): if provided with .dot, it is converted to .png.",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=450,
        help="PNG export DPI for route maps (default: 450).",
    )
    args = parser.parse_args()

    start = parse_tonality(args.source)
    goal = parse_tonality(args.target)
    spelling = choose_spelling(args.source, args.target, args.spelling)

    nodes, ops = shortest_path(start, goal)

    path_keys = [tonality_to_str(t, spelling) for t in nodes]
    op_chain = " ".join(ops) if ops else "(none)"

    print(f"From: {tonality_to_str(start, spelling)}")
    print(f"To:   {tonality_to_str(goal, spelling)}")
    print(f"Steps: {len(ops)}")
    print(f"Ops:   {op_chain}")
    print(f"Path:  {' -> '.join(path_keys)}")

    mode = args.mode.strip().lower()
    export_enabled = args.export or mode in {"png", "export"}

    if mode and mode not in {"png", "export"}:
        raise ValueError(f"Unknown mode '{args.mode}'. Use 'png' or omit it.")

    if not export_enabled:
        print("No files written. Use mode 'png' (e.g., python3 tonality_route.py png --from C --to c) or --export.")
        return 0

    dot = build_dot(start, goal, nodes, ops, spelling)
    out_png = _resolve_output_path(args)

    created_png, warn = _export_png(dot, out_png, args.dpi)

    if created_png:
        print(f"PNG written to: {out_png}")
        out_tex = _next_available_path(out_png.with_suffix(".tex"))
        _write_route_tex(out_tex, out_png, args.source, args.target)
        print(f"TEX written to: {out_tex}")
    elif warn:
        print(f"Warning: {warn}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
