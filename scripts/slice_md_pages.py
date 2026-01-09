#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


PAGE_MARKER_RE = re.compile(r"<!--\s*Page:\s*(\d+)\s*-->")


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


def _split_into_page_segments(markdown: str) -> list[str]:
    matches = list(PAGE_MARKER_RE.finditer(markdown))
    if not matches:
        return [markdown.strip() + "\n"]

    segments: list[str] = []
    pre = markdown[: matches[0].start()].strip()
    if pre:
        segments.append(pre + "\n")

    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        seg = markdown[start:end].strip()
        if seg:
            segments.append(seg + "\n")
    return segments


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Write a new Markdown file containing only the selected <!-- Page: N --> segments."
    )
    parser.add_argument("in_md", type=Path, help="Input Markdown file")
    parser.add_argument("out_md", type=Path, help="Output Markdown file")
    parser.add_argument(
        "--pages",
        required=True,
        help="Comma-separated list/ranges, e.g. 8,65,69 or 8-10,65",
    )
    parser.add_argument(
        "--include-preface",
        action="store_true",
        help="Include content before the first page marker (default: omit).",
    )

    args = parser.parse_args(argv)
    if not args.in_md.exists():
        print(f"ERROR: Input file not found: {args.in_md}", file=sys.stderr)
        return 2

    pages = set(_parse_page_spec(args.pages))
    if not pages:
        print("ERROR: no pages specified", file=sys.stderr)
        return 2

    raw = args.in_md.read_text(encoding="utf-8")
    segments = _split_into_page_segments(raw)

    out_segments: list[str] = []
    for seg in segments:
        m = PAGE_MARKER_RE.search(seg)
        if not m:
            if args.include_preface:
                out_segments.append(seg.rstrip() + "\n")
            continue
        page_num = int(m.group(1))
        if page_num in pages:
            out_segments.append(seg.rstrip() + "\n")

    if not out_segments:
        print("ERROR: no matching page segments found in input.", file=sys.stderr)
        return 2

    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.write_text("\n".join(s.rstrip() for s in out_segments).rstrip() + "\n", encoding="utf-8")
    print(f"[ok] wrote {args.out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

