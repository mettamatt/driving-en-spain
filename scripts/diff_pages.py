#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path


PAGE_MARKER_RE = re.compile(r"^\s*<!--\s*Page:\s*(\d+)\s*-->\s*$")


def _parse_page_spec(spec: str) -> list[int]:
    pages: set[int] = set()
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start = int(a.strip())
            end = int(b.strip())
            if end < start:
                start, end = end, start
            pages.update(range(start, end + 1))
            continue
        pages.add(int(part))
    return sorted(pages)


def _split_by_page(markdown: str) -> dict[int, list[str]]:
    pages: dict[int, list[str]] = {}
    current: int | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        if current is None:
            return
        pages[current] = list(buf)
        buf = []

    for line in markdown.splitlines():
        m = PAGE_MARKER_RE.match(line)
        if m:
            flush()
            current = int(m.group(1))
            buf = [line]
            continue
        if current is None:
            continue
        buf.append(line)

    flush()
    return pages


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Diff specific page segments between two Markdown files using <!-- Page: N --> markers."
    )
    parser.add_argument("a_md", type=Path, help="First Markdown file")
    parser.add_argument("b_md", type=Path, help="Second Markdown file")
    parser.add_argument(
        "--pages",
        required=True,
        help="Comma-separated list/ranges, e.g. 8,65,69 or 8-10,65",
    )

    args = parser.parse_args(argv)
    if not args.a_md.exists():
        print(f"ERROR: file not found: {args.a_md}", file=sys.stderr)
        return 2
    if not args.b_md.exists():
        print(f"ERROR: file not found: {args.b_md}", file=sys.stderr)
        return 2

    pages = _parse_page_spec(args.pages)
    if not pages:
        print("ERROR: no pages specified", file=sys.stderr)
        return 2

    a_pages = _split_by_page(args.a_md.read_text(encoding="utf-8"))
    b_pages = _split_by_page(args.b_md.read_text(encoding="utf-8"))

    any_diff = False
    for p in pages:
        a_seg = a_pages.get(p, [])
        b_seg = b_pages.get(p, [])
        if a_seg == b_seg:
            continue
        any_diff = True
        fromfile = f"{args.a_md} (Page {p})"
        tofile = f"{args.b_md} (Page {p})"
        diff = difflib.unified_diff(
            a_seg,
            b_seg,
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
        )
        for line in diff:
            print(line)

    return 1 if any_diff else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

