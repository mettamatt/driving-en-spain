#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import statistics
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PageText:
    page_number: int  # 1-based
    lines: list[str]


@dataclass(frozen=True)
class _Fragment:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class _Row:
    y: float
    cells: tuple[str, ...]


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


def _median(values: list[float], default: float) -> float:
    values = [v for v in values if v > 0]
    if not values:
        return default
    try:
        return statistics.median(values)
    except statistics.StatisticsError:
        return default


def _escape_md_table_cell(text: str) -> str:
    return text.replace("|", "\\|").strip()


def _extract_page_layout_rows(page: "fitz.Page") -> list[str]:
    """
    Layout-aware extraction for pages that are heavy on tables / multi-column content.

    Strategy (intentionally simple):
    - Extract word boxes via PyMuPDF.
    - Split each PyMuPDF line into column fragments based on large horizontal gaps.
    - Cluster fragments by y to form "rows".
    - Detect stable column starts and emit Markdown tables for multi-column runs.

    This does not attempt perfect table row-span reconstruction; it aims to preserve
    column separation so later reflow/translation doesn't interleave columns.
    """
    page_width = float(page.rect.width)
    page_height = float(page.rect.height)

    # Words tuple: (x0, y0, x1, y1, text, block_no, line_no, word_no)
    words = page.get_text("words") or []

    # Group words by PyMuPDF's (block, line) IDs, but keep per-word geometry.
    by_line: dict[tuple[int, int], list[tuple[float, float, float, float, str]]] = defaultdict(list)
    for x0, y0, x1, y1, text, block_no, line_no, _word_no in words:
        t = (text or "").strip()
        if not t:
            continue
        by_line[(int(block_no), int(line_no))].append((float(x0), float(y0), float(x1), float(y1), t))

    fragments: list[_Fragment] = []
    gap_threshold = 6.5  # points; tuned to split columns but not normal word spacing.

    for (_block_no, _line_no), line_words in by_line.items():
        line_words.sort(key=lambda w: w[0])  # x0

        current: list[tuple[float, float, float, float, str]] = []

        def flush() -> None:
            nonlocal current
            if not current:
                return
            text = " ".join(w[4] for w in current).strip()
            if not text:
                current = []
                return
            x0 = min(w[0] for w in current)
            y0 = min(w[1] for w in current)
            x1 = max(w[2] for w in current)
            y1 = max(w[3] for w in current)
            # Drop standalone page-number fragments that live in the bottom margin.
            if _is_standalone_page_number(text) and y0 >= page_height * 0.85:
                current = []
                return
            fragments.append(_Fragment(text=text, x0=x0, y0=y0, x1=x1, y1=y1))
            current = []

        prev_x1: float | None = None
        for x0, y0, x1, y1, text in line_words:
            if prev_x1 is not None and x0 - prev_x1 > gap_threshold and current:
                flush()
                prev_x1 = None
            current.append((x0, y0, x1, y1, text))
            prev_x1 = x1
        flush()

    if not fragments:
        return []

    # Detect column starts by clustering fragment left edges.
    bin_size = 10
    min_sep = 60.0
    weight_by_bin: Counter[int] = Counter()
    total_weight = 0
    for f in fragments:
        # Ignore near-right-edge fragments (page numbers / marginalia).
        if f.x0 >= page_width * 0.85:
            continue
        b = int(round(f.x0 / bin_size) * bin_size)
        w = max(1, len(f.text))
        weight_by_bin[b] += w
        total_weight += w

    col_bins = [b for b, _w in weight_by_bin.most_common()]
    col_starts: list[float] = []
    for b in col_bins:
        # Allow smaller columns (e.g. a narrow middle column of short labels).
        # Too-high a cutoff can collapse 3-column tables into 2 columns.
        if total_weight and weight_by_bin[b] / total_weight < 0.07:
            continue
        if all(abs(b - s) >= min_sep for s in col_starts):
            col_starts.append(float(b))
        if len(col_starts) >= 4:
            break

    col_starts.sort()
    if len(col_starts) < 2:
        # Not enough stable columns; fall back to plain text extraction.
        return _lines_from_text(page.get_text("text") or "")

    # Midpoints between columns define assignment regions.
    boundaries = [(a + b) / 2.0 for a, b in zip(col_starts, col_starts[1:])]

    def col_index(x0: float) -> int:
        return int(bisect_right(boundaries, x0))

    # Cluster fragments into y-rows using a tolerance, not a fixed bin. This avoids
    # misalignments where left/right column text is on the same visual row but
    # differs by <1pt in y.
    fragments.sort(key=lambda f: (f.y0, f.x0))

    y_tol = 1.8
    row_fragments: list[list[_Fragment]] = []
    row_y0: list[float] = []
    for f in fragments:
        if not row_fragments:
            row_fragments.append([f])
            row_y0.append(f.y0)
            continue
        if abs(f.y0 - row_y0[-1]) <= y_tol:
            row_fragments[-1].append(f)
            # Keep the row anchor stable: average within a small band.
            row_y0[-1] = (row_y0[-1] + f.y0) / 2.0
        else:
            row_fragments.append([f])
            row_y0.append(f.y0)

    gaps = [float(b - a) for a, b in zip(row_y0, row_y0[1:]) if b - a > 0]
    # Ignore tiny gaps which are often baseline jitter across columns, not real
    # row spacing. These tiny gaps can skew the "typical" gap down and cause
    # heuristics (like header merging) to misfire.
    gaps_sorted = sorted(g for g in gaps if g >= 4.0)
    typical_gap = _median(gaps_sorted[: max(1, len(gaps_sorted) // 2)], default=15.0)

    rows: list[_Row] = []
    for y, frags in zip(row_y0, row_fragments):
        frags.sort(key=lambda f: f.x0)
        cells: list[list[str]] = [[] for _ in range(len(col_starts))]
        for f in frags:
            idx = col_index(f.x0)
            if 0 <= idx < len(cells):
                cells[idx].append(f.text)
        row_cells = tuple(" ".join(parts).strip() for parts in cells)
        rows.append(_Row(y=float(y), cells=row_cells))

    def row_nonempty_cols(r: _Row) -> list[int]:
        return [i for i, c in enumerate(r.cells) if c.strip()]

    def row_is_multi(r: _Row) -> bool:
        return len(row_nonempty_cols(r)) >= 2

    def row_has_nonleft(r: _Row) -> bool:
        return any(c.strip() for c in r.cells[1:])

    def render_table(segment_rows: list[_Row]) -> list[str]:
        if not segment_rows:
            return []

        col_count = len(segment_rows[0].cells)

        def header_like(text: str) -> bool:
            t = text.strip()
            if not t or len(t) > 80:
                return False
            if t.endswith(".") or t.endswith(":"):
                return False
            return True

        # If the segment starts with single-column header labels (e.g., a right-column header
        # stacked above a left-column header), try to build a header row from the prefix rows
        # before the first multi-column content row.
        first_multi_idx = next((idx for idx, r in enumerate(segment_rows) if row_is_multi(r)), 0)
        if first_multi_idx > 0:
            header_parts: list[list[str]] = [[] for _ in range(col_count)]
            for r in segment_rows[: min(first_multi_idx, 3)]:
                for i, c in enumerate(r.cells):
                    c = c.strip()
                    if c and header_like(c):
                        header_parts[i].append(c)
            merged = [(" ".join(parts)).strip() for parts in header_parts]
            if sum(1 for c in merged if c) >= 2:
                header_cells = merged
                body = segment_rows[first_multi_idx:]
            else:
                header_cells = [f"Column {i + 1}" for i in range(col_count)]
                body = segment_rows
        else:
            header_cells = list(segment_rows[0].cells)
            if not (
                row_is_multi(segment_rows[0])
                and all(header_like(c) for c in header_cells if c.strip())
            ):
                header_cells = [f"Column {i + 1}" for i in range(col_count)]
                body = segment_rows
            else:
                body = segment_rows[1:]
                # Merge up to 2 additional header rows if they are close and header-like.
                merged = header_cells[:]
                used = 1
                header_row_max_gap = typical_gap * 1.1
                for r in segment_rows[1:3]:
                    # Use a strict threshold here: body rows are frequently "label-like"
                    # (short, no trailing punctuation), so merging must only happen for
                    # truly adjacent header continuation lines.
                    if r.y - segment_rows[used - 1].y > header_row_max_gap:
                        break
                    if any(c.strip() and not header_like(c) for c in r.cells):
                        break
                    for i, c in enumerate(r.cells):
                        c = c.strip()
                        if c:
                            merged[i] = (merged[i].strip() + " " + c).strip()
                    used += 1
                header_cells = merged
                body = segment_rows[used:]

        lines: list[str] = []
        lines.append(
            "| " + " | ".join(_escape_md_table_cell(c) for c in header_cells) + " |"
        )
        lines.append("| " + " | ".join(["---"] * col_count) + " |")
        for r in body:
            lines.append(
                "| " + " | ".join(_escape_md_table_cell(c) for c in r.cells) + " |"
            )
        return lines

    out: list[str] = []
    prev_y: float | None = None
    i = 0
    while i < len(rows):
        r = rows[i]
        if prev_y is not None and r.y - prev_y > typical_gap * 2.6:
            if out and out[-1] != "":
                out.append("")

        nonempty_texts = [c.strip() for c in r.cells if c.strip()]
        if len(nonempty_texts) == 1 and _looks_like_section_heading(nonempty_texts[0]):
            s = _normalize_line(nonempty_texts[0])
            if s:
                out.append(s)
            prev_y = r.y
            i += 1
            continue

        if r.y < 40.0 or not row_has_nonleft(r):
            # Plain line(s).
            for cell in r.cells:
                s = _normalize_line(cell)
                if not s:
                    continue
                out.append(_as_md_bullet(s))
            prev_y = r.y
            i += 1
            continue

        # Start a table segment.
        segment: list[_Row] = []
        left_only_run = 0
        left_only_armed = False
        while i < len(rows):
            r2 = rows[i]
            gap = 0.0 if prev_y is None else (r2.y - prev_y)
            nonempty = row_nonempty_cols(r2)
            if nonempty == [0]:
                if left_only_run == 0:
                    left_only_armed = gap > typical_gap * 1.8
                    left_only_run = 1 if left_only_armed else 0
                elif left_only_armed:
                    left_only_run += 1
            else:
                left_only_run = 0
                left_only_armed = False

            segment.append(r2)
            prev_y = r2.y
            i += 1

            if left_only_armed and left_only_run >= 3:
                # Treat these rows as non-table content (likely a break between tables/columns).
                break_rows = segment[-left_only_run:]
                segment = segment[:-left_only_run]

                if segment:
                    out.append("")
                    out.extend(render_table(segment))
                    out.append("")

                for br in break_rows:
                    for cell in br.cells:
                        s = _normalize_line(cell)
                        if not s:
                            continue
                        out.append(_as_md_bullet(s))
                out.append("")
                # Avoid rendering the same segment again after the inner loop exits.
                segment = []
                break

        else:
            break_rows = []

        if segment:
            out.append("")
            out.extend(render_table(segment))
            out.append("")

    # Clean up excessive blank lines; keep the structure reasonably stable.
    cleaned: list[str] = []
    blank_run = 0
    for line in out:
        if line.strip() == "":
            blank_run += 1
            if blank_run <= 2:
                cleaned.append("")
            continue
        blank_run = 0
        cleaned.append(line.rstrip())

    while cleaned and cleaned[0] == "":
        cleaned.pop(0)
    while cleaned and cleaned[-1] == "":
        cleaned.pop()

    return cleaned


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


def _extract_pages_pymupdf_layout(pdf_path: Path) -> list[PageText]:
    try:
        import fitz  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "PyMuPDF is not installed. Install it with: python -m pip install pymupdf"
        ) from exc

    doc = fitz.open(str(pdf_path))
    pages: list[PageText] = []

    # Heuristic thresholds (tuned for this PDF): classify a page as \"layout-heavy\" if it has
    # a meaningful second column and a noticeable amount of row pairing.
    BIN = 10
    SEP_MIN = 140
    ROW_BIN = 8

    for i in range(doc.page_count):
        page = doc.load_page(i)

        d = page.get_text("dict") or {}
        lines_for_metrics: list[tuple[float, float, float, float, int]] = []
        for block in d.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                text = "".join(span.get("text", "") for span in spans).strip()
                if not text:
                    continue
                bbox = line.get("bbox")
                if not bbox:
                    continue
                x0, y0, x1, y1 = (float(v) for v in bbox)
                if _is_standalone_page_number(text):
                    continue
                lines_for_metrics.append((x0, y0, x1, y1, len(text)))

        total_chars = sum(w for *_rest, w in lines_for_metrics)
        layout_heavy = False
        if total_chars >= 200 and len(lines_for_metrics) >= 8:
            bins: Counter[int] = Counter()
            for x0, _y0, _x1, _y1, w in lines_for_metrics:
                b = int(round(x0 / BIN) * BIN)
                bins[b] += w

            sorted_bins = bins.most_common()
            if sorted_bins:
                c1 = sorted_bins[0][0]
                c2 = None
                for b, _bw in sorted_bins[1:]:
                    if abs(b - c1) >= SEP_MIN:
                        c2 = b
                        break

                if c2 is not None:
                    w1 = 0
                    w2 = 0
                    rows = defaultdict(lambda: {"w1": 0, "w2": 0})
                    for x0, y0, _x1, y1, w in lines_for_metrics:
                        y_mid = (y0 + y1) / 2.0
                        row_key = int(round(y_mid / ROW_BIN) * ROW_BIN)
                        if abs(x0 - c1) <= abs(x0 - c2):
                            w1 += w
                            rows[row_key]["w1"] += w
                        else:
                            w2 += w
                            rows[row_key]["w2"] += w
                    total_w = w1 + w2
                    min_ratio = min(w1, w2) / total_w if total_w else 0.0
                    row_count = len(rows)
                    paired_rows = sum(
                        1 for r in rows.values() if r["w1"] > 0 and r["w2"] > 0
                    )
                    paired_ratio = paired_rows / row_count if row_count else 0.0
                    layout_heavy = min_ratio >= 0.20 and paired_ratio >= 0.20

        if layout_heavy:
            page_lines = _extract_page_layout_rows(page)
        else:
            page_lines = _lines_from_text(page.get_text("text") or "")

        pages.append(PageText(page_number=i + 1, lines=page_lines))

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
    parser.add_argument(
        "--layout-aware-tables",
        action="store_true",
        help=(
            "Experimental: use PyMuPDF word positions to preserve multi-column tables as Markdown tables. "
            "Only applies with --backend pymupdf."
        ),
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
        if args.layout_aware_tables:
            pages = _extract_pages_pymupdf_layout(pdf_path)
        else:
            pages = _extract_pages_pymupdf(pdf_path)
    else:
        if args.layout_aware_tables:
            print(
                "WARNING: --layout-aware-tables is only supported with --backend pymupdf; ignoring.",
                file=sys.stderr,
            )
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
