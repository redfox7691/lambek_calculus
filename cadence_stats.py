#!/usr/bin/env python3
"""
Compute cadence frequency statistics from last_line_analysis TXT output.

Expected input format:
  Last Line Analysis: <title>
  <section_id>\t<readable_sequent_with_operators>

Example:
  python3 cadence_stats.py --input "last_line_analysis/JazzStandards_all.txt"
  python3 cadence_stats.py --input "last_line_analysis/JazzStandards_all.txt" --top 15
  python3 cadence_stats.py --input "last_line_analysis/JazzStandards_all.txt" --csv-out "last_line_analysis/cadence_stats.csv"
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path
import re
from typing import Iterable


ROMAN_TOKEN = r"(?:[#b]?[IViv]+)"
LINK_RE = re.compile(rf"({ROMAN_TOKEN})\s*\\\s*({ROMAN_TOKEN})")


def _extract_section_payloads(lines: Iterable[str]) -> list[str]:
    payloads: list[str] = []
    for line in lines:
        if "\t" not in line:
            continue
        _section_id, payload = line.split("\t", 1)
        payloads.append(payload.strip())
    return payloads


def _extract_left_side(payload: str) -> str:
    # Only analyze the antecedent side before the final turnstile marker.
    return payload.split("|- (depth=", 1)[0]


def _count_links_and_chains(
    payloads: Iterable[str],
    include_self: bool = False,
) -> tuple[Counter[str], Counter[str]]:
    link_counts: Counter[str] = Counter()
    chain_counts: Counter[str] = Counter()

    for payload in payloads:
        left = _extract_left_side(payload)
        links = LINK_RE.findall(left)
        if not links:
            continue

        if include_self:
            link_counts.update(f"{a} -> {b}" for a, b in links)
        else:
            link_counts.update(f"{a} -> {b}" for a, b in links if a != b)

        # Adjacent shared-middle chains: a->b and b->c => a->b->c
        for (a, b), (c, d) in zip(links, links[1:]):
            if b == c:
                if include_self or not (a == b == d):
                    chain_counts[f"{a} -> {b} -> {d}"] += 1

    return link_counts, chain_counts


def _print_ranked(title: str, counts: Counter[str], top_n: int) -> None:
    total = sum(counts.values())
    print(title)
    print(f"Total: {total}")
    if total == 0:
        print("(no matches)")
        print()
        return

    for idx, (name, value) in enumerate(counts.most_common(top_n), start=1):
        pct = 100.0 * value / total
        print(f"{idx:>2}. {name:<24} {value:>8}  {pct:>6.2f}%")
    print()


def _write_csv(out_path: Path, links: Counter[str], chains: Counter[str]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    total_links = sum(links.values())
    total_chains = sum(chains.values())

    with out_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["category", "pattern", "count", "percentage"])

        for pattern, count in links.most_common():
            pct = (100.0 * count / total_links) if total_links else 0.0
            w.writerow(["single_link", pattern, count, f"{pct:.6f}"])

        for pattern, count in chains.most_common():
            pct = (100.0 * count / total_chains) if total_chains else 0.0
            w.writerow(["three_step_chain", pattern, count, f"{pct:.6f}"])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract cadence frequencies and percentages from last_line_analysis TXT files."
    )
    parser.add_argument("--input", required=True, help="Path to last_line_analysis TXT file.")
    parser.add_argument("--top", type=int, default=20, help="Top N patterns to print (default: 20).")
    parser.add_argument("--csv-out", default="", help="Optional CSV output path for full ranked tables.")
    parser.add_argument(
        "--include-self",
        action="store_true",
        help="Include self-links/chains such as I -> I and I -> I -> I (default: excluded).",
    )
    args = parser.parse_args()

    in_path = Path(args.input)
    if not in_path.is_absolute():
        in_path = Path(__file__).resolve().parent / in_path
    if not in_path.exists():
        raise FileNotFoundError(f"Input file not found: {in_path}")

    lines = in_path.read_text(encoding="utf-8").splitlines()
    payloads = _extract_section_payloads(lines)
    links, chains = _count_links_and_chains(payloads, include_self=args.include_self)

    print(f"Input: {in_path}")
    print(f"Sections parsed: {len(payloads)}")
    print()

    _print_ranked("Top Single Cadence Links", links, max(1, args.top))
    _print_ranked("Top Three-Step Cadence Chains", chains, max(1, args.top))

    if args.csv_out:
        out_path = Path(args.csv_out)
        if not out_path.is_absolute():
            out_path = Path(__file__).resolve().parent / out_path
        _write_csv(out_path, links, chains)
        print(f"CSV written to: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
