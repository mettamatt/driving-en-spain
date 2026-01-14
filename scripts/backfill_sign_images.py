#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from marker_extract_to_md import (
    _convert_marker_pagination_to_page_markers,
    _maybe_backfill_service_signs,
)


def _relpath_posix(target: Path, base_dir: Path) -> str:
    rel = os.path.relpath(str(target), str(base_dir))
    return rel.replace(os.sep, "/")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill missing service sign icons (e.g. S-107..S-120) into an existing Markdown "
            "file by extracting the icon images from the source PDF (via PyMuPDF)."
        )
    )
    parser.add_argument("pdf_path", type=Path, help="Source PDF.")
    parser.add_argument("in_md", type=Path, help="Input Markdown file (Spanish or English).")
    parser.add_argument(
        "out_md",
        type=Path,
        nargs="?",
        default=None,
        help="Output Markdown file (default: in-place when --in-place is set).",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Write changes back to the input Markdown file.",
    )
    parser.add_argument(
        "--image-dir",
        type=Path,
        default=None,
        help="Directory to write extracted PNGs (default: alongside out_md).",
    )
    parser.add_argument(
        "--no-convert-marker-pagination",
        action="store_true",
        help="Do not convert marker pagination lines ({N}-----) into <!-- Page: N --> comments.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write files; just report what would change.",
    )

    args = parser.parse_args(argv)

    pdf_path: Path = args.pdf_path
    if not pdf_path.exists() or not pdf_path.is_file():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        return 2

    in_md: Path = args.in_md
    if not in_md.exists() or not in_md.is_file():
        print(f"ERROR: Input Markdown not found: {in_md}", file=sys.stderr)
        return 2

    if args.out_md is None and not args.in_place:
        print("ERROR: Provide out_md or pass --in-place.", file=sys.stderr)
        return 2

    out_md = args.out_md or in_md
    image_dir = args.image_dir or out_md.parent
    image_dir.mkdir(parents=True, exist_ok=True)

    md_text = in_md.read_text(encoding="utf-8")
    if not args.no_convert_marker_pagination:
        md_text = _convert_marker_pagination_to_page_markers(md_text)

    prefix = _relpath_posix(image_dir, out_md.parent)
    md_image_prefix = "" if prefix in {"", "."} else prefix.rstrip("/") + "/"

    try:
        patched, images_written = _maybe_backfill_service_signs(
            md_text,
            pdf_path=pdf_path,
            output_folder=image_dir,
            md_image_prefix=md_image_prefix,
        )
    except Exception as exc:
        print(f"ERROR: Backfill failed: {exc}", file=sys.stderr)
        return 2

    changed = patched != md_text

    if args.dry_run:
        print(f"changed={changed} images_written={images_written} image_dir={image_dir}")
        return 0

    if changed:
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(patched, encoding="utf-8")
        print(f"[ok] wrote {out_md}")
    else:
        print("[skip] no markdown changes")

    if images_written:
        print(f"[ok] wrote {images_written} PNG(s) into {image_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

