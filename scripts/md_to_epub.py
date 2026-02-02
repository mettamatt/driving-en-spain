#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _which(cmd: str) -> str | None:
    return shutil.which(cmd)


def _run(cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Required command not found: {cmd[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Command failed (exit={exc.returncode}): {cmd[0]}") from exc


def _sanitize_for_xhtml(text: str) -> str:
    """
    EPUB 3 content documents are XHTML (XML), so void tags must be self-closing.

    Our source Markdown includes raw HTML like <br> inside tables (from marker output),
    which is valid HTML but invalid XML, and causes Apple Books/WebKit to show a parse error.
    """

    # Make <br> XML-safe. Preserve already self-closed forms (<br/> / <br />).
    text = re.sub(r"<br\s*>", "<br />", text, flags=re.IGNORECASE)
    return text


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Render a Markdown file to EPUB 3.\n\n"
            "By default this sanitizes raw HTML (<br> -> <br />) so the output XHTML is\n"
            "well-formed and works in Apple Books."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("in_md", type=Path, help="Input Markdown file")
    parser.add_argument("out_epub", type=Path, help="Output EPUB path")
    parser.add_argument(
        "--resource-root",
        type=Path,
        default=None,
        help="Base directory for relative images/links (default: input file's folder).",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="EPUB title (default: input filename).",
    )
    parser.add_argument(
        "--toc",
        action="store_true",
        help="Generate a table of contents from headings.",
    )
    parser.add_argument(
        "--split-level",
        type=int,
        default=2,
        help="Split level for chapters (pandoc --split-level) (default: 2).",
    )
    parser.add_argument(
        "--no-sanitize",
        action="store_true",
        help="Disable XHTML sanitization (not recommended for Apple Books).",
    )

    args = parser.parse_args(argv)

    pandoc = _which("pandoc")
    if not pandoc:
        print("ERROR: pandoc is required. Install it (e.g. `brew install pandoc`).", file=sys.stderr)
        return 2

    in_md = args.in_md.expanduser()
    if not in_md.exists():
        print(f"ERROR: Input file not found: {in_md}", file=sys.stderr)
        return 2

    out_epub = args.out_epub.expanduser().resolve()
    resource_root = (args.resource_root or in_md.parent).expanduser()
    title = args.title or in_md.stem

    try:
        text = in_md.read_text(encoding="utf-8")
    except Exception as exc:
        print(f"ERROR: Failed to read {in_md}: {exc}", file=sys.stderr)
        return 2

    if not args.no_sanitize:
        text = _sanitize_for_xhtml(text)

    with tempfile.TemporaryDirectory(prefix="md_to_epub_") as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        tmp_md = tmp_dir_path / "input.sanitized.md"
        tmp_md.write_text(text, encoding="utf-8")

        out_epub.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            pandoc,
            str(tmp_md),
            "--from=gfm+raw_html",
            "--to=epub3",
            "--resource-path",
            str(resource_root),
            "--metadata",
            f"title={title}",
            "--split-level",
            str(args.split_level),
            "-o",
            str(out_epub),
        ]
        if args.toc:
            cmd.append("--toc")

        _run(cmd)

    print(f"[ok] wrote {out_epub}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

