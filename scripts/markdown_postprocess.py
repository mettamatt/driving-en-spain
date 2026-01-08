#!/usr/bin/env python3
from __future__ import annotations

import re
from dataclasses import dataclass


HTML_COMMENT_LINE_RE = re.compile(r"^\s*<!--.*?-->\s*$")
PAGE_MARKER_RE = re.compile(r"^\s*<!--\s*Page:\s*(\d+)\s*-->\s*$")
FENCE_RE = re.compile(r"^\s*```")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
UNORDERED_LIST_RE = re.compile(r"^(\s*[-*+])\s+(.+)$")
ORDERED_LIST_RE = re.compile(r"^(\s*\d+[.)])\s+(.+)$")

DEFINITION_START_RE = re.compile(
    r"^[A-Za-z][A-Za-z0-9'’-]*(?:\s+[A-Za-z][A-Za-z0-9'’-]*){0,3}\.\s+"
)
TABLE_KEY_RE = re.compile(r"^[A-Z0-9][A-Z0-9+./-]{0,7}$")
AGE_YEARS_RE = re.compile(r"^\d{1,3}\s+years?$", re.IGNORECASE)

TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")
SIMPLE_NUMBER_RE = re.compile(r"^\d{1,4}$")


@dataclass(frozen=True)
class LicenceRow:
    code: str
    vehicles: str
    min_age: str


@dataclass(frozen=True)
class TwoColRow:
    key: str
    value: str


def _normalize_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _escape_md_table_cell(text: str) -> str:
    return text.replace("|", "\\|").strip()


def _first_nonspace_char(s: str) -> str | None:
    for ch in s:
        if not ch.isspace():
            return ch
    return None


def _looks_like_definition_start(line: str) -> bool:
    s = line.strip()
    if len(s) > 120:
        return False
    return bool(DEFINITION_START_RE.match(s))


def _looks_like_table_key(line: str) -> bool:
    return bool(TABLE_KEY_RE.match(line.strip()))


def _looks_like_age_years(line: str) -> bool:
    return bool(AGE_YEARS_RE.match(line.strip()))


def _looks_like_label_line(line: str) -> bool:
    """
    Heuristic for term/label lines that should stay on their own line, e.g.:
    - "Pick-up"
    - "Three-wheeled vehicle"
    - "Watch video"
    """
    s = line.strip()
    if not s:
        return False
    # Never treat structural Markdown lines as labels.
    if FENCE_RE.match(s) or HEADING_RE.match(s) or HTML_COMMENT_LINE_RE.match(s):
        return False
    if UNORDERED_LIST_RE.match(s) or ORDERED_LIST_RE.match(s):
        return False
    if TABLE_LINE_RE.match(s):
        return False
    if len(s) > 60:
        return False
    if s.endswith((".", "!", "?", "…", ",", ";", ":", "—", "–")):
        return False
    if _looks_like_definition_start(s):
        return False
    # Treat short all-caps codes as labels (e.g., "A1", "B+E", "ITV").
    if _looks_like_table_key(s):
        return True
    # Too many words -> likely prose.
    words = s.split()
    if len(words) > 7:
        return False

    # Must start with an uppercase letter.
    first = _first_nonspace_char(s)
    if not first or not first.isalpha() or first.islower():
        return False

    # If it looks like a sentence (common starters), don't treat it as a label.
    tokens = re.findall(r"[a-zA-Z][a-zA-Z'’-]*", s.lower())
    if tokens:
        sentence_starters = {
            "a",
            "an",
            "the",
            "this",
            "these",
            "that",
            "those",
            "it",
            "they",
            "there",
            "some",
            "you",
            "we",
            "and",
            "or",
            "but",
            "because",
            "if",
            "when",
            "while",
            "as",
            "unlike",
            "in",
            "on",
            "for",
            "to",
            "of",
        }
        if tokens[0] in sentence_starters:
            return False

    return True


def _should_join_lines(curr: str, nxt: str) -> bool:
    """
    Heuristic: join wrapped lines within a paragraph; keep label-like lines separate.
    """
    if not curr or not nxt:
        return False

    nxt_stripped = nxt.strip()
    if not nxt_stripped:
        return False
    if (
        _looks_like_label_line(nxt_stripped)
        or _looks_like_definition_start(nxt_stripped)
        or TABLE_LINE_RE.match(nxt_stripped)
    ):
        return False
    return True


def _append_wrapped(parts: list[str], fragment: str) -> None:
    fragment = fragment.strip()
    if not fragment:
        return
    if not parts:
        parts.append(fragment)
        return
    prev = parts[-1]
    first = _first_nonspace_char(fragment)
    if prev.endswith("-") and first and first.isalpha() and prev[:-1] and prev[-2].isalpha():
        parts[-1] = prev[:-1] + fragment
        return
    parts.append(fragment)


def _is_markdown_table_start(lines: list[str], index: int) -> bool:
    if index >= len(lines):
        return False
    line = lines[index].rstrip()
    if not TABLE_LINE_RE.match(line):
        return False
    if index + 1 >= len(lines):
        return False
    sep = lines[index + 1].rstrip()
    # a simple separator row: pipes + dashes/colons/spaces
    if not TABLE_LINE_RE.match(sep):
        return False
    if re.search(r"[A-Za-z0-9]", sep):
        return False
    return True


def reflow_markdown_paragraphs(markdown: str) -> str:
    """
    Convert hard-wrapped lines into more natural Markdown paragraphs.
    Preserves headings, lists, HTML comments (including page markers), and Markdown tables.
    """
    lines = markdown.splitlines()
    out: list[str] = []
    paragraph_parts: list[str] = []

    def ensure_blank_line() -> None:
        if out and out[-1] != "":
            out.append("")

    def flush_paragraph() -> None:
        nonlocal paragraph_parts
        if not paragraph_parts:
            return
        out.append(" ".join(paragraph_parts).strip())
        paragraph_parts = []

    def is_html_comment(line: str) -> bool:
        return bool(HTML_COMMENT_LINE_RE.match(line))

    def is_heading(line: str) -> bool:
        return bool(HEADING_RE.match(line))

    def parse_list_start(line: str) -> tuple[str, str] | None:
        m = UNORDERED_LIST_RE.match(line)
        if m:
            return (m.group(1) + " ", m.group(2))
        m = ORDERED_LIST_RE.match(line)
        if m:
            return (m.group(1) + " ", m.group(2))
        return None

    def next_nonblank_starts_lowercase(from_index: int) -> bool:
        j = from_index + 1
        while j < len(lines) and lines[j].strip() == "":
            j += 1
        if j >= len(lines):
            return False
        peek = lines[j].rstrip()
        if (
            is_heading(peek)
            or is_html_comment(peek)
            or parse_list_start(peek)
            or FENCE_RE.match(peek)
            or TABLE_LINE_RE.match(peek)
        ):
            return False
        first = _first_nonspace_char(peek)
        return bool(first and first.isalpha() and first.islower())

    in_code_fence = False
    i = 0
    while i < len(lines):
        raw = lines[i]

        # Preserve fenced code blocks exactly.
        if FENCE_RE.match(raw):
            flush_paragraph()
            ensure_blank_line()
            out.append(raw.rstrip("\n"))
            in_code_fence = not in_code_fence
            i += 1
            continue

        if in_code_fence:
            out.append(raw.rstrip("\n"))
            i += 1
            continue

        # Preserve markdown tables exactly.
        if _is_markdown_table_start(lines, i):
            flush_paragraph()
            ensure_blank_line()
            while i < len(lines):
                row = lines[i].rstrip()
                if row.strip() == "":
                    break
                if not TABLE_LINE_RE.match(row):
                    break
                out.append(row)
                i += 1
            ensure_blank_line()
            # Skip blank lines after the table (we already inserted one).
            while i < len(lines) and lines[i].strip() == "":
                i += 1
            continue

        # For non-structural lines, strip trailing whitespace to avoid Markdown hard breaks.
        line = raw.rstrip()

        if line.strip() == "":
            flush_paragraph()
            ensure_blank_line()
            i += 1
            continue

        if is_heading(line):
            flush_paragraph()
            ensure_blank_line()
            out.append(line)
            ensure_blank_line()
            i += 1
            continue

        if is_html_comment(line):
            flush_paragraph()
            ensure_blank_line()
            out.append(raw.rstrip("\n"))
            ensure_blank_line()
            i += 1
            continue

        list_parsed = parse_list_start(line)
        if list_parsed:
            flush_paragraph()
            ensure_blank_line()
            # Consume a list block.
            while i < len(lines):
                raw_item = lines[i]
                item_line = raw_item.rstrip()
                parsed = parse_list_start(item_line)
                if not parsed:
                    break
                prefix, body = parsed
                item_parts: list[str] = []
                _append_wrapped(item_parts, body)
                i += 1
                while i < len(lines):
                    nxt_raw = lines[i]
                    nxt = nxt_raw.rstrip()
                    if nxt.strip() == "":
                        break
                    if (
                        is_heading(nxt)
                        or is_html_comment(nxt)
                        or parse_list_start(nxt)
                        or TABLE_LINE_RE.match(nxt)
                    ):
                        break
                    if _looks_like_label_line(nxt) or _looks_like_definition_start(nxt):
                        break
                    _append_wrapped(item_parts, nxt)
                    i += 1
                out.append(prefix + " ".join(item_parts).strip())
                if i < len(lines) and lines[i].strip() == "":
                    break
            ensure_blank_line()
            while i < len(lines) and lines[i].strip() == "":
                i += 1
            continue

        if _looks_like_label_line(line):
            if next_nonblank_starts_lowercase(i):
                # Likely a wrapped sentence/phrase, not a standalone label.
                pass
            else:
                flush_paragraph()
                ensure_blank_line()
                while True:
                    out.append(line)
                    i += 1
                    if i >= len(lines):
                        break
                    peek_raw = lines[i]
                    peek = peek_raw.rstrip()
                    if peek.strip() == "":
                        break
                    if (
                        is_heading(peek)
                        or is_html_comment(peek)
                        or parse_list_start(peek)
                        or FENCE_RE.match(peek)
                        or TABLE_LINE_RE.match(peek)
                    ):
                        break
                    if not _looks_like_label_line(peek):
                        break
                    if next_nonblank_starts_lowercase(i):
                        break
                    line = peek
                ensure_blank_line()
                while i < len(lines) and lines[i].strip() == "":
                    i += 1
                continue

        # Regular prose line: decide whether it should be joined with the next line.
        nxt_line = None
        if i + 1 < len(lines):
            nxt_line = lines[i + 1].rstrip()
            if (
                nxt_line.strip() == ""
                or is_heading(nxt_line)
                or is_html_comment(nxt_line)
                or parse_list_start(nxt_line)
                or _looks_like_label_line(nxt_line)
                or _looks_like_definition_start(nxt_line)
                or TABLE_LINE_RE.match(nxt_line)
            ):
                nxt_line = None

        _append_wrapped(paragraph_parts, line)
        if not nxt_line or not _should_join_lines(line, nxt_line):
            flush_paragraph()
            ensure_blank_line()
        i += 1

    flush_paragraph()
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out).rstrip() + "\n"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _next_nonblank(lines: list[str], start: int) -> int | None:
    i = start
    while i < len(lines) and lines[i].strip() == "":
        i += 1
    return i if i < len(lines) else None


def _is_simple_number(line: str) -> bool:
    return bool(SIMPLE_NUMBER_RE.fullmatch(line.strip()))


def _looks_like_licence_header(lines: list[str], index: int) -> tuple[tuple[str, str, str], int] | None:
    """
    Returns ((h1,h2,h3), idx_after_headers) if a licence table header is found at index.
    """
    if index >= len(lines):
        return None
    if _norm(lines[index]) not in {"type of licence", "type of license"}:
        return None
    j = _next_nonblank(lines, index + 1)
    if j is None:
        return None
    if _norm(lines[j]) not in {"vehicles you can drive", "vehicles you may drive"}:
        return None
    k = _next_nonblank(lines, j + 1)
    if k is None:
        return None
    if _norm(lines[k]) not in {"minimum age to drive", "minimum driving age"}:
        return None
    return ((lines[index].strip(), lines[j].strip(), lines[k].strip()), k + 1)


def _parse_licence_rows(lines: list[str], start: int) -> tuple[list[LicenceRow], int, bool]:
    """
    Parse rows of a licence table starting at `start` (expected to be at the first row code).
    Returns (rows, next_index, ended_by_page_marker).
    """
    rows: list[LicenceRow] = []
    i = start
    ended_by_marker = False

    while True:
        i2 = _next_nonblank(lines, i)
        if i2 is None:
            return rows, len(lines), ended_by_marker
        i = i2

        line = lines[i].strip()
        if PAGE_MARKER_RE.match(line):
            ended_by_marker = True
            return rows, i, ended_by_marker
        if HTML_COMMENT_LINE_RE.match(line) or HEADING_RE.match(line):
            return rows, i, ended_by_marker
        if not _looks_like_table_key(line):
            return rows, i, ended_by_marker

        code = line
        i += 1

        vehicle_parts: list[str] = []
        age: str | None = None

        while True:
            j = _next_nonblank(lines, i)
            if j is None:
                break
            i = j
            line2 = lines[i].strip()
            if PAGE_MARKER_RE.match(line2):
                ended_by_marker = True
                break
            if HTML_COMMENT_LINE_RE.match(line2) or HEADING_RE.match(line2):
                break
            if _looks_like_age_years(line2):
                age = line2
                i += 1
                break
            if _looks_like_table_key(line2) and vehicle_parts:
                # Next row started unexpectedly; attempt to split age from the last part.
                break

            vehicle_parts.append(line2)
            i += 1

        if age is None and vehicle_parts:
            m = re.search(r"\b(\d{1,3}\s+years?)\b\.?$", vehicle_parts[-1], re.IGNORECASE)
            if m:
                age = m.group(1)
                vehicle_parts[-1] = vehicle_parts[-1][: m.start()].rstrip(" ,;:-")

        if age is None:
            # Not actually a licence table; abort conversion.
            return [], start, False

        vehicles = re.sub(r"\s+", " ", " ".join(vehicle_parts)).strip()
        rows.append(LicenceRow(code=code, vehicles=vehicles, min_age=age))


def _render_licence_table(headers: tuple[str, str, str], rows: list[LicenceRow]) -> list[str]:
    h1, h2, h3 = (_escape_md_table_cell(h) for h in headers)
    out = [
        f"| {h1} | {h2} | {h3} |",
        "| --- | --- | --- |",
    ]
    for r in rows:
        out.append(
            f"| {_escape_md_table_cell(r.code)} | {_escape_md_table_cell(r.vehicles)} | {_escape_md_table_cell(r.min_age)} |"
        )
    return out


def _looks_like_two_col_header(lines: list[str], index: int) -> tuple[tuple[str, str], int] | None:
    if index >= len(lines):
        return None
    if _norm(lines[index]) != "category":
        return None
    j = _next_nonblank(lines, index + 1)
    if j is None:
        return None
    if _norm(lines[j]) != "use":
        return None
    return ((lines[index].strip(), lines[j].strip()), j + 1)


def _parse_two_col_rows(lines: list[str], start: int) -> tuple[list[TwoColRow], int]:
    rows: list[TwoColRow] = []
    i = start

    def is_key(s: str) -> bool:
        return bool(re.fullmatch(r"[A-Z]", s.strip()))

    while True:
        i2 = _next_nonblank(lines, i)
        if i2 is None:
            return rows, len(lines)
        i = i2
        line = lines[i].strip()
        if PAGE_MARKER_RE.match(line) or HTML_COMMENT_LINE_RE.match(line) or HEADING_RE.match(line):
            return rows, i
        if not is_key(line):
            return rows, i
        key = line
        i += 1
        value_parts: list[str] = []
        while True:
            j = _next_nonblank(lines, i)
            if j is None:
                break
            i = j
            s = lines[i].strip()
            if PAGE_MARKER_RE.match(s) or HTML_COMMENT_LINE_RE.match(s) or HEADING_RE.match(s):
                break
            if is_key(s) and value_parts:
                break
            value_parts.append(s)
            i += 1

        value = re.sub(r"\s+", " ", " ".join(value_parts)).strip()
        if not value:
            return [], start
        rows.append(TwoColRow(key=key, value=value))


def _render_two_col_table(headers: tuple[str, str], rows: list[TwoColRow]) -> list[str]:
    h1, h2 = (_escape_md_table_cell(h) for h in headers)
    out = [
        f"| {h1} | {h2} |",
        "| --- | --- |",
    ]
    for r in rows:
        out.append(f"| {_escape_md_table_cell(r.key)} | {_escape_md_table_cell(r.value)} |")
    return out


def _find_block_end(lines: list[str], start: int, *, stop_norms: set[str]) -> int:
    """
    Return the index of the first line that should terminate a conversion block.
    """
    i = start
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        if PAGE_MARKER_RE.match(s) or HTML_COMMENT_LINE_RE.match(s) or HEADING_RE.match(s):
            return i
        if _norm(s) in stop_norms:
            return i
        i += 1
    return len(lines)


def _looks_like_outside_speed_table_header(
    lines: list[str], index: int
) -> tuple[tuple[str, str, str], int] | None:
    if index >= len(lines):
        return None
    if _norm(lines[index]) != "motorway and dual carriageway":
        return None
    j = _next_nonblank(lines, index + 1)
    if j is None or _norm(lines[j]) != "roads":
        return None
    k = _next_nonblank(lines, j + 1)
    if k is None or _norm(lines[k]) != "tracks":
        return None
    return ((lines[index].strip(), lines[j].strip(), lines[k].strip()), k + 1)


def _render_speed_max_min_table(
    road_labels: tuple[str, str, str], values: list[str]
) -> list[str]:
    mw, roads, tracks = (_escape_md_table_cell(s) for s in road_labels)
    mw_max, mw_min, r_max, r_min, t_max, t_min = (v.strip() for v in values[:6])
    out = [
        "| Road type | Max speed (km/h) | Min speed (km/h) |",
        "| --- | --- | --- |",
        f"| {mw} | {_escape_md_table_cell(mw_max)} | {_escape_md_table_cell(mw_min)} |",
        f"| {roads} | {_escape_md_table_cell(r_max)} | {_escape_md_table_cell(r_min)} |",
        f"| {tracks} | {_escape_md_table_cell(t_max)} | {_escape_md_table_cell(t_min)} |",
    ]
    return out


def _render_speed_single_table(
    road_labels: tuple[str, str, str], values: list[str]
) -> list[str]:
    mw, roads, tracks = (_escape_md_table_cell(s) for s in road_labels)
    mw_v, r_v, t_v = (v.strip() for v in values[:3])
    out = [
        "| Road type | Speed limit |",
        "| --- | --- |",
        f"| {mw} | {_escape_md_table_cell(mw_v)} |",
        f"| {roads} | {_escape_md_table_cell(r_v)} |",
        f"| {tracks} | {_escape_md_table_cell(t_v)} |",
    ]
    return out


def _parse_outside_speed_table(
    lines: list[str], start: int, end: int
) -> tuple[list[str], list[str]] | None:
    """
    Parse the "Motorway and dual carriageway / Roads / Tracks" speed blocks extracted from PDF tables.
    Returns (rendered_table_lines, extra_lines) or None if it doesn't match.
    """
    header_norms = {
        "maximum speed",
        "minimum speed",
        "maximum and minimum speed",
    }

    has_min = any(_norm(lines[i]) == "minimum speed" for i in range(start, end))

    extras: list[str] = []
    if has_min:
        nums: list[str] = []
        for i in range(start, end):
            s = lines[i].strip()
            if not s:
                continue
            if _norm(s) in header_norms:
                continue
            if _is_simple_number(s):
                nums.append(s)
                continue
            extras.append(s)
        if len(nums) < 6:
            return None
        # Any non-numeric lines get emitted after the table (e.g., "On some roads the limit may be 100.")."
        return (nums[:6], extras)

    # Max-only / mixed blocks: pick first three non-header values, allowing text for the first cell.
    values: list[str] = []
    for i in range(start, end):
        s = lines[i].strip()
        if not s:
            continue
        if _norm(s) in header_norms:
            continue
        if _is_simple_number(s):
            values.append(s)
            continue
        if len(values) < 1:
            values.append(s)
            continue
        # Remaining non-numeric lines are likely notes/figure captions.
        extras.append(s)

    if len(values) < 3:
        return None
    return (values[:3], extras)


def _looks_like_in_town_speed_table_header(
    lines: list[str], index: int
) -> tuple[tuple[str, str, str], int] | None:
    if index >= len(lines):
        return None
    if not _norm(lines[index]).startswith("streets without kerbs"):
        return None
    j = _next_nonblank(lines, index + 1)
    if j is None or _norm(lines[j]) != "streets with one lane in each direction":
        return None
    k = _next_nonblank(lines, j + 1)
    if k is None or _norm(lines[k]) != "streets with several lanes in each direction":
        return None
    return ((lines[index].strip(), lines[j].strip(), lines[k].strip()), k + 1)


def _parse_in_town_speed_table(lines: list[str], start: int, end: int) -> list[str] | None:
    speeds: list[str] = []
    for i in range(start, end):
        s = lines[i].strip()
        if not s:
            continue
        if _is_simple_number(s):
            speeds.append(s)
            if len(speeds) >= 3:
                break
    if len(speeds) < 3:
        return None
    return speeds[:3]


def _render_in_town_speed_table(street_labels: tuple[str, str, str], speeds: list[str]) -> list[str]:
    s1, s2, s3 = (_escape_md_table_cell(s) for s in street_labels)
    v1, v2, v3 = (v.strip() for v in speeds[:3])
    out = [
        "| Street type | Max speed (km/h) |",
        "| --- | --- |",
        f"| {s1} | {_escape_md_table_cell(v1)} |",
        f"| {s2} | {_escape_md_table_cell(v2)} |",
        f"| {s3} | {_escape_md_table_cell(v3)} |",
    ]
    return out


def _looks_like_points_table_header(lines: list[str], index: int) -> tuple[tuple[str, str], int] | None:
    if index >= len(lines):
        return None
    if _norm(lines[index]) != "type of driver":
        return None
    j = _next_nonblank(lines, index + 1)
    if j is None or _norm(lines[j]) != "points":
        return None
    return ((lines[index].strip(), lines[j].strip()), j + 1)


def _parse_points_rows(lines: list[str], start: int, end: int) -> tuple[list[tuple[str, str]], int]:
    rows: list[tuple[str, str]] = []
    i = start
    while True:
        i2 = _next_nonblank(lines, i)
        if i2 is None or i2 >= end:
            return rows, end
        i = i2

        # End conditions.
        s = lines[i].strip()
        if PAGE_MARKER_RE.match(s) or HTML_COMMENT_LINE_RE.match(s) or HEADING_RE.match(s):
            return rows, i

        # Parse label (may span multiple lines) until we hit a numeric value.
        label_parts: list[str] = []
        while i < end:
            s = lines[i].strip()
            if not s:
                i += 1
                continue
            if _is_simple_number(s):
                break
            if PAGE_MARKER_RE.match(s) or HTML_COMMENT_LINE_RE.match(s) or HEADING_RE.match(s):
                return rows, i
            label_parts.append(s)
            i += 1

        i3 = _next_nonblank(lines, i)
        if i3 is None or i3 >= end:
            return rows, end
        i = i3
        val = lines[i].strip()
        if not _is_simple_number(val) or not label_parts:
            return rows, i
        label = re.sub(r"\s+", " ", " ".join(label_parts)).strip()
        rows.append((label, val))
        i += 1


def _render_points_table(headers: tuple[str, str], rows: list[tuple[str, str]]) -> list[str]:
    h1, h2 = (_escape_md_table_cell(h) for h in headers)
    out = [
        f"| {h1} | {h2} |",
        "| --- | --- |",
    ]
    for label, val in rows:
        out.append(f"| {_escape_md_table_cell(label)} | {_escape_md_table_cell(val)} |")
    return out


def convert_known_tables(markdown: str) -> str:
    """
    Convert a couple of known "PDF extracted" table patterns into Markdown tables.
    Keeps page markers and other HTML comments.
    """
    lines = [ln.rstrip() for ln in markdown.splitlines()]
    out: list[str] = []

    in_code_fence = False
    carry_licence_headers: tuple[str, str, str] | None = None
    after_page_marker = False

    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()

        if FENCE_RE.match(line):
            in_code_fence = not in_code_fence
            out.append(line)
            i += 1
            continue

        if in_code_fence:
            out.append(line)
            i += 1
            continue

        if PAGE_MARKER_RE.match(line):
            out.append(line)
            after_page_marker = True
            i += 1
            continue

        # Licence table continuation (often spans pages).
        if after_page_marker and carry_licence_headers and _looks_like_table_key(line.strip()):
            rows, next_i, ended_by_marker = _parse_licence_rows(lines, i)
            if rows:
                out.append("")
                out.extend(_render_licence_table(carry_licence_headers, rows))
                out.append("")
                i = next_i
                after_page_marker = False
                carry_licence_headers = carry_licence_headers if ended_by_marker else None
                continue
            carry_licence_headers = None
            after_page_marker = False

        after_page_marker = False

        maybe_licence_header = _looks_like_licence_header(lines, i)
        if maybe_licence_header:
            headers, start_rows = maybe_licence_header
            rows, next_i, ended_by_marker = _parse_licence_rows(lines, start_rows)
            if rows:
                out.append("")
                out.extend(_render_licence_table(headers, rows))
                out.append("")
                i = next_i
                carry_licence_headers = headers if ended_by_marker else None
                continue

        maybe_two_col_header = _looks_like_two_col_header(lines, i)
        if maybe_two_col_header:
            headers2, start_rows2 = maybe_two_col_header
            rows2, next_i2 = _parse_two_col_rows(lines, start_rows2)
            if len(rows2) >= 2:
                out.append("")
                out.extend(_render_two_col_table(headers2, rows2))
                out.append("")
                i = next_i2
                continue

        maybe_points_header = _looks_like_points_table_header(lines, i)
        if maybe_points_header:
            headers_p, start_rows_p = maybe_points_header
            end_p = _find_block_end(lines, start_rows_p, stop_norms=set())
            rows_p, next_i_p = _parse_points_rows(lines, start_rows_p, end_p)
            if len(rows_p) >= 2:
                out.append("")
                out.extend(_render_points_table(headers_p, rows_p))
                out.append("")
                i = next_i_p
                continue

        maybe_speed_header = _looks_like_outside_speed_table_header(lines, i)
        if maybe_speed_header:
            road_labels, start_values = maybe_speed_header
            end_values = _find_block_end(
                lines,
                start_values,
                stop_norms={"type of vehicle", "types of vehicle"},
            )
            parsed = _parse_outside_speed_table(lines, start_values, end_values)
            if parsed:
                values, extras = parsed
                out.append("")
                if len(values) >= 6:
                    out.extend(_render_speed_max_min_table(road_labels, values))
                else:
                    out.extend(_render_speed_single_table(road_labels, values))
                out.append("")
                if extras:
                    out.extend(extras)
                    out.append("")
                i = end_values
                continue

        maybe_town_speed_header = _looks_like_in_town_speed_table_header(lines, i)
        if maybe_town_speed_header:
            street_labels, start_vals = maybe_town_speed_header
            end_vals = _find_block_end(
                lines,
                start_vals,
                stop_norms={"type of vehicle", "types of vehicle"},
            )
            speeds = _parse_in_town_speed_table(lines, start_vals, end_vals)
            if speeds:
                out.append("")
                out.extend(_render_in_town_speed_table(street_labels, speeds))
                out.append("")
                i = end_vals
                continue

        out.append(line)
        i += 1

    while out and out[-1] == "":
        out.pop()
    return "\n".join(out).rstrip() + "\n"


def postprocess_markdown(
    markdown: str,
    *,
    style: str = "paragraphs",
    enable_tables: bool = True,
) -> str:
    """
    - enable_tables: convert some known extracted tables into Markdown tables.
    - style:
      - paragraphs: merge wrapped lines into paragraphs for reading
      - preserve: keep original line breaks (but still normalises whitespace)
    """
    text = _normalize_line_endings(markdown)
    if enable_tables:
        text = convert_known_tables(text)

    # Always strip trailing spaces to avoid unintended Markdown hard breaks.
    text = "\n".join(line.rstrip() for line in text.splitlines()).rstrip() + "\n"

    if style == "paragraphs":
        return reflow_markdown_paragraphs(text)
    return text
