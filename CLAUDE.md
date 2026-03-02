# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This project applies **Lambek Calculus** (a substructural logic) to model harmonic progressions in jazz. Chords are mapped to Roman numeral grades, modulations are routed on a 24-key D/S/R/P tonality lattice, and proof trees are generated as LaTeX/PNG outputs.

## Environment Requirements

- **Python 3** (no pip packages required for core scripts)
- **pdflatex** with packages: `amsmath`, `amssymb`, `graphicx`, `bussproofs`, `stmaryrd`, `turnstile`, `geometry`, `fontenc`, `inputenc`
- **Ghostscript (`gs`)** for PNG export (or `sips` on macOS)
- **Graphviz (`dot`)** for route map PNG export (optional)

Run the environment checker:
```bash
bash requirements_check.sh
```

## Core Scripts and Their Roles

| Script | Role |
|---|---|
| `chord_grade.py` | Maps chord symbols → Roman numeral grades in a given key |
| `tonality_route.py` | BFS shortest-path routing on the 24-key D/S/R/P lattice |
| `lambek_tree.py` | Main entry point: generates Lambek proof trees (imports all others) |
| `simplify_tree_notation.py` | Post-processor: converts formal TEX → readable TEX + PNG |
| `cadence_stats.py` | Analyzes `last_line_analysis` TXT files for cadence frequencies |
| `generate_chords.py` | Reverse process: generates chord sequences from a tonality |

**Dependency chain:** `lambek_tree.py` imports from `chord_grade`, `tonality_route`, `simplify_tree_notation`, and `cadence_stats`.

## Key Commands

### Validate scripts compile
```bash
python3 -m py_compile chord_grade.py lambek_tree.py tonality_route.py simplify_tree_notation.py
```

### Generate proof tree from a chord sequence
```bash
python3 lambek_tree.py --sequence D7 G7 Cmaj7
```

### Analyze a jazz standard
```bash
python3 lambek_tree.py --standard "Alone Together"
python3 lambek_tree.py --standard-readable "Alone Together"   # also creates readable version
```

### Last-line analysis (fast mode)
```bash
python3 lambek_tree.py analyse "Alone Together"
python3 lambek_tree.py analyse "JazzStandards-main/JazzStandards" --format txt --cadence-csv "last_line_analysis/out.csv"
```

### Cadence statistics
```bash
python3 cadence_stats.py --input "last_line_analysis/JazzStandards_all.txt" --csv-out "last_line_analysis/cadence_stats.csv"
```

### Generate chord progressions (reverse)
```bash
python3 generate_chords.py --tonality C --target-chords 8 --png --png-dpi 900
```

### Readable converter (post-process existing TEX)
```bash
python3 simplify_tree_notation.py --standard "After You've Gone"
python3 simplify_tree_notation.py --input path/to/tree.tex
```

### Tonality route
```bash
python3 tonality_route.py --from C --to a
python3 tonality_route.py png --from C --to a --out-dir route_maps
```

### Web app
```bash
bash run_webapp.sh           # starts FastAPI server at http://127.0.0.1:8000
# or manually:
python3 -m pip install -r webapp/requirements.txt
python3 webapp/backend.py
```

## Output Directory Structure

| Mode | Output location |
|---|---|
| `--sequence` | `tree_outputs/` (or `--out-dir`) |
| `--standard` | `standard_outputs/<StandardName>[_1, _2, ...]` |
| `--standard-readable` | `standard_outputs_readable/<StandardName>...` |
| `simplify_tree_notation.py` | `readable_outputs/` |
| `analyse` | `last_line_analysis/` |
| `generate_chords.py` | `generated_chords/` |
| `tonality_route.py png` | `route_maps/` |

## Architecture: Tonality Lattice (D/S/R/P)

The 24 keys (12 major + 12 minor) form a lattice with four accessibility relations:
- **D** – dominant (up a fifth)
- **S** – subdominant (down a fifth)
- **R** – relative (major ↔ minor, same key signature)
- **P** – parallel (major ↔ minor, same root)

`tonality_route.py` uses BFS with a simplicity preference: shortest path, then fewest R steps, then fewest non-D/S steps, then D/S > P > R.

Left-labels in proof trees show the relation chain and resolved key: `R_{...}Base = Target`.

## Architecture: Proof Tree Generation (`lambek_tree.py`)

1. **Tonality inference** – prioritizes opening ii-V-I/ii-V evidence; falls back to diatonic-coherence scoring.
2. **Chord normalization** – enharmonic normalization, slash-bass stripping, parenthetical alternate expansion, contraction of repeated chords/blocks.
3. **Modulation detection** – uses the D/S/R/P lattice; modulation steps are emitted as boxed labels in the tree.
4. **Tree rendering** – outputs `bussproofs`-style LaTeX; auto-scales (`\scprooftree`) from 0.9 down to 0.4 for large trees.
5. **PNG export** – calls `pdflatex` then `gs` (or `sips`) in a temp directory.

## Chord Parsing Rules

- `MA7` = `maj7`; slash bass ignored for function (`CMA7/G` → `CMA7`)
- Parenthetical alternates expanded: `Em7b5(Gm7)` → `Em7b5` + `Gm7`
- `bII7 → I` recognized as dominant-functional resolution but graded literally
- Diminished (`dim`/`o`/`°`) treated as dominant-like; minor/diminished use lowercase Roman numerals
- Half-diminished: `m7b5`, `m7(b5)`, `ø7`, `07`

## Shell Note

In zsh, quote chords with parentheses to prevent shell interpretation:
```bash
python3 lambek_tree.py --sequence 'Dm7(b5)' G7 Cm7
```

## Maintenance Protocol

When adding features:
1. Update `PROJECT_NOTES.txt`
2. Update `instructions.txt`
3. Update and recompile `program_paper/Lambek_Calculus_Program_Notes.tex`
4. Run validation: `python3 -m py_compile` + one `--sequence` test + one `--standard` test
