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

from markdown_postprocess import postprocess_markdown


PAGE_MARKER_RE = re.compile(r"<!--\s*Page:\s*(\d+)\s*-->")


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
        "- Preserve the user's existing line breaks as much as possible (do not merge multiple lines into one).",
        "- Keep short term/label lines on their own line (e.g., 'Car', 'Pedestrian', 'Driving licence').",
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

    needs_api = args.force or any(
        not (workdir / f"chunk-{c.index:04d}.en.md").exists() for c in chunks
    )

    if needs_api and not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY is not set in the environment.", file=sys.stderr)
        print("Set it like: export OPENAI_API_KEY='...'", file=sys.stderr)
        print("Or create a .env file (see env.example).", file=sys.stderr)
        return 2

    client = None
    if needs_api:
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
        assert client is not None
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
    combined = postprocess_markdown(combined, style=args.output_style, enable_tables=True)
    out_md.write_text(combined, encoding="utf-8")
    print(f"[ok] wrote {out_md}")
    print(f"[ok] workdir {workdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
