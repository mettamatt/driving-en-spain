#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from openai import APIConnectionError, APIError, OpenAI, RateLimitError


PAGE_MARKER_RE = re.compile(r"<!--\s*Page:\s*(\d+)\s*-->")
HTML_COMMENT_LINE_RE = re.compile(r"^\s*<!--.*?-->\s*$")
FENCE_RE = re.compile(r"^\s*```")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
UNORDERED_LIST_RE = re.compile(r"^(\s*[-*+])\s+(.+)$")
ORDERED_LIST_RE = re.compile(r"^(\s*\d+[.)])\s+(.+)$")
DEFINITION_START_RE = re.compile(r"^[A-Za-z][A-Za-z0-9'’-]*(?:\s+[A-Za-z][A-Za-z0-9'’-]*){0,3}\.\s+")


@dataclass(frozen=True)
class Chunk:
    index: int  # 1-based
    text: str
    expected_page_markers: list[str]


def _maybe_unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and ((value[0] == value[-1] == '"') or (value[0] == value[-1] == "'")):
        return value[1:-1]
    return value


def _load_dotenv_file(dotenv_path: Path) -> None:
    """
    Minimal .env loader:
    - Supports KEY=VALUE and `export KEY=VALUE`
    - Ignores comments/blank lines
    - Does not override variables already present in os.environ
    """
    try:
        raw = dotenv_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = _maybe_unquote(value)
        if not key or key in os.environ:
            continue
        os.environ[key] = value


def _load_dotenv_if_present() -> None:
    # Prefer repo root (cwd), fallback to repo root inferred from scripts/ dir.
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ]
    for p in candidates:
        if p.exists() and p.is_file():
            _load_dotenv_file(p)
            return


def _load_glossary(path: Path | None) -> dict[str, str]:
    if not path:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"ERROR: Failed to parse glossary JSON: {path}\n{exc}") from exc
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise SystemExit("ERROR: Glossary must be a JSON object mapping strings to strings.")
    return data


def _split_into_page_segments(markdown: str) -> list[str]:
    matches = list(PAGE_MARKER_RE.finditer(markdown))
    if not matches:
        return [markdown.strip() + "\n"]

    segments: list[str] = []
    # Preface (before first page marker), if any
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


def _build_chunks(segments: list[str], max_chars: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if not current:
            return
        text = "\n".join(s.rstrip() for s in current).rstrip() + "\n"
        expected = [f"<!-- Page: {n} -->" for n in PAGE_MARKER_RE.findall(text)]
        chunks.append(Chunk(index=len(chunks) + 1, text=text, expected_page_markers=expected))
        current = []
        current_len = 0

    for seg in segments:
        seg_len = len(seg)
        if current and current_len + seg_len > max_chars:
            flush()
        if seg_len > max_chars and current:
            # Segment is huge; still place it in its own chunk.
            flush()
        current.append(seg)
        current_len += seg_len

    flush()
    return chunks


def _make_system_prompt(glossary: dict[str, str], english_variant: str) -> str:
    english_variant = (english_variant or "").strip().lower()
    if english_variant not in {"uk", "us", "international"}:
        english_variant = "uk"

    lines = [
        "You are a precise translation engine.",
        "Task: translate the user's Spanish Markdown into English Markdown.",
        "",
        "Rules (must follow):",
        "- Output ONLY the translated Markdown. No preface, no explanations, no code fences.",
        "- Do NOT summarize, omit, or add any content.",
        "- Preserve all Markdown structure (headings, lists, tables) and punctuation.",
        "- Preserve ALL HTML comments exactly as-is (e.g., <!-- Page: 12 -->). Do not translate or delete them.",
        "- Keep acronyms/proper nouns (e.g., DGT) as-is unless an explicit glossary entry says otherwise.",
        "- Use plain, easy-to-read English (short sentences; simple words), matching 'Lectura Fácil' style.",
        "- Do NOT add Markdown hard line breaks (no trailing two-spaces at end of lines).",
        "- Reflow prose into natural paragraphs (merge line-wrapped sentences), while keeping headings and list items as headings/list items.",
    ]

    if english_variant == "uk":
        lines.append(
            "- Use British English (UK) spelling and UK/European road terminology (e.g., motorway, roundabout, overtake, give way)."
        )
    elif english_variant == "us":
        lines.append(
            "- Use American English (US) spelling and road terminology (e.g., highway, traffic circle, pass, yield)."
        )
    else:
        lines.append(
            "- Use international English spelling and widely understood road terminology (avoid region-specific slang)."
        )

    lines.append("")

    if glossary:
        lines.append("Glossary (apply consistently):")
        for src, dst in sorted(glossary.items(), key=lambda kv: kv[0].lower()):
            lines.append(f"- {src} => {dst}")
        lines.append("")

    return "\n".join(lines).rstrip()


def _first_nonspace_char(s: str) -> str | None:
    for ch in s:
        if not ch.isspace():
            return ch
    return None


def _looks_like_definition_start(line: str) -> bool:
    """
    e.g. "Engine size. A measure used to..." or "Axle. A bar that..."
    """
    s = line.strip()
    if len(s) > 120:
        return False
    return bool(DEFINITION_START_RE.match(s))


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
    if len(s) > 60:
        return False
    if s.endswith((".", "!", "?", "…", ",", ";", ":", "—", "–")):
        return False
    if _looks_like_definition_start(s):
        return False
    # Too many words -> likely prose.
    words = s.split()
    if len(words) > 7:
        return False
    # Must start with an uppercase letter or a digit (avoid mid-paragraph fragments).
    first = _first_nonspace_char(s)
    if not first:
        return False
    if not first.isalpha():
        return False
    if first.islower():
        return False

    # If it looks like a sentence (common starters / auxiliary verbs), don't treat it as a label.
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
        auxiliary_verbs = {
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "has",
            "have",
            "had",
            "can",
            "cannot",
            "can't",
            "must",
            "may",
            "might",
            "should",
            "shall",
            "will",
            "would",
            "do",
            "does",
            "did",
            "need",
            "needs",
            "needed",
            "use",
            "uses",
            "using",
            "used",
        }
        if any(t in auxiliary_verbs for t in tokens):
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
    if _looks_like_label_line(nxt_stripped) or _looks_like_definition_start(nxt_stripped):
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


def _reflow_markdown_paragraphs(markdown: str) -> str:
    """
    Convert hard-wrapped lines into more natural Markdown paragraphs.
    Preserves headings, lists, and HTML comments (including page markers).
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
            # Preserve the comment line exactly as-is (aside from trailing newline removal).
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
                    if is_heading(nxt) or is_html_comment(nxt) or parse_list_start(nxt):
                        break
                    if _looks_like_label_line(nxt) or _looks_like_definition_start(nxt):
                        # Likely the list ended and a new term/paragraph starts.
                        break
                    _append_wrapped(item_parts, nxt)
                    i += 1
                out.append(prefix + " ".join(item_parts).strip())
                # Allow a blank line to end the list block.
                if i < len(lines) and lines[i].strip() == "":
                    break
            ensure_blank_line()
            # Skip any blank lines after list block (we already inserted one).
            while i < len(lines) and lines[i].strip() == "":
                i += 1
            continue

        if _looks_like_label_line(line):
            if next_nonblank_starts_lowercase(i):
                # This is likely a wrapped sentence/phrase (e.g. "Axles connect..." + "and ..."),
                # not a standalone label.
                pass
            else:
                flush_paragraph()
                ensure_blank_line()
                # Emit a compact label block: consecutive label lines with no blank lines between.
                while True:
                    out.append(line)
                    i += 1
                    if i >= len(lines):
                        break
                    peek_raw = lines[i]
                    peek = peek_raw.rstrip()
                    if peek.strip() == "":
                        break
                    if is_heading(peek) or is_html_comment(peek) or parse_list_start(peek) or FENCE_RE.match(peek):
                        break
                    if not _looks_like_label_line(peek):
                        break
                    if next_nonblank_starts_lowercase(i):
                        break
                    line = peek
                ensure_blank_line()
                # Skip blank lines after label block (we already inserted one).
                while i < len(lines) and lines[i].strip() == "":
                    i += 1
                continue

        # Regular prose line: decide whether it should be joined with the next line.
        nxt_line = None
        if i + 1 < len(lines):
            nxt_raw = lines[i + 1]
            nxt_line = nxt_raw.rstrip()
            # Don't join across structural boundaries.
            if (
                nxt_line.strip() == ""
                or is_heading(nxt_line)
                or is_html_comment(nxt_line)
                or parse_list_start(nxt_line)
                or _looks_like_label_line(nxt_line)
                or _looks_like_definition_start(nxt_line)
            ):
                nxt_line = None

        _append_wrapped(paragraph_parts, line)
        if not nxt_line or not _should_join_lines(line, nxt_line):
            flush_paragraph()
            ensure_blank_line()
        i += 1

    flush_paragraph()
    # Trim trailing blank lines
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out).rstrip() + "\n"


def _validate_markers(expected: list[str], output: str) -> list[str]:
    missing = [m for m in expected if m not in output]
    return missing


def _call_openai_translate(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_text: str,
    *,
    max_output_tokens: int,
    reasoning_effort: str | None,
    temperature: float,
    max_attempts: int,
) -> str:
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            kwargs = {}
            if reasoning_effort:
                kwargs["reasoning"] = {"effort": reasoning_effort}

            resp = client.responses.create(
                model=model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                **kwargs,
            )
            return (resp.output_text or "").rstrip() + "\n"
        except (RateLimitError, APIConnectionError, APIError) as exc:
            last_exc = exc
            sleep_s = min(60.0, 2.0**attempt)
            print(
                f"[warn] OpenAI error (attempt {attempt}/{max_attempts}): {exc}\n"
                f"       sleeping {sleep_s:.1f}s then retrying...",
                file=sys.stderr,
            )
            time.sleep(sleep_s)
        except Exception as exc:  # pragma: no cover
            last_exc = exc
            break

    raise SystemExit(f"ERROR: OpenAI request failed after {max_attempts} attempts.\n{last_exc}")


def main(argv: list[str]) -> int:
    _load_dotenv_if_present()

    parser = argparse.ArgumentParser(
        description="Translate a Markdown file from Spanish to English using the OpenAI API, in resumable chunks."
    )
    parser.add_argument("in_md", type=Path, help="Input Spanish Markdown file")
    parser.add_argument("out_md", type=Path, help="Output English Markdown file")
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_MODEL", "gpt-5.2"),
        help="OpenAI model name (default: env OPENAI_MODEL or gpt-5.2).",
    )
    parser.add_argument(
        "--english-variant",
        default=os.environ.get("OPENAI_ENGLISH_VARIANT", "uk"),
        choices=["uk", "us", "international"],
        help="Target English variant (default: env OPENAI_ENGLISH_VARIANT or uk).",
    )
    parser.add_argument(
        "--output-style",
        default=os.environ.get("OPENAI_MD_OUTPUT_STYLE", "paragraphs"),
        choices=["paragraphs", "preserve"],
        help="Markdown output style (default: env OPENAI_MD_OUTPUT_STYLE or paragraphs).",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=12000,
        help="Approx max characters per chunk (default 12000).",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=8192,
        help="Max output tokens per chunk (default 8192).",
    )
    parser.add_argument(
        "--reasoning-effort",
        default="none",
        choices=["none", "minimal", "medium", "high"],
        help="Reasoning effort (default none).",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature (default 0.0).",
    )
    parser.add_argument(
        "--glossary-json",
        type=Path,
        default=None,
        help="Optional JSON glossary mapping Spanish->English.",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="Directory for resumable chunk outputs (default: <out_md>.work).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not call the API; just print chunk counts and sizes.",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=6,
        help="Max attempts per chunk on transient errors (default 6).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-translate and overwrite existing chunk outputs in the workdir.",
    )

    args = parser.parse_args(argv)

    in_md: Path = args.in_md
    out_md: Path = args.out_md
    if not in_md.exists():
        print(f"ERROR: Input Markdown not found: {in_md}", file=sys.stderr)
        return 2

    glossary = _load_glossary(args.glossary_json)
    system_prompt = _make_system_prompt(glossary, english_variant=args.english_variant)

    markdown = in_md.read_text(encoding="utf-8")
    if not PAGE_MARKER_RE.search(markdown):
        print(
            "[warn] No <!-- Page: N --> markers found. For best reliability, run the extractor with --page-markers.",
            file=sys.stderr,
        )
    segments = _split_into_page_segments(markdown)
    chunks = _build_chunks(segments, max_chars=args.max_chars)

    if args.dry_run:
        total_chars = sum(len(c.text) for c in chunks)
        print(f"chunks={len(chunks)} total_chars={total_chars} max_chars={args.max_chars}")
        for c in chunks[:10]:
            print(
                f"- chunk {c.index:04d}: chars={len(c.text)} markers={len(c.expected_page_markers)}"
            )
        if len(chunks) > 10:
            print(f"... ({len(chunks) - 10} more)")
        return 0

    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY is not set in the environment.", file=sys.stderr)
        print("Set it like: export OPENAI_API_KEY='...'", file=sys.stderr)
        print("Or create a .env file (see env.example).", file=sys.stderr)
        return 2

    workdir: Path = args.workdir or Path(str(out_md) + ".work")
    workdir.mkdir(parents=True, exist_ok=True)

    manifest_path = workdir / "manifest.json"
    if not manifest_path.exists() or args.force:
        manifest = {
            "input": str(in_md),
            "output": str(out_md),
            "model": args.model,
            "max_chars": args.max_chars,
            "max_output_tokens": args.max_output_tokens,
            "reasoning_effort": args.reasoning_effort,
            "temperature": args.temperature,
            "chunks": [
                {
                    "index": c.index,
                    "chars": len(c.text),
                    "expected_page_markers": c.expected_page_markers,
                    "outfile": f"chunk-{c.index:04d}.en.md",
                }
                for c in chunks
            ],
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    client = OpenAI(
        api_key=os.environ.get("OPENAI_API_KEY"),
        base_url=os.environ.get("OPENAI_BASE_URL"),
        organization=os.environ.get("OPENAI_ORG_ID"),
        project=os.environ.get("OPENAI_PROJECT_ID"),
    )

    translated_chunks: list[str] = []
    for c in chunks:
        out_chunk = workdir / f"chunk-{c.index:04d}.en.md"
        if out_chunk.exists() and not args.force:
            translated_chunks.append(out_chunk.read_text(encoding="utf-8"))
            print(f"[skip] chunk {c.index:04d} (exists)")
            continue

        print(f"[do] chunk {c.index:04d} (chars={len(c.text)} markers={len(c.expected_page_markers)})")
        out_text = _call_openai_translate(
            client,
            args.model,
            system_prompt,
            c.text,
            max_output_tokens=args.max_output_tokens,
            reasoning_effort=args.reasoning_effort,
            temperature=args.temperature,
            max_attempts=args.max_attempts,
        )

        missing = _validate_markers(c.expected_page_markers, out_text)
        if missing:
            msg = "\n".join(missing[:10])
            raise SystemExit(
                "ERROR: Model output is missing expected page markers.\n"
                f"Missing ({len(missing)}):\n{msg}\n\n"
                "Tip: re-run with a smaller --max-chars (e.g., 8000) and/or a larger --max-output-tokens."
            )

        out_chunk.write_text(out_text, encoding="utf-8")
        translated_chunks.append(out_text)

    out_md.parent.mkdir(parents=True, exist_ok=True)
    combined = "".join(translated_chunks).rstrip() + "\n"
    if args.output_style == "paragraphs":
        combined = _reflow_markdown_paragraphs(combined)
    out_md.write_text(combined, encoding="utf-8")
    print(f"[ok] wrote {out_md}")
    print(f"[ok] workdir {workdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
