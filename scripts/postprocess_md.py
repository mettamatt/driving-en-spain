#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from markdown_postprocess import postprocess_markdown


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Post-process a Markdown file (paragraph reflow + known table conversions)."
    )
    parser.add_argument("in_md", type=Path, help="Input Markdown file")
    parser.add_argument("out_md", type=Path, help="Output Markdown file")
    parser.add_argument(
        "--style",
        default="paragraphs",
        choices=["paragraphs", "preserve"],
        help="Output style (default: paragraphs).",
    )
    parser.add_argument(
        "--no-tables",
        action="store_true",
        help="Disable known-table conversions.",
    )

    args = parser.parse_args(argv)

    if not args.in_md.exists():
        print(f"ERROR: Input file not found: {args.in_md}", file=sys.stderr)
        return 2

    text = args.in_md.read_text(encoding="utf-8")
    processed = postprocess_markdown(
        text,
        style=args.style,
        enable_tables=not args.no_tables,
    )

    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.write_text(processed, encoding="utf-8")
    print(f"[ok] wrote {args.out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

