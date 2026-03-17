#!/usr/bin/env python3
"""
tex_to_svg.py — Parse bussproofs .tex from lambek_tree.py and render as SVG.

No LaTeX, no Ghostscript, no external dependencies beyond Python 3.

Usage:
    python3 tex_to_svg.py input.tex [--out output.svg]
    python3 tex_to_svg.py input.tex --stdout        # print SVG to stdout
"""

import re
import sys
import argparse
import html
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PNode:
    """A node in the parsed proof tree."""
    kind: str           # "axiom" | "unary" | "binary"
    chord: str = ""     # only axiom nodes
    grade: str = ""     # Roman numeral at conclusion
    depth: int = 0      # sequent depth counter
    right_label: str = ""
    left_label: str = ""
    is_bold_left: bool = False   # True when left label is \boldsymbol (new key)
    left:  Optional["PNode"] = None
    right: Optional["PNode"] = None


# ─────────────────────────────────────────────────────────────────────────────
# Brace-balanced argument extractor
# ─────────────────────────────────────────────────────────────────────────────

def _extract_brace_arg(s: str, start: int) -> tuple[str, int]:
    """Extract the content of {…} starting at index start. Returns (content, end_index)."""
    if start >= len(s) or s[start] != '{':
        return ("", start)
    depth = 0
    i = start
    while i < len(s):
        if s[i] == '{':
            depth += 1
        elif s[i] == '}':
            depth -= 1
            if depth == 0:
                return (s[start + 1:i], i + 1)
        i += 1
    return (s[start + 1:], len(s))


# ─────────────────────────────────────────────────────────────────────────────
# LaTeX text cleaner
# ─────────────────────────────────────────────────────────────────────────────

def _clean(s: str) -> str:
    """Strip common LaTeX markup, leaving readable text."""
    s = re.sub(r'\\text\{([^}]*)\}', r'\1', s)
    s = re.sub(r'\\textbf\{([^}]*)\}', r'\1', s)
    s = re.sub(r'\\boldsymbol\{([^}]*)\}', r'\1', s)
    s = re.sub(r'\\mathbf\{([^}]*)\}', r'\1', s)
    s = s.replace('$', '').replace('\\,', ' ').replace('\\;', ' ')
    s = s.replace('\\{', '{').replace('\\}', '}')
    return s.strip()


def _grade_from_sequent(seq: str) -> tuple[str, int]:
    """
    Pull (grade, depth) from a bussproofs sequent like:
      '\\text{ii}, \\text{ii}\\backslash\\text{V}\\sststile{3}{}\\text{V}'
    Returns ('V', 3).
    """
    m = re.search(r'\\sststile\{(\d+)\}\{[^}]*\}(.*?)$', seq, re.DOTALL)
    if not m:
        return ('?', 0)
    depth = int(m.group(1))
    rhs = m.group(2).strip()
    # May be \Box_D\Box_R\text{IV} — we want just the innermost grade
    texts = re.findall(r'\\text\{([^}]+)\}', rhs)
    grade = texts[-1] if texts else _clean(rhs)
    return (grade, depth)


def _parse_left_label(raw: str) -> tuple[str, bool]:
    """
    Parse a \\LeftLabel argument into (display_text, is_new_key).
    Bold (\boldsymbol) marks a new key arrival.
    Examples:
      '$  R_{D}g = d  $:'  ->  ('R_D g→d', False)
      '$\\boldsymbol{ R_{R}R_{D}g = F }$:' -> ('R_RR_D g→F', True)
      '\\textbf{C:}'  ->  ('C', False)   [initial key label]
    """
    is_bold = bool(re.search(r'\\boldsymbol|\\textbf', raw))
    s = _clean(raw).strip(':').strip()
    # Normalise R_{X}  →  R_X
    s = re.sub(r'R_\{([^}]+)\}', r'R_\1', s)
    # Normalise  X = Y  →  X→Y
    s = re.sub(r'\s*=\s*', '→', s)
    return (s, is_bold)


# ─────────────────────────────────────────────────────────────────────────────
# Tokeniser + parser
# ─────────────────────────────────────────────────────────────────────────────

_CMD_RE = re.compile(r'\\([A-Za-z]+)')


def _tokenise(tex: str) -> list[tuple[str, str]]:
    """
    Walk tex source and yield (command_name, first_brace_arg) pairs.
    Commands without a brace arg get arg=''.
    """
    tokens: list[tuple[str, str]] = []
    i = 0
    while i < len(tex):
        m = _CMD_RE.match(tex, i)
        if m:
            cmd = m.group(1)
            i = m.end()
            # skip whitespace
            while i < len(tex) and tex[i] in ' \t\n\r':
                i += 1
            if i < len(tex) and tex[i] == '{':
                arg, i = _extract_brace_arg(tex, i)
            else:
                arg = ''
            tokens.append((cmd, arg))
        else:
            i += 1
    return tokens


def parse_tex(tex_source: str) -> Optional[PNode]:
    """
    Parse bussproofs-style LaTeX and return the root PNode.
    Uses a stack: AxiomC stores a pending chord; UnaryInfC / BinaryInfC pop it.
    """
    # Work only inside \begin{document}…\end{document} if present
    m = re.search(r'\\begin\{document\}(.*?)\\end\{document\}', tex_source, re.DOTALL)
    body = m.group(1) if m else tex_source

    tokens = _tokenise(body)

    stack: list[PNode] = []
    pending_chord: str = ""
    pending_ll: str = ""
    pending_rl: str = ""

    for cmd, arg in tokens:
        if cmd == 'AxiomC':
            tm = re.search(r'\\text\{([^}]+)\}', arg)
            pending_chord = tm.group(1) if tm else _clean(arg)
            pending_ll = pending_rl = ""

        elif cmd == 'LeftLabel':
            pending_ll = arg

        elif cmd == 'RightLabel':
            pending_rl = arg

        elif cmd == 'UnaryInfC':
            grade, depth = _grade_from_sequent(arg)
            ll_text, ll_bold = _parse_left_label(pending_ll)
            rl_text = _clean(pending_rl).strip('$').strip()

            if pending_chord:
                # AxiomC + UnaryInfC pair → leaf node
                node = PNode(
                    kind='axiom',
                    chord=pending_chord,
                    grade=grade,
                    depth=depth,
                    left_label=ll_text,
                    right_label=rl_text,
                    is_bold_left=ll_bold,
                )
                stack.append(node)
                pending_chord = ""
            else:
                # Unary inference on existing node → modulation step
                child = stack.pop() if stack else PNode(kind='axiom', chord='?', grade='?')
                node = PNode(
                    kind='unary',
                    grade=grade,
                    depth=depth,
                    left_label=ll_text,
                    right_label=rl_text,
                    is_bold_left=ll_bold,
                    left=child,
                )
                stack.append(node)
            pending_ll = pending_rl = ""

        elif cmd == 'BinaryInfC':
            grade, depth = _grade_from_sequent(arg)
            ll_text, ll_bold = _parse_left_label(pending_ll)
            rl_text = _clean(pending_rl).strip('$').strip()

            # Right = most recently pushed (new chord), Left = accumulated spine
            right_child = stack.pop() if stack else PNode(kind='axiom', chord='?', grade='?')
            left_child  = stack.pop() if stack else PNode(kind='axiom', chord='?', grade='?')
            node = PNode(
                kind='binary',
                grade=grade,
                depth=depth,
                left_label=ll_text,
                right_label=rl_text,
                is_bold_left=ll_bold,
                left=left_child,
                right=right_child,
            )
            stack.append(node)
            pending_ll = pending_rl = ""

    return stack[-1] if stack else None


# ─────────────────────────────────────────────────────────────────────────────
# Layout  (staircase, same logic as JS Proof Tableau)
# ─────────────────────────────────────────────────────────────────────────────

BW, BH   = 92, 40    # chord / conclusion box  (width, height)
RW, RH   = 72, 22    # rule box
XS       = 130       # horizontal step per binary level
YS_BIN   = 130       # vertical step for binary inference
YS_UNI   = 75        # vertical step for unary (modulation) inference
RULE_UP  = 0.62      # rule box sits this fraction above the binary YS_BIN gap


def _leftmost_axiom(node: PNode) -> PNode:
    if node.kind == 'axiom':
        return node
    if node.left:
        return _leftmost_axiom(node.left)
    return node


def _layout(node: PNode,
            positions: dict[int, tuple[float, float]],
            cur_x: float, cur_y: float) -> tuple[float, float]:
    """
    Recursively assign (x, y) positions; returns the position of *this* node.
    """
    if node.kind == 'axiom':
        positions[id(node)] = (cur_x, cur_y)
        return cur_x, cur_y

    elif node.kind == 'binary':
        # Layout left spine first
        lx, ly = _layout(node.left, positions, cur_x, cur_y)
        # Right child: new axiom/node to the right at the same y
        rx, ry = lx + XS, ly
        _layout(node.right, positions, rx, ry)
        # Conclusion: below and between
        nx = (lx + rx) / 2
        ny = ly + YS_BIN
        positions[id(node)] = (nx, ny)
        return nx, ny

    elif node.kind == 'unary':
        cx, cy = _layout(node.left, positions, cur_x, cur_y)
        nx, ny = cx, cy + YS_UNI
        positions[id(node)] = (nx, ny)
        return nx, ny

    return cur_x, cur_y


def compute_layout(root: PNode) -> dict[int, tuple[float, float]]:
    positions: dict[int, tuple[float, float]] = {}
    _layout(root, positions, 0.0, 0.0)
    return positions


# ─────────────────────────────────────────────────────────────────────────────
# SVG renderer
# ─────────────────────────────────────────────────────────────────────────────

# Colour palette
COL = {
    'axiom_fill':   '#dce8ff',
    'axiom_stroke': '#3a6db5',
    'axiom_text':   '#1a2a4a',
    'bin_fill':     '#fff3cd',
    'bin_stroke':   '#b07d00',
    'bin_text':     '#3d2b00',
    'uni_fill':     '#e8f5e9',
    'uni_stroke':   '#2e7d32',
    'uni_text':     '#1b5e20',
    'rule_fill':    '#fde8e8',
    'rule_stroke':  '#c0392b',
    'rule_text':    '#7b0000',
    'edge':         '#888',
    'lbl_new_key':  '#1565c0',  # bold left label (new key)
    'lbl_mod':      '#555',     # normal modulation label
    'lbl_key':      '#333',     # initial key label
}

ML, MR, MT, MB = 18, 18, 50, 20  # margins
KEY_LBL_FONT = 9
CHORD_FONT   = 10
GRADE_FONT   = 8
RULE_FONT    = 8
LEFT_LBL_FONT = 8


def _e(s: str) -> str:
    """HTML-escape for SVG text content."""
    return html.escape(str(s))


def _all_nodes(root: PNode) -> list[PNode]:
    out: list[PNode] = []
    def _walk(n: PNode) -> None:
        out.append(n)
        if n.left:  _walk(n.left)
        if n.right: _walk(n.right)
    _walk(root)
    return out


def render_svg(root: PNode, title: str = '') -> str:
    pos = compute_layout(root)
    nodes = _all_nodes(root)

    if not pos:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="40"><text y="20">empty</text></svg>'

    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    title_h = 22 if title else 0
    W = int(max_x - min_x + BW + ML + MR)
    H = int(max_y - min_y + BH + MT + MB + title_h)

    def tx(x: float) -> float: return x - min_x + ML + BW / 2
    def ty(y: float) -> float: return y - min_y + MT + title_h

    lines: list[str] = []
    lines.append(f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
                 f'style="width:100%;max-width:{W}px;display:block;font-family:\'Segoe UI\',Arial,sans-serif">')

    # ── title ──────────────────────────────────────────────────────────────
    if title:
        lines.append(f'<text x="{W//2}" y="16" text-anchor="middle" '
                     f'font-size="12" font-weight="bold" fill="#333">{_e(title)}</text>')

    # ── edges + rule boxes ─────────────────────────────────────────────────
    for node in nodes:
        if node.kind not in ('binary', 'unary') or id(node) not in pos:
            continue
        nx, ny = pos[id(node)]
        ncx, ncy = tx(nx), ty(ny)

        if node.kind == 'binary':
            lx, ly = pos[id(node.left)]
            rx, ry = pos[id(node.right)]
            lcx, lcy = tx(lx), ty(ly)
            rcx, rcy = tx(rx), ty(ry)

            rule_y = ncy - RULE_UP * YS_BIN

            # Lines from left and right children down to rule box
            lines.append(f'<line x1="{lcx:.1f}" y1="{lcy + BH/2:.1f}" '
                         f'x2="{(lcx+rcx)/2:.1f}" y2="{rule_y - RH/2:.1f}" '
                         f'stroke="{COL["edge"]}" stroke-width="1.3"/>')
            lines.append(f'<line x1="{rcx:.1f}" y1="{rcy + BH/2:.1f}" '
                         f'x2="{(lcx+rcx)/2:.1f}" y2="{rule_y - RH/2:.1f}" '
                         f'stroke="{COL["edge"]}" stroke-width="1.3"/>')
            # Line from rule box down to conclusion
            lines.append(f'<line x1="{ncx:.1f}" y1="{rule_y + RH/2:.1f}" '
                         f'x2="{ncx:.1f}" y2="{ncy - BH/2:.1f}" '
                         f'stroke="{COL["edge"]}" stroke-width="1.3"/>')

            # Rule box (centred between left/right at rule_y)
            rule_cx = (lcx + rcx) / 2
            lines.append(f'<rect x="{rule_cx - RW/2:.1f}" y="{rule_y - RH/2:.1f}" '
                         f'width="{RW}" height="{RH}" rx="5" '
                         f'fill="{COL["rule_fill"]}" stroke="{COL["rule_stroke"]}" stroke-width="1.1"/>')
            rule_txt = node.right_label or r'(\L)'
            lines.append(f'<text x="{rule_cx:.1f}" y="{rule_y + 5:.1f}" '
                         f'text-anchor="middle" font-size="{RULE_FONT}" '
                         f'fill="{COL["rule_text"]}" font-family="monospace">{_e(rule_txt)}</text>')

            # Left label (key / modulation) — to the left of left child
            if node.left_label:
                col = COL['lbl_new_key'] if node.is_bold_left else COL['lbl_mod']
                fw  = 'bold' if node.is_bold_left else 'normal'
                lines.append(f'<text x="{lcx - BW/2 - 4:.1f}" y="{lcy + 5:.1f}" '
                              f'text-anchor="end" font-size="{LEFT_LBL_FONT}" '
                              f'fill="{col}" font-weight="{fw}">{_e(node.left_label)}</text>')

        elif node.kind == 'unary':
            cx, cy = pos[id(node.left)]
            ccx, ccy = tx(cx), ty(cy)
            rule_y = ncy - RULE_UP * YS_UNI

            # Line from child down to rule box
            lines.append(f'<line x1="{ccx:.1f}" y1="{ccy + BH/2:.1f}" '
                         f'x2="{ccx:.1f}" y2="{rule_y - RH/2:.1f}" '
                         f'stroke="{COL["uni_stroke"]}" stroke-width="1.3" stroke-dasharray="4,2"/>')
            # Line from rule box to conclusion
            lines.append(f'<line x1="{ncx:.1f}" y1="{rule_y + RH/2:.1f}" '
                         f'x2="{ncx:.1f}" y2="{ncy - BH/2:.1f}" '
                         f'stroke="{COL["uni_stroke"]}" stroke-width="1.3" stroke-dasharray="4,2"/>')

            # Rule box
            lines.append(f'<rect x="{ccx - RW/2:.1f}" y="{rule_y - RH/2:.1f}" '
                         f'width="{RW}" height="{RH}" rx="5" '
                         f'fill="{COL["uni_fill"]}" stroke="{COL["uni_stroke"]}" stroke-width="1.1"/>')
            rule_txt = node.right_label or 'K'
            lines.append(f'<text x="{ccx:.1f}" y="{rule_y + 5:.1f}" '
                         f'text-anchor="middle" font-size="{RULE_FONT}" '
                         f'fill="{COL["uni_text"]}" font-family="monospace">{_e(rule_txt)}</text>')

            # Left label
            if node.left_label:
                col = COL['lbl_new_key'] if node.is_bold_left else COL['lbl_mod']
                fw  = 'bold' if node.is_bold_left else 'normal'
                lines.append(f'<text x="{ccx - RW/2 - 4:.1f}" y="{rule_y + 5:.1f}" '
                              f'text-anchor="end" font-size="{LEFT_LBL_FONT}" '
                              f'fill="{col}" font-weight="{fw}">{_e(node.left_label)}</text>')

    # ── node boxes ─────────────────────────────────────────────────────────
    for node in nodes:
        if id(node) not in pos:
            continue
        nx, ny = pos[id(node)]
        cx, cy = tx(nx), ty(ny)
        bx, by = cx - BW / 2, cy - BH / 2

        if node.kind == 'axiom':
            # Blue leaf box
            lines.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{BW}" height="{BH}" rx="6" '
                         f'fill="{COL["axiom_fill"]}" stroke="{COL["axiom_stroke"]}" stroke-width="1.5"/>')
            lines.append(f'<text x="{cx:.1f}" y="{cy - 5:.1f}" text-anchor="middle" '
                         f'font-size="{CHORD_FONT}" font-weight="bold" fill="{COL["axiom_text"]}">'
                         f'{_e(node.chord)}</text>')
            lines.append(f'<text x="{cx:.1f}" y="{cy + 10:.1f}" text-anchor="middle" '
                         f'font-size="{GRADE_FONT}" fill="#555">/{_e(node.grade)}</text>')
            # Initial key label (left label on first axiom after key inference)
            if node.left_label:
                lines.append(f'<text x="{bx - 4:.1f}" y="{cy + 4:.1f}" '
                              f'text-anchor="end" font-size="{KEY_LBL_FONT}" '
                              f'fill="{COL["lbl_key"]}" font-weight="bold">{_e(node.left_label)}</text>')

        elif node.kind == 'binary':
            lines.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{BW}" height="{BH}" rx="6" '
                         f'fill="{COL["bin_fill"]}" stroke="{COL["bin_stroke"]}" stroke-width="1.5"/>')
            lines.append(f'<text x="{cx:.1f}" y="{cy - 4:.1f}" text-anchor="middle" '
                         f'font-size="{CHORD_FONT}" font-weight="bold" fill="{COL["bin_text"]}">'
                         f'{_e(node.grade)}</text>')
            if node.left_label:
                lines.append(f'<text x="{cx:.1f}" y="{cy + 10:.1f}" text-anchor="middle" '
                              f'font-size="{GRADE_FONT}" fill="#666">{_e(node.left_label)}</text>')

        elif node.kind == 'unary':
            lines.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{BW}" height="{BH}" rx="6" '
                         f'fill="{COL["uni_fill"]}" stroke="{COL["uni_stroke"]}" stroke-width="1.5"/>')
            lines.append(f'<text x="{cx:.1f}" y="{cy - 4:.1f}" text-anchor="middle" '
                         f'font-size="{CHORD_FONT}" font-weight="bold" fill="{COL["uni_text"]}">'
                         f'{_e(node.grade)}</text>')
            if node.left_label:
                lines.append(f'<text x="{cx:.1f}" y="{cy + 10:.1f}" text-anchor="middle" '
                              f'font-size="{GRADE_FONT}" fill="#2e7d32">{_e(node.left_label)}</text>')

    lines.append('</svg>')
    return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def convert_file(tex_path: Path, out_path: Optional[Path] = None,
                 stdout: bool = False, title: str = '') -> bool:
    tex = tex_path.read_text(encoding='utf-8')
    root = parse_tex(tex)
    if root is None:
        print(f'ERROR: could not parse proof tree from {tex_path}', file=sys.stderr)
        return False

    svg = render_svg(root, title=title or tex_path.stem)

    if stdout:
        print(svg)
    else:
        dest = out_path or tex_path.with_suffix('.svg')
        dest.write_text(svg, encoding='utf-8')
        print(f'Saved: {dest}')
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description='Convert lambek_tree .tex → SVG (no LaTeX needed)')
    ap.add_argument('input', nargs='?', help='.tex file to convert')
    ap.add_argument('--out', '-o', help='output .svg path (default: same as input with .svg)')
    ap.add_argument('--stdout', action='store_true', help='print SVG to stdout instead of file')
    ap.add_argument('--title', default='', help='title to embed in SVG')
    ap.add_argument('--batch', metavar='DIR',
                    help='convert all .tex files in DIR (recursive)')
    args = ap.parse_args()

    if args.batch:
        d = Path(args.batch)
        files = list(d.rglob('*.tex'))
        ok = sum(convert_file(f, title=f.stem) for f in files)
        print(f'Converted {ok}/{len(files)} files.')
        return 0 if ok == len(files) else 1

    if not args.input:
        ap.print_help()
        return 1

    return 0 if convert_file(
        Path(args.input),
        Path(args.out) if args.out else None,
        stdout=args.stdout,
        title=args.title,
    ) else 1


if __name__ == '__main__':
    sys.exit(main())
