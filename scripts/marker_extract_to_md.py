#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


MARKER_PAGINATION_RE = re.compile(r"^\{(\d+)\}-+\s*$")


def _convert_marker_pagination_to_page_markers(markdown: str) -> str:
    """
    Marker pagination format (when `--paginate_output` is used) looks like:

        {0}------------------------------------------------

    where the number is a 0-based page index.

    Convert these to the repo's standard:

        <!-- Page: 1 -->
    """
    out_lines: list[str] = []
    for raw in markdown.splitlines():
        line = raw.rstrip("\n")
        m = MARKER_PAGINATION_RE.match(line.strip())
        if not m:
            out_lines.append(line)
            continue
        page_number = int(m.group(1)) + 1  # 1-based for humans + our other scripts
        out_lines.append(f"<!-- Page: {page_number} -->")
    return "\n".join(out_lines).rstrip() + "\n"


def _ensure_cache_env(cache_root: Path) -> None:
    """
    Marker/surya default to OS cache directories (e.g. ~/Library/Caches on macOS).
    In some sandboxed environments this is not writable, so we default caches into
    the repo under out/.cache.
    """
    model_cache_dir = Path(os.environ.get("MODEL_CACHE_DIR", str(cache_root / "datalab" / "models")))
    hf_home = Path(os.environ.get("HF_HOME", str(cache_root / "huggingface")))

    model_cache_dir.mkdir(parents=True, exist_ok=True)
    hf_home.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("MODEL_CACHE_DIR", str(model_cache_dir))
    os.environ.setdefault("HF_HOME", str(hf_home))
    os.environ.setdefault("XDG_CACHE_HOME", str(cache_root))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a PDF to Markdown using marker-pdf (with image extraction). "
            "Optionally rewrites marker pagination into <!-- Page: N --> comments."
        )
    )
    parser.add_argument("pdf_path", type=Path, help="Input PDF file.")
    parser.add_argument(
        "output_dir",
        type=Path,
        help="Output directory (marker will create a subfolder named after the PDF).",
    )
    parser.add_argument(
        "--page-range",
        dest="page_range",
        type=str,
        default=None,
        help='0-based pages/ranges, e.g. "0,5-10,20" (marker convention).',
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Enable marker's hybrid (LLM) mode (requires configuring an LLM backend).",
    )
    parser.add_argument(
        "--force-ocr",
        action="store_true",
        help="Force OCR on the whole document (slower, but can help with garbled PDFs).",
    )
    parser.add_argument(
        "--strip-existing-ocr",
        action="store_true",
        help="Strip existing OCR text and re-OCR with surya.",
    )
    parser.add_argument(
        "--disable-ocr",
        action="store_true",
        help="Disable OCR entirely (faster when the PDF has good embedded text).",
    )
    parser.add_argument(
        "--disable-image-extraction",
        action="store_true",
        help="Do not extract/save images; images may be replaced with descriptions in LLM mode.",
    )
    parser.add_argument(
        "--no-page-markers",
        action="store_true",
        help="Do not rewrite marker's {N}----- pagination lines into <!-- Page: N --> comments.",
    )
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=None,
        help="Cache root for model downloads (default: out/.cache).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable marker debug outputs (saves layout images/json into the output folder).",
    )

    args = parser.parse_args(argv)

    pdf_path: Path = args.pdf_path
    if not pdf_path.exists() or not pdf_path.is_file():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        return 2

    repo_root = Path(__file__).resolve().parent.parent
    cache_root = args.cache_root or (repo_root / "out" / ".cache")
    _ensure_cache_env(cache_root)

    # Import marker lazily so env overrides apply before surya settings are loaded.
    try:
        from marker.config.parser import ConfigParser
        from marker.models import create_model_dict
        from marker.output import save_output
    except Exception as exc:
        print(
            "ERROR: marker-pdf is not installed.\n\n"
            "Install with:\n"
            "  python -m pip install marker-pdf\n",
            file=sys.stderr,
        )
        print(str(exc), file=sys.stderr)
        return 2

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    marker_cli_options: dict[str, object] = {
        "output_dir": str(output_dir),
        "output_format": "markdown",
        "paginate_output": True,
        "page_range": args.page_range,
        "use_llm": bool(args.use_llm),
        "force_ocr": bool(args.force_ocr),
        "strip_existing_ocr": bool(args.strip_existing_ocr),
        "disable_ocr": bool(args.disable_ocr),
        "disable_image_extraction": bool(args.disable_image_extraction),
        "debug": bool(args.debug),
    }

    config_parser = ConfigParser(marker_cli_options)
    models = create_model_dict()

    converter_cls = config_parser.get_converter_cls()
    converter = converter_cls(
        config=config_parser.generate_config_dict(),
        artifact_dict=models,
        processor_list=config_parser.get_processors(),
        renderer=config_parser.get_renderer(),
        llm_service=config_parser.get_llm_service(),
    )

    rendered = converter(str(pdf_path))
    out_folder = Path(config_parser.get_output_folder(str(pdf_path)))
    base_filename = config_parser.get_base_filename(str(pdf_path))
    save_output(rendered, str(out_folder), base_filename)

    md_path = out_folder / f"{base_filename}.md"
    if not md_path.exists():
        print(f"ERROR: Expected marker output not found: {md_path}", file=sys.stderr)
        return 2

    if not args.no_page_markers:
        md_raw = md_path.read_text(encoding="utf-8")
        md_fixed = _convert_marker_pagination_to_page_markers(md_raw)
        md_path.write_text(md_fixed, encoding="utf-8")

    print(f"[ok] markdown {md_path}")
    print(f"[ok] images live alongside markdown in {out_folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

