#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


MARKER_PAGINATION_RE = re.compile(r"^\{(\d+)\}-+\s*$")

LLM_SERVICE_ALIASES: dict[str, str] = {
    "openai": "marker.services.openai.OpenAIService",
    "gemini": "marker.services.gemini.GoogleGeminiService",
    "claude": "marker.services.claude.ClaudeService",
    "ollama": "marker.services.ollama.OllamaService",
    "vertex": "marker.services.vertex.VertexService",
    "azure_openai": "marker.services.azure_openai.AzureOpenAIService",
}


def _maybe_unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and (
        (value[0] == value[-1] == '"') or (value[0] == value[-1] == "'")
    ):
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
    model_cache_dir = Path(
        os.environ.get("MODEL_CACHE_DIR", str(cache_root / "datalab" / "models"))
    )
    hf_home = Path(os.environ.get("HF_HOME", str(cache_root / "huggingface")))

    model_cache_dir.mkdir(parents=True, exist_ok=True)
    hf_home.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("MODEL_CACHE_DIR", str(model_cache_dir))
    os.environ.setdefault("HF_HOME", str(hf_home))
    os.environ.setdefault("XDG_CACHE_HOME", str(cache_root))


def main(argv: list[str]) -> int:
    _load_dotenv_if_present()

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

    llm = parser.add_argument_group("LLM / hybrid options")
    llm.add_argument(
        "--use-llm",
        action="store_true",
        help="Enable marker's hybrid (LLM) mode (requires configuring an LLM backend).",
    )
    llm.add_argument(
        "--llm-service",
        type=str,
        default=None,
        help=(
            "LLM service to use in --use-llm mode. "
            "Examples: openai, gemini, claude, or a full import path like "
            "marker.services.openai.OpenAIService. "
            "Env fallback: MARKER_LLM_SERVICE."
        ),
    )
    llm.add_argument(
        "--llm-model",
        type=str,
        default=None,
        help=(
            "Optional LLM model name (service-specific). For OpenAI this maps to openai_model. "
            "Env fallback: MARKER_LLM_MODEL."
        ),
    )
    llm.add_argument(
        "--llm-page-correction-prompt",
        type=str,
        default=None,
        help=(
            "Enable marker's LLMPageCorrectionProcessor with this prompt. "
            "Useful for fixing misclassified blocks (e.g., tables extracted as headings). "
            "Env fallback: MARKER_LLM_PAGE_CORRECTION_PROMPT."
        ),
    )
    llm.add_argument(
        "--llm-disable-table-rewrite",
        action="store_true",
        help=(
            "Disable marker's LLMTableProcessor rewriting pass. This can reduce unwanted edits "
            "(e.g., replacing images inside table cells with 'Image: ...' placeholders)."
        ),
    )

    ocr = parser.add_argument_group("OCR options")
    ocr.add_argument(
        "--force-ocr",
        action="store_true",
        help="Force OCR on the whole document (slower, but can help with garbled PDFs).",
    )
    ocr.add_argument(
        "--strip-existing-ocr",
        action="store_true",
        help="Strip existing OCR text and re-OCR with surya.",
    )
    ocr.add_argument(
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

    if not args.use_llm and (
        args.llm_service
        or args.llm_model
        or args.llm_page_correction_prompt
        or args.llm_disable_table_rewrite
    ):
        print(
            "[warn] LLM flags were provided but --use-llm was not set; ignoring them.",
            file=sys.stderr,
        )

    llm_service = None
    llm_model = None
    llm_page_correction_prompt = None
    if args.use_llm:
        # Only consult env fallbacks when LLM mode is enabled.
        llm_service_arg = args.llm_service or os.environ.get("MARKER_LLM_SERVICE")
        llm_model = args.llm_model or os.environ.get("MARKER_LLM_MODEL")
        llm_page_correction_prompt = args.llm_page_correction_prompt or os.environ.get(
            "MARKER_LLM_PAGE_CORRECTION_PROMPT"
        )
        if llm_service_arg:
            llm_service = LLM_SERVICE_ALIASES.get(llm_service_arg.lower(), llm_service_arg)

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

    if llm_service:
        marker_cli_options["llm_service"] = llm_service

    openai_llm_service = LLM_SERVICE_ALIASES["openai"]
    if args.use_llm and llm_service == openai_llm_service:
        # Marker expects openai_api_key/openai_model config fields.
        openai_api_key = os.environ.get("OPENAI_API_KEY")
        if not openai_api_key:
            print(
                "ERROR: OPENAI_API_KEY is not set (required for --llm-service openai).",
                file=sys.stderr,
            )
            print("Tip: set it in your environment or add it to .env.", file=sys.stderr)
            return 2
        marker_cli_options["openai_api_key"] = openai_api_key

        openai_base_url = os.environ.get("OPENAI_BASE_URL")
        if openai_base_url:
            marker_cli_options["openai_base_url"] = openai_base_url

        if llm_model:
            marker_cli_options["openai_model"] = llm_model
        # Use PNG for broadest compatibility.
        marker_cli_options["openai_image_format"] = "png"

    if args.llm_model and not (args.use_llm and llm_service == openai_llm_service):
        # Keep the flag from being a no-op without surprising side effects.
        print(
            "[warn] --llm-model is currently only applied for --llm-service openai.",
            file=sys.stderr,
        )

    if llm_page_correction_prompt:
        marker_cli_options[
            "LLMPageCorrectionProcessor_block_correction_prompt"
        ] = llm_page_correction_prompt
    if args.use_llm and args.llm_disable_table_rewrite:
        marker_cli_options["LLMTableProcessor_use_llm"] = False

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
