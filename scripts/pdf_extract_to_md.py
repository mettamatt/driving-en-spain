#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PageText:
    page_number: int  # 1-based
    lines: list[str]


def _is_standalone_page_number(line: str) -> bool:
    line = line.strip()
    return bool(re.fullmatch(r"\d{1,4}", line))


def _normalize_line(line: str) -> str:
    line = line.replace("\u00a0", " ")  # NBSP
    line = line.replace("\t", " ")
    line = re.sub(r"[ ]{2,}", " ", line)
    return line.strip()


def _as_md_bullet(line: str) -> str:
    m = re.match(r"^\s*([■●•◦▪▫–—-])\s*(.+)$", line)
    if not m:
        return line
    return f"- {m.group(2).strip()}"


def _lines_from_text(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    raw_lines = text.split("\n")
    lines: list[str] = []
    for raw in raw_lines:
        norm = _normalize_line(raw)
        if norm == "":
            lines.append("")
        else:
            lines.append(_as_md_bullet(norm))
    # Collapse >2 consecutive blank lines
    collapsed: list[str] = []
    blank_run = 0
    for line in lines:
        if line == "":
            blank_run += 1
            if blank_run <= 2:
                collapsed.append("")
            continue
        blank_run = 0
        collapsed.append(line)
    # Trim leading/trailing blanks
    while collapsed and collapsed[0] == "":
        collapsed.pop(0)
    while collapsed and collapsed[-1] == "":
        collapsed.pop()

    # Drop consecutive duplicate non-numeric lines (common PDF extraction artifact).
    deduped: list[str] = []
    prev: str | None = None
    for line in collapsed:
        if line and prev == line and not _is_standalone_page_number(line):
            continue
        deduped.append(line)
        prev = line

    return deduped


def _extract_pages_pymupdf(pdf_path: Path) -> list[PageText]:
    try:
        import fitz  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "PyMuPDF is not installed. Install it with: python -m pip install pymupdf"
        ) from exc

    doc = fitz.open(str(pdf_path))
    pages: list[PageText] = []
    for i in range(doc.page_count):
        page = doc.load_page(i)
        text = page.get_text("text") or ""
        pages.append(PageText(page_number=i + 1, lines=_lines_from_text(text)))
    return pages


def _extract_pages_pypdf(pdf_path: Path) -> list[PageText]:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "pypdf is not installed. Install it with: python -m pip install pypdf"
        ) from exc

    reader = PdfReader(str(pdf_path))
    pages: list[PageText] = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        pages.append(PageText(page_number=i + 1, lines=_lines_from_text(text)))
    return pages


def _first_nonempty_line(lines: list[str]) -> str | None:
    for line in lines:
        if line.strip() != "":
            return line
    return None


def _last_nonempty_line(lines: list[str]) -> str | None:
    for line in reversed(lines):
        if line.strip() != "":
            return line
    return None


def _looks_like_section_heading(line: str) -> bool:
    if re.match(r"^Tema\s+\d+\.\s+.+$", line, flags=re.IGNORECASE):
        return True
    if re.match(r"^Anexo\b", line, flags=re.IGNORECASE):
        return True
    if re.match(r"^Índice\b", line, flags=re.IGNORECASE) or re.match(
        r"^Indice\b", line, flags=re.IGNORECASE
    ):
        return True
    if re.match(r"^Introducción\b", line, flags=re.IGNORECASE):
        return True
    return False


def _clean_page_lines(
    page: PageText,
    repetitive_headers: set[str],
    repetitive_footers: set[str],
) -> tuple[list[str], str | None]:
    """
    Returns (cleaned_lines, section_heading_candidate).
    """
    lines = list(page.lines)

    # Drop a standalone page number line at the top.
    def drop_top_page_numbers() -> None:
        nonlocal lines
        while lines and _is_standalone_page_number(lines[0]):
            lines.pop(0)
            while lines and lines[0] == "":
                lines.pop(0)

    drop_top_page_numbers()

    section_candidate = _first_nonempty_line(lines)
    if section_candidate and not _looks_like_section_heading(section_candidate):
        section_candidate = None

    # Drop repetitive header/footer if they appear in the top/bottom position.
    if lines:
        top = _first_nonempty_line(lines)
        if top and top in repetitive_headers:
            # Remove only the first occurrence near the top.
            for i, line in enumerate(lines[:10]):
                if line == top:
                    lines.pop(i)
                    break
            while lines and lines[0] == "":
                lines.pop(0)
            drop_top_page_numbers()

    if lines:
        bottom = _last_nonempty_line(lines)
        if bottom and bottom in repetitive_footers:
            for i in range(len(lines) - 1, max(-1, len(lines) - 10), -1):
                if lines[i] == bottom:
                    lines.pop(i)
                    break
            while lines and lines[-1] == "":
                lines.pop()

    # If we're going to emit the section heading, drop it from the page body to avoid repeats.
    if section_candidate and lines and lines[0] == section_candidate:
        lines.pop(0)
        while lines and lines[0] == "":
            lines.pop(0)
        drop_top_page_numbers()

    return (lines, section_candidate)


def _compute_repetitive_headers_and_footers(
    pages: list[PageText],
    min_ratio: float,
    min_count: int,
) -> tuple[set[str], set[str]]:
    header_counter: Counter[str] = Counter()
    footer_counter: Counter[str] = Counter()

    for page in pages:
        lines = list(page.lines)
        while lines and _is_standalone_page_number(lines[0]):
            lines.pop(0)
            while lines and lines[0] == "":
                lines.pop(0)

        top = _first_nonempty_line(lines)
        bottom = _last_nonempty_line(lines)
        if top:
            header_counter[top] += 1
        if bottom:
            footer_counter[bottom] += 1

    threshold = max(min_count, int(len(pages) * min_ratio))
    repetitive_headers = {line for line, count in header_counter.items() if count >= threshold}
    repetitive_footers = {line for line, count in footer_counter.items() if count >= threshold}

    return repetitive_headers, repetitive_footers


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Extract PDF text into a clean-ish Markdown file for translation/search."
    )
    parser.add_argument("pdf", type=Path, help="Input PDF path")
    parser.add_argument("out_md", type=Path, help="Output Markdown path")
    parser.add_argument(
        "--backend",
        choices=["pymupdf", "pypdf"],
        default="pymupdf",
        help="Extraction backend (PyMuPDF is usually better).",
    )
    parser.add_argument("--start-page", type=int, default=1, help="1-based start page")
    parser.add_argument("--end-page", type=int, default=None, help="1-based end page (inclusive)")
    parser.add_argument(
        "--no-drop-repeated",
        action="store_true",
        help="Do not attempt to drop repeated headers/footers.",
    )
    parser.add_argument(
        "--page-markers",
        action="store_true",
        help="Insert HTML comment page markers (recommended for reliable chunked translation).",
    )
    parser.add_argument(
        "--min-repeat-ratio",
        type=float,
        default=0.10,
        help="Header/footer repeat ratio to consider 'repetitive' (default 0.10).",
    )
    parser.add_argument(
        "--min-repeat-count",
        type=int,
        default=30,
        help="Minimum header/footer count to consider 'repetitive' (default 30).",
    )

    args = parser.parse_args(argv)

    pdf_path: Path = args.pdf
    out_md: Path = args.out_md
    if not pdf_path.exists():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        return 2

    if args.backend == "pymupdf":
        pages = _extract_pages_pymupdf(pdf_path)
    else:
        pages = _extract_pages_pypdf(pdf_path)

    start = max(1, args.start_page)
    end = args.end_page or len(pages)
    end = min(end, len(pages))
    selected = [p for p in pages if start <= p.page_number <= end]

    repetitive_headers: set[str] = set()
    repetitive_footers: set[str] = set()
    if not args.no_drop_repeated:
        repetitive_headers, repetitive_footers = _compute_repetitive_headers_and_footers(
            selected, min_ratio=args.min_repeat_ratio, min_count=args.min_repeat_count
        )

    out_lines: list[str] = []
    out_lines.append(f"# {pdf_path.stem}")
    out_lines.append("")
    out_lines.append(f"<!-- Source: {pdf_path.name} -->")
    out_lines.append("")

    current_section: str | None = None
    for page in selected:
        cleaned_lines, section_candidate = _clean_page_lines(
            page, repetitive_headers=repetitive_headers, repetitive_footers=repetitive_footers
        )

        if section_candidate and section_candidate != current_section:
            out_lines.append(f"## {section_candidate}")
            out_lines.append("")
            current_section = section_candidate

        if args.page_markers:
            out_lines.append(f"<!-- Page: {page.page_number} -->")

        if not cleaned_lines:
            out_lines.append("")
            continue

        out_lines.extend(cleaned_lines)
        out_lines.append("")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(out_lines).rstrip() + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
