#!/usr/bin/env python3
r"""
Create a readability-oriented version of generated Lambek tree TEX files.

Goals:
- Replace LeftLabel relation chains R_{...} with plain key labels.
- Resolve \Box_{D,S,R,P} into explicit key tags when key differs from left label key.
- Keep original output untouched by writing to a separate folder.

Example:
  python3 simplify_tree_notation.py --standard "After You've Gone"
  python3 simplify_tree_notation.py --input /path/to/tree.tex
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


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

TON_RE = re.compile(r"^\s*([A-Ga-g])([#b♯♭]?)")
LAST_LINE_ENTRY_RE = re.compile(r'^\s*"([^"]+)"\s*"\$\s*(.*?)\s*\$"\s*\\\\\s*$')


def _latex_escape_text(s: str) -> str:
    return (
        s.replace('\\', r'\\textbackslash{}')
        .replace('{', r'\{')
        .replace('}', r'\}')
        .replace('#', r'\#')
        .replace('_', r'\_')
        .replace('&', r'\&')
        .replace('%', r'\%')
    )


@dataclass(frozen=True)
class Tonality:
    pc: int
    mode: str  # M/m


def _normalize_note(token: str) -> str:
    m = TON_RE.match(token or "")
    if not m:
        raise ValueError(f"Invalid tonality token '{token}'.")
    letter, accidental = m.groups()
    acc = accidental.replace("♯", "#").replace("♭", "b")
    note = f"{letter.upper()}{acc}".replace("b", "B")
    if note not in NOTE_TO_PC:
        raise ValueError(f"Unsupported note '{token}'.")
    return note


def parse_tonality(token: str) -> Tonality:
    token = (token or "").strip()
    if not token:
        raise ValueError("Empty tonality.")
    note = _normalize_note(token)
    letter = token[0]
    mode = "m" if letter.islower() else "M"
    return Tonality(pc=NOTE_TO_PC[note], mode=mode)


def tonality_to_str(t: Tonality) -> str:
    root = PC_TO_NAME_FLAT[t.pc]
    if t.mode == "M":
        return root
    return root[0].lower() + root[1:]


def apply_op(t: Tonality, op: str) -> Tonality:
    if op == "D":
        return Tonality((t.pc + 7) % 12, t.mode)
    if op == "S":
        return Tonality((t.pc + 5) % 12, t.mode)
    if op == "P":
        return Tonality(t.pc, "m" if t.mode == "M" else "M")
    if op == "R":
        if t.mode == "M":
            return Tonality((t.pc + 9) % 12, "m")
        return Tonality((t.pc + 3) % 12, "M")
    return t


def _slugify(s: str) -> str:
    s = s.strip().replace("♯", "#").replace("♭", "b").replace("#", "s")
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^A-Za-z0-9._-]", "_", s)
    return s or "out"


def _next_available_dir(path: Path) -> Path:
    if not path.exists():
        return path
    i = 1
    while True:
        cand = path.parent / f"{path.name}_{i}"
        if not cand.exists():
            return cand
        i += 1


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


def _render_png_from_tex(tex_path: Path, out_png: Path, dpi: int) -> tuple[bool, str]:
    pdflatex = shutil.which("pdflatex")
    if not pdflatex:
        return False, "pdflatex not found: PNG skipped."

    gs = shutil.which("gs")
    sips = shutil.which("sips")

    with tempfile.TemporaryDirectory(prefix="readable_render_") as tmp:
        tmpdir = Path(tmp)
        tmp_tex = tmpdir / tex_path.name
        tmp_tex.write_text(tex_path.read_text(encoding="utf-8"), encoding="utf-8")

        try:
            subprocess.run(
                [
                    pdflatex,
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    "-output-directory",
                    str(tmpdir),
                    str(tmp_tex),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            tail = "\n".join((exc.stdout or "").splitlines()[-20:])
            return False, f"pdflatex failed: {tail}"

        pdf_path = tmpdir / tex_path.with_suffix('.pdf').name
        if not pdf_path.exists():
            return False, "pdflatex did not produce PDF"

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
                tail = "\n".join((exc.stdout or "").splitlines()[-20:])
                return False, f"ghostscript failed: {tail}"

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
                tail = "\n".join((exc.stdout or "").splitlines()[-12:])
                return False, f"sips failed: {tail}"

    return False, "No PDF-to-PNG tool found (gs/sips)."


def _extract_left_label_key(line: str) -> Tonality | None:
    # cases: ... = Bb ... OR \textbf{F:}
    m = re.search(r"=\s*([A-Ga-g][#b]?)", line)
    if m:
        try:
            return parse_tonality(m.group(1))
        except Exception:
            return None

    m = re.search(r"\\textbf\{\s*([A-Ga-g][#b]?)\s*:\s*\}", line)
    if m:
        try:
            return parse_tonality(m.group(1))
        except Exception:
            return None

    m = re.search(r"\\LeftLabel\{\s*([A-Ga-g][#b]?)\s*:\s*\}", line)
    if m:
        try:
            return parse_tonality(m.group(1))
        except Exception:
            return None

    return None


def _simplify_left_label(line: str, key: Tonality | None) -> str:
    if key is None:
        return line
    key_s = tonality_to_str(key)
    return rf"\LeftLabel{{\textbf{{{key_s}:}}}}"


def _parse_balanced_brace(s: str, i: int) -> tuple[str, int]:
    # expects s[i] == '{'
    depth = 0
    j = i
    out = []
    while j < len(s):
        ch = s[j]
        if ch == "{":
            depth += 1
            if depth > 1:
                out.append(ch)
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return "".join(out), j + 1
            out.append(ch)
        else:
            out.append(ch)
        j += 1
    return "".join(out), j


def _annotate_if_needed(atom: str, formula_key: Tonality, line_key: Tonality) -> str:
    if formula_key == line_key:
        return atom
    key = _latex_escape_text(tonality_to_str(formula_key))
    return rf"\text{{\textbf{{{key}}}}}\{{{atom}\}}"


def _is_degree_token(txt: str) -> bool:
    t = txt.strip()
    # Roman-degree-like tokens: I, ii, bVII, #iv, etc.
    return re.match(r"^[#b]?[IViv]+$", t) is not None


def _annotate_degree_atoms(expr: str, formula_key: Tonality, line_key: Tonality) -> str:
    r"""
    Annotate plain degree atoms in an inherited expression, so conversion is complete.
    Only wraps 	ext{...} atoms that look like Roman degrees.
    Existing keyed atoms like 	ext{	extbf{Bb}}\{...\} are left unchanged.
    """
    if formula_key == line_key:
        return expr

    def _parse_escaped_brace_group(s: str, start: int) -> tuple[str, int] | None:
        # parses \{ ... \} with nesting on escaped braces
        if not (start + 1 < len(s) and s[start] == '\\' and s[start + 1] == '{'):
            return None
        j = start + 2
        depth = 1
        parts: list[str] = []
        while j < len(s):
            if j + 1 < len(s) and s[j] == '\\' and s[j + 1] == '{':
                depth += 1
                parts.append(r"\{")
                j += 2
                continue
            if j + 1 < len(s) and s[j] == '\\' and s[j + 1] == '}':
                depth -= 1
                if depth == 0:
                    return "".join(parts), j + 2
                parts.append(r"\}")
                j += 2
                continue
            parts.append(s[j])
            j += 1
        return None

    def _parse_existing_keyed_atom(s: str, start: int) -> tuple[str, int] | None:
        # existing keyed form: 	ext{	extbf{Bb}}\{...\}
        if not s.startswith(r"\text{", start):
            return None
        inner, end_i = _parse_balanced_brace(s, start + len(r"\text"))
        if re.match(r"^\s*(\\textbf\{[^{}]+\}|[A-Ga-g][#b]?)\s*$", inner) is None:
            return None
        parsed = _parse_escaped_brace_group(s, end_i)
        if parsed is None:
            return None
        _, end_j = parsed
        return s[start:end_j], end_j

    out: list[str] = []
    i = 0
    while i < len(expr):
        keyed = _parse_existing_keyed_atom(expr, i)
        if keyed is not None:
            chunk, end_i = keyed
            out.append(chunk)
            i = end_i
            continue

        if expr.startswith(r"\text{", i):
            inner, end_i = _parse_balanced_brace(expr, i + len(r"\text"))
            atom = rf"\text{{{inner}}}"
            if _is_degree_token(inner):
                out.append(_annotate_if_needed(atom, formula_key, line_key))
            else:
                out.append(atom)
            i = end_i
            continue
        out.append(expr[i])
        i += 1
    return "".join(out)

def _parse_keyed_atom(s: str, start: int) -> tuple[str, str, int, str] | None:
    # keyed atom form: \text{\textbf{Bb}}\{...\}
    if not s.startswith(r"\text{", start):
        return None
    key_inner, after_key = _parse_balanced_brace(s, start + len(r"\text"))
    m = re.match(r"^\s*\\textbf\{([^{}]+)\}\s*$", key_inner)
    if not m:
        return None
    if not (after_key + 1 < len(s) and s[after_key] == '\\' and s[after_key + 1] == '{'):
        return None

    j = after_key + 2
    depth = 1
    parts: list[str] = []
    while j < len(s):
        if j + 1 < len(s) and s[j] == '\\' and s[j + 1] == '{':
            depth += 1
            parts.append(r"\{")
            j += 2
            continue
        if j + 1 < len(s) and s[j] == '\\' and s[j + 1] == '}':
            depth -= 1
            if depth == 0:
                raw = s[start:j + 2]
                return m.group(1), "".join(parts), j + 2, raw
            parts.append(r"\}")
            j += 2
            continue
        parts.append(s[j])
        j += 1
    return None


def _merge_same_key_backslash(expr: str) -> str:
    # Simplify K{A}\K{B} -> K{A\B}
    out: list[str] = []
    i = 0
    while i < len(expr):
        left = _parse_keyed_atom(expr, i)
        if left is None:
            out.append(expr[i])
            i += 1
            continue

        key_l, inner_l, end_l, raw_l = left
        j = end_l
        ws1_start = j
        while j < len(expr) and expr[j].isspace():
            j += 1
        ws1 = expr[ws1_start:j]

        if not expr.startswith(r"\backslash", j):
            out.append(raw_l)
            i = end_l
            continue

        op_start = j
        j += len(r"\backslash")
        ws2_start = j
        while j < len(expr) and expr[j].isspace():
            j += 1
        ws2 = expr[ws2_start:j]

        right = _parse_keyed_atom(expr, j)
        if right is None:
            out.append(raw_l)
            i = end_l
            continue

        key_r, inner_r, end_r, raw_r = right
        if key_l != key_r:
            out.append(raw_l + ws1 + expr[op_start:op_start + len(r"\backslash")] + ws2 + raw_r)
            i = end_r
            continue

        merged = rf"\text{{\textbf{{{key_l}}}}}\{{{inner_l}\backslash{inner_r}\}}"
        out.append(merged)
        i = end_r

    return "".join(out)


def _parse_plain_degree_atom(s: str, start: int) -> tuple[str, int] | None:
    if not s.startswith(r"\text{", start):
        return None
    inner, end_i = _parse_balanced_brace(s, start + len(r"\text"))
    if not _is_degree_token(inner):
        return None
    return rf"\text{{{inner}}}", end_i


def _extract_single_keyed_succedent(chunk: str) -> tuple[str, str] | None:
    # Parse right side of ...\sststile{n}{}<RIGHT>; return (key, atom) only for a single keyed atom.
    m = re.search(r"\\sststile\{[^}]*\}\{\}", chunk)
    if not m:
        return None
    right = chunk[m.end():].strip()
    parsed = _parse_keyed_atom(right, 0)
    if parsed is None:
        return None
    key, inner, end_i, _raw = parsed
    if right[end_i:].strip():
        return None

    inner_s = inner.strip()
    plain = _parse_plain_degree_atom(inner_s, 0)
    if plain is None:
        return None
    atom, end_atom = plain
    if inner_s[end_atom:].strip():
        return None
    return key, atom


def _promote_plain_left_operand_from_pending(chunk: str, pending: tuple[str, str] | None) -> tuple[str, bool]:
    # One-step carry only: previous sequent succedent -> current line first matching plain left operand.
    if pending is None:
        return chunk, False

    key, atom = pending
    out: list[str] = []
    i = 0
    promoted = False

    while i < len(chunk):
        # Never inspect inside already-keyed chunks.
        keyed = _parse_keyed_atom(chunk, i)
        if keyed is not None:
            _k, _inner, end_i, raw = keyed
            out.append(raw)
            i = end_i
            continue

        if not promoted:
            plain = _parse_plain_degree_atom(chunk, i)
            if plain is not None:
                atom_i, end_i = plain
                j = end_i
                while j < len(chunk) and chunk[j].isspace():
                    j += 1
                if atom_i == atom and chunk.startswith(r"\backslash", j):
                    out.append(rf"\text{{\textbf{{{key}}}}}\{{{atom_i}\}}")
                    i = end_i
                    promoted = True
                    continue

        out.append(chunk[i])
        i += 1

    return ''.join(out), promoted


def _merge_same_key_comma(expr: str) -> str:
    # Simplify K{A}, K{B}, K{C} -> K{A, B, C}
    # but only for standalone premises. Do NOT merge a term if it is immediately
    # used as left operand of an outer \backslash (e.g., K{B}\backslash X).
    def _next_nonspace(s: str, idx: int) -> int:
        j = idx
        while j < len(s) and s[j].isspace():
            j += 1
        return j

    out: list[str] = []
    i = 0
    while i < len(expr):
        first = _parse_keyed_atom(expr, i)
        if first is None:
            out.append(expr[i])
            i += 1
            continue

        key, inner, end_i, raw = first

        # If first term is the right operand of an outer ...\backslash K{...},
        # keep it isolated (do not merge with following same-key chunks).
        left_ctx = expr[:i].rstrip()
        if left_ctx.endswith(r"\backslash"):
            out.append(raw)
            i = end_i
            continue

        # If first term is immediately used as K{...}\backslash..., keep it isolated.
        after_first = _next_nonspace(expr, end_i)
        if expr.startswith(r"\backslash", after_first):
            out.append(raw)
            i = end_i
            continue

        inners = [inner]
        j = end_i

        while True:
            k = _next_nonspace(expr, j)
            if k >= len(expr) or expr[k] != ',':
                break
            k += 1
            k = _next_nonspace(expr, k)

            nxt = _parse_keyed_atom(expr, k)
            if nxt is None:
                break
            n_key, n_inner, n_end, _n_raw = nxt
            if n_key != key:
                break

            after_n = _next_nonspace(expr, n_end)
            if expr.startswith(r"\backslash", after_n):
                # Keep this keyed term separate so K{...}\backslash... remains explicit.
                break

            inners.append(n_inner)
            j = n_end

        if len(inners) == 1:
            out.append(raw)
            i = end_i
            continue

        merged_inner = ', '.join(inners)
        out.append(rf"\text{{\textbf{{{key}}}}}\{{{merged_inner}\}}")
        i = j

    return ''.join(out)


def _transform_box_term(expr: str, i: int, line_key: Tonality, formula_key: Tonality) -> tuple[str, int]:
    # parse one term starting with \Box_X ... and return (converted, end_index)
    op_i = i + len(r"\Box_")
    if op_i >= len(expr):
        return expr[i:], len(expr)

    op = expr[op_i].upper()
    next_key = apply_op(formula_key, op) if op in {"D", "S", "R", "P"} else formula_key
    j = op_i + 1  # skip operator letter D/S/R/P
    while j < len(expr) and expr[j].isspace():
        j += 1

    # form: \Box_X\{ ... \}
    if j + 1 < len(expr) and expr[j] == '\\' and expr[j + 1] == '{':
        k = j + 2
        depth = 1
        inner: list[str] = []
        while k < len(expr):
            if k + 1 < len(expr) and expr[k] == '\\' and expr[k + 1] == '{':
                depth += 1
                inner.append(r"\{")
                k += 2
                continue
            if k + 1 < len(expr) and expr[k] == '\\' and expr[k + 1] == '}':
                depth -= 1
                if depth == 0:
                    k += 2
                    break
                inner.append(r"\}")
                k += 2
                continue
            inner.append(expr[k])
            k += 1

        inner_t = _transform_boxes("".join(inner), line_key, next_key)
        inner_t = _annotate_degree_atoms(inner_t, next_key, line_key)
        return inner_t, k

    # form: \Box_X{ ... }
    if j < len(expr) and expr[j] == "{":
        inner_raw, end_idx = _parse_balanced_brace(expr, j)
        inner_t = _transform_boxes(inner_raw, line_key, next_key)
        inner_t = _annotate_degree_atoms(inner_t, next_key, line_key)
        return inner_t, end_idx

    # form: \Box_X\text{...}
    if expr.startswith(r"\text{", j):
        inner_raw, end_idx = _parse_balanced_brace(expr, j + len(r"\text"))
        atom = rf"\text{{{inner_raw}}}"
        return _annotate_if_needed(atom, next_key, line_key), end_idx

    # form: \Box_X\Box_Y...
    if expr.startswith(r"\Box_", j):
        nested_t, end_idx = _transform_box_term(expr, j, line_key, next_key)
        nested_t = _annotate_degree_atoms(nested_t, next_key, line_key)
        return nested_t, end_idx

    # Unknown suffix: drop only this box operator and continue from suffix.
    # This avoids introducing placeholder '?' in output.
    return "", j


def _transform_boxes(expr: str, line_key: Tonality, formula_key: Tonality | None = None) -> str:
    if formula_key is None:
        formula_key = line_key
    i = 0
    out: list[str] = []
    while i < len(expr):
        if expr.startswith(r"\Box_", i):
            term_t, end_i = _transform_box_term(expr, i, line_key, formula_key)
            out.append(term_t)
            i = end_i
            continue

        out.append(expr[i])
        i += 1

    return "".join(out)


def _transform_sequent_line(line: str, current_key: Tonality | None, key_history: list[Tonality], pending_succedent: tuple[str, str] | None) -> tuple[str, tuple[str, str] | None, bool]:
    if current_key is None:
        return line, None, False

    # transform content inside $ ... $
    chunks = line.split("$")
    if len(chunks) < 3:
        return line, None, False

    next_pending: tuple[str, str] | None = None
    consumed = False
    for idx in range(1, len(chunks), 2):
        chunks[idx] = _transform_boxes(chunks[idx], current_key, current_key)
        chunks[idx], used_here = _promote_plain_left_operand_from_pending(chunks[idx], pending_succedent)
        consumed = consumed or used_here
        chunks[idx] = _merge_same_key_backslash(chunks[idx])
        chunks[idx] = _merge_same_key_comma(chunks[idx])

        cand = _extract_single_keyed_succedent(chunks[idx])
        if cand is not None:
            next_pending = cand

    return "$".join(chunks), next_pending, consumed


def _extract_last_sequent_chunk(lines: list[str]) -> str | None:
    for line in reversed(lines):
        if "\\sststile" not in line:
            continue
        chunks = line.split("$")
        for idx in range(len(chunks) - 1, 0, -1):
            if idx % 2 == 1 and "\\sststile" in chunks[idx]:
                return chunks[idx]
    return None


def _apply_explicit_final_key(chunk: str, final_key: Tonality) -> str:
    key = _latex_escape_text(tonality_to_str(final_key))
    i = 0
    out: list[str] = []

    while i < len(chunk):
        keyed = _parse_keyed_atom(chunk, i)
        if keyed is not None:
            _k, _inner, end_i, raw = keyed
            out.append(raw)
            i = end_i
            continue

        plain = _parse_plain_degree_atom(chunk, i)
        if plain is not None:
            atom, end_i = plain
            out.append(rf"\text{{\textbf{{{key}}}}}\{{{atom}\}}")
            i = end_i
            continue

        out.append(chunk[i])
        i += 1

    return ''.join(out)


def _append_explicit_final_line(lines: list[str], final_key: Tonality | None) -> list[str]:
    if final_key is None:
        return lines

    last_chunk = _extract_last_sequent_chunk(lines)
    if not last_chunk:
        return lines

    explicit = _apply_explicit_final_key(last_chunk, final_key)
    # Apply same merge policy used in converted sequents:
    # - merge K{A}\K{B} -> K{A\B}
    # - then merge adjacent standalone same-key terms.
    explicit = _merge_same_key_backslash(explicit)
    explicit = _merge_same_key_comma(explicit)
    add = [
        r"\doubleLine",
        r"\dashedLine",
        rf"\UnaryInfC{{$ {explicit} $}}",
    ]

    end_idx = None
    for i, ln in enumerate(lines):
        if ln.strip() == r"\end{scprooftree}" or ln.strip() == r"\end{prooftree}":
            end_idx = i
            break

    if end_idx is None:
        return lines + add

    return lines[:end_idx] + add + lines[end_idx:]


def _extract_last_leftlabel_key_from_tree(tex_path: Path) -> Tonality | None:
    text = tex_path.read_text(encoding="utf-8")
    current_key: Tonality | None = None
    for line in text.splitlines():
        if r"\LeftLabel" in line:
            k = _extract_left_label_key(line)
            if k is not None:
                current_key = k
    return current_key


def _resolve_last_line_entry_key(entry_id: str, standards_root: Path) -> Tonality | None:
    raw = entry_id.replace(r"\_", "_")
    section_name = raw
    standard_slug = ""
    if "::" in raw:
        standard_slug, section_name = raw.split("::", 1)

    candidates: list[Path] = []
    if standard_slug:
        for folder in standards_root.glob(f"{standard_slug}*"):
            if folder.is_dir():
                tf = folder / f"{section_name}.tex"
                if tf.exists():
                    candidates.append(tf)
    else:
        candidates = list(standards_root.rglob(f"{section_name}.tex"))

    if not candidates:
        return None
    chosen = max(candidates, key=lambda pp: pp.stat().st_mtime)
    return _extract_last_leftlabel_key_from_tree(chosen)


def _is_last_line_analysis_tex(lines: list[str]) -> bool:
    return any(LAST_LINE_ENTRY_RE.match(ln) for ln in lines)


def _force_landscape_layout(lines: list[str]) -> list[str]:
    out = list(lines)
    geom_re = re.compile(r"^\\usepackage(?:\[([^\]]*)\])?\{geometry\}\s*$")

    for i, ln in enumerate(out):
        m = geom_re.match(ln.strip())
        if not m:
            continue
        opts_raw = (m.group(1) or '').strip()
        opts = [x.strip() for x in opts_raw.split(',') if x.strip()]
        if 'landscape' not in opts:
            opts.insert(0, 'landscape')
        opts_str = ','.join(opts) if opts else 'landscape'
        out[i] = rf"\usepackage[{opts_str}]{{geometry}}"
        return out

    insert_at = None
    for i, ln in enumerate(out):
        if ln.strip().startswith('\\documentclass'):
            insert_at = i + 1
            break
    if insert_at is None:
        insert_at = 0
    out.insert(insert_at, r"\usepackage[landscape,margin=1in]{geometry}")
    return out


def _simplify_last_line_analysis_file(in_tex: Path, out_tex: Path, standards_root: Path) -> None:
    text = in_tex.read_text(encoding="utf-8")
    out_lines: list[str] = []
    key_cache: dict[str, Tonality | None] = {}

    for line in text.splitlines():
        m = LAST_LINE_ENTRY_RE.match(line)
        if not m:
            out_lines.append(line)
            continue
        entry_id, chunk = m.group(1), m.group(2)
        if entry_id not in key_cache:
            key_cache[entry_id] = _resolve_last_line_entry_key(entry_id, standards_root)
        key = key_cache[entry_id]
        if key is None:
            out_lines.append(line)
            continue

        transformed = _transform_boxes(chunk, key, key)
        transformed = _merge_same_key_backslash(transformed)
        transformed = _merge_same_key_comma(transformed)
        out_lines.append(f'"{entry_id}" "$ {transformed} $"\\\\')
        out_lines.append("")

    out_lines = _force_landscape_layout(out_lines)
    out_tex.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


def simplify_tex_text(text: str, standards_root: Path) -> str:
    """
    Simplify a TEX document string and return the converted TEX string.
    Mirrors simplify_tex_file() behavior without filesystem I/O.
    """
    lines = text.splitlines()
    if _is_last_line_analysis_tex(lines):
        out_lines: list[str] = []
        key_cache: dict[str, Tonality | None] = {}

        for line in lines:
            m = LAST_LINE_ENTRY_RE.match(line)
            if not m:
                out_lines.append(line)
                continue
            entry_id, chunk = m.group(1), m.group(2)
            if entry_id not in key_cache:
                key_cache[entry_id] = _resolve_last_line_entry_key(entry_id, standards_root)
            key = key_cache[entry_id]
            if key is None:
                out_lines.append(line)
                continue

            transformed = _transform_boxes(chunk, key, key)
            transformed = _merge_same_key_backslash(transformed)
            transformed = _merge_same_key_comma(transformed)
            out_lines.append(f'"{entry_id}" "$ {transformed} $"\\\\')
            out_lines.append("")

        out_lines = _force_landscape_layout(out_lines)
        return "\n".join(out_lines) + "\n"

    current_key: Tonality | None = None
    key_history: list[Tonality] = []
    pending_succedent: tuple[str, str] | None = None
    pending_steps: int = 0
    out_lines: list[str] = []

    for line in lines:
        if "\\LeftLabel" in line:
            k = _extract_left_label_key(line)
            if k is not None:
                if current_key is not None and k != current_key:
                    key_history.append(current_key)
                current_key = k
                out_lines.append(_simplify_left_label(line, k))
                continue

        if any(tag in line for tag in ("\\UnaryInfC", "\\BinaryInfC", "\\AxiomC")):
            transformed, next_pending, consumed = _transform_sequent_line(line, current_key, key_history, pending_succedent)
            out_lines.append(transformed)
            if next_pending is not None:
                pending_succedent = next_pending
                pending_steps = 3
            elif consumed and pending_succedent is not None:
                pending_steps = 3
            elif pending_succedent is not None:
                pending_steps = max(0, pending_steps - 1)
                if pending_steps == 0:
                    pending_succedent = None
        else:
            out_lines.append(line)

    out_lines = _append_explicit_final_line(out_lines, current_key)
    out_lines = _force_landscape_layout(out_lines)
    return "\n".join(out_lines) + "\n"


def simplify_tex_file(in_tex: Path, out_tex: Path, standards_root: Path) -> None:
    text = in_tex.read_text(encoding="utf-8")
    out_tex.write_text(simplify_tex_text(text, standards_root), encoding="utf-8")


def _find_latest_standard_folder(root: Path, standard_name: str) -> Path:
    slug = _slugify(standard_name)
    candidates = [p for p in root.glob(f"{slug}*") if p.is_dir()]
    if not candidates:
        raise FileNotFoundError(f"No folder found for standard '{standard_name}' in {root}")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def main() -> int:
    parser = argparse.ArgumentParser(description="Simplify Box/R-heavy proof-tree TEX into readability-oriented TEX.")
    parser.add_argument("--input", default="", help="Single TEX file to simplify.")
    parser.add_argument("--standard", default="", help="Standard name; simplifies all TEX in latest standard output folder.")
    parser.add_argument("--in-root", default="standard_outputs", help="Root folder containing per-standard TEX outputs.")
    parser.add_argument("--standard-root", default="standard_outputs", help="Root folder used to resolve section keys for last_line_analysis input.")
    parser.add_argument("--out-root", default="readable_outputs", help="Destination root for simplified TEX outputs.")
    parser.add_argument("--dpi", type=int, default=600, help="PNG render DPI for readable outputs (default: 600).")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    in_root = Path(args.in_root)
    if not in_root.is_absolute():
        in_root = script_dir / in_root

    standard_root = Path(args.standard_root)
    if not standard_root.is_absolute():
        standard_root = script_dir / standard_root

    out_root = Path(args.out_root)
    if not out_root.is_absolute():
        out_root = script_dir / out_root
    out_root.mkdir(parents=True, exist_ok=True)

    if args.input:
        in_tex = Path(args.input)
        if not in_tex.is_absolute():
            in_tex = script_dir / in_tex
        if not in_tex.exists():
            raise FileNotFoundError(f"Input TEX not found: {in_tex}")
        out_dir = _next_available_dir(out_root / _slugify(in_tex.stem))
        out_dir.mkdir(parents=True, exist_ok=False)
        out_tex = out_dir / in_tex.name
        simplify_tex_file(in_tex, out_tex, standard_root)
        out_png = out_tex.with_suffix('.png')
        ok, msg = _render_png_from_tex(out_tex, out_png, max(300, args.dpi))
        print(f"Input:  {in_tex}")
        print(f"Output TEX: {out_tex}")
        if ok:
            print(f"Output PNG: {out_png}")
        else:
            print(f"Warning: {msg}")
        return 0

    if args.standard:
        latest = _find_latest_standard_folder(in_root, args.standard)
        out_dir = _next_available_dir(out_root / latest.name)
        out_dir.mkdir(parents=True, exist_ok=False)

        tex_files = sorted(latest.glob("*.tex"))
        if not tex_files:
            raise ValueError(f"No TEX files found in {latest}")

        print(f"Standard source folder: {latest}")
        print(f"Readable output folder: {out_dir}")
        for tf in tex_files:
            out_tex = out_dir / tf.name
            simplify_tex_file(tf, out_tex, standard_root)
            out_png = out_tex.with_suffix('.png')
            ok, msg = _render_png_from_tex(out_tex, out_png, max(300, args.dpi))
            if ok:
                print(f"- {tf.name} -> {out_tex.name} + {out_png.name}")
            else:
                print(f"- {tf.name} -> {out_tex.name} (PNG skipped: {msg})")
        return 0

    raise ValueError("Use --input <file.tex> or --standard <name>.")


if __name__ == "__main__":
    raise SystemExit(main())
