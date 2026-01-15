#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


MARKER_PAGINATION_RE = re.compile(r"^\{(\d+)\}-+\s*$")
PAGE_MARKER_RE = re.compile(r"^\s*<!--\s*Page:\s*(\d+)\s*-->\s*$")
SERVICE_SIGN_BOLD_RE = re.compile(r"\*\*S-\d{1,3}[A-Za-z]*\*\*")
SERVICE_SIGN_CODE_RE = re.compile(r"\bS-\d{1,3}[A-Za-z]*\b")

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


def _split_md_table_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_md_table_separator_row(line: str) -> bool:
    # A permissive separator check: row contains pipes + dashes/colons/spaces, but no alphanumerics.
    if re.search(r"[A-Za-z0-9]", line):
        return False
    s = line.strip()
    if not s.startswith("|"):
        return False
    return bool(re.fullmatch(r"[|\-: ]+", s))


def _next_picture_index(output_folder: Path, page_index: int) -> int:
    pattern = f"_page_{page_index}_Picture_"
    max_n = 0
    for p in output_folder.iterdir():
        name = p.name
        if not name.startswith(pattern):
            continue
        m = re.match(rf"^{re.escape(pattern)}(\d+)\.", name)
        if not m:
            continue
        max_n = max(max_n, int(m.group(1)))
    return max_n + 1


def _extract_right_column_images_by_y(page) -> list[tuple[int, float]]:
    """
    Return a list of (xref, y_center) for images that look like "sign icons" in the
    right column of the page.
    """
    page_rect = page.rect
    min_x = page_rect.width * 0.6

    candidates: list[tuple[int, float]] = []
    for img in page.get_images(full=True):
        xref = int(img[0])
        for r in page.get_image_rects(xref):
            # Filter to right column and roughly "icon sized".
            if r.x0 < min_x:
                continue
            if r.width < 15 or r.width > 120:
                continue
            if r.height < 15 or r.height > 160:
                continue
            candidates.append((xref, (r.y0 + r.y1) / 2))

    # De-dup xrefs keeping the topmost occurrence (not expected here, but safe).
    best_by_xref: dict[int, float] = {}
    for xref, cy in candidates:
        best_by_xref[xref] = min(best_by_xref.get(xref, cy), cy)

    return sorted(best_by_xref.items(), key=lambda t: t[1])


def _map_codes_to_images(pdf_path: Path, *, page_number: int, codes: list[str]) -> dict[str, int]:
    """
    Best-effort mapping of sign codes (e.g. S-114) to image xrefs on the corresponding PDF page.
    Uses vertical order matching.
    """
    try:
        import fitz  # PyMuPDF
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("PyMuPDF (fitz) is required for image backfill.") from exc

    doc = fitz.open(pdf_path)
    try:
        page_index = page_number - 1
        if page_index < 0 or page_index >= doc.page_count:
            return {}
        page = doc[page_index]

        images = _extract_right_column_images_by_y(page)
        if not images:
            return {}

        code_positions: list[tuple[str, float]] = []
        for code in codes:
            rects = page.search_for(code)
            if not rects:
                continue
            r = rects[0]
            code_positions.append((code, (r.y0 + r.y1) / 2))

        if len(code_positions) < 2:
            return {}

        code_positions.sort(key=lambda t: t[1])

        # Greedy matching by vertical proximity.
        remaining = images.copy()
        mapping: dict[str, int] = {}
        for code, cy in code_positions:
            if not remaining:
                break
            best_idx = min(range(len(remaining)), key=lambda j: abs(remaining[j][1] - cy))
            xref, _ = remaining.pop(best_idx)
            mapping[code] = xref

        return mapping
    finally:
        doc.close()


def _save_xref_as_png(pdf_path: Path, *, xref: int, out_path: Path) -> None:
    try:
        import fitz  # PyMuPDF
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("PyMuPDF (fitz) is required for image backfill.") from exc

    doc = fitz.open(pdf_path)
    try:
        pix = fitz.Pixmap(doc, xref)
        try:
            if pix.alpha or pix.n > 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            pix.save(out_path)
        finally:
            pix = None  # help GC on large docs
    finally:
        doc.close()


def _maybe_backfill_service_signs(
    markdown: str,
    *,
    pdf_path: Path,
    output_folder: Path,
    md_image_prefix: str = "",
) -> tuple[str, int]:
    """
    Fixup pass for marker outputs where "service sign" icons are missing from the Markdown,
    most commonly because the PDF embeds them as JPX (JPEG2000) images which marker may not extract.

    Currently handles:
    - A 2-column service-sign table with an empty right column (e.g. S-107..S-113).
    - A single run-on line with multiple **S-###** entries (e.g. S-114..S-120), splitting
      into one entry per sign and inserting the missing icons.
    """
    md_image_prefix = (md_image_prefix or "").strip()
    if md_image_prefix and not md_image_prefix.endswith("/"):
        md_image_prefix += "/"

    def md_path_for_filename(filename: str) -> str:
        return f"{md_image_prefix}{filename}" if md_image_prefix else filename

    lines = markdown.splitlines()
    out_lines: list[str] = []
    current_page: int | None = None
    images_written = 0

    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\n")
        m_page = PAGE_MARKER_RE.match(line.strip())
        if m_page:
            current_page = int(m_page.group(1))
            out_lines.append(line)
            i += 1
            continue

        # Backfill tables like:
        # | S-107 ... |  |
        # | --- | -- |
        if current_page is not None and line.strip().startswith("|") and "S-" in line:
            table_block: list[str] = []
            while i < len(lines):
                ln = lines[i].rstrip("\n")
                if ln.strip() == "":
                    break
                if not ln.strip().startswith("|"):
                    break
                table_block.append(ln)
                i += 1

            # Analyze content rows.
            row_infos: list[tuple[int, str, list[str]]] = []
            max_cols = 0
            for row_idx, row in enumerate(table_block):
                if _is_md_table_separator_row(row):
                    continue
                cells = _split_md_table_row(row)
                max_cols = max(max_cols, len(cells))
                code_m = SERVICE_SIGN_CODE_RE.search(cells[0])
                if not code_m:
                    continue
                code = code_m.group(0)
                row_infos.append((row_idx, code, cells))

            if not row_infos:
                out_lines.extend(table_block)
                continue

            wants_one_col_upgrade = max_cols == 1
            wants_two_col_fill = max_cols == 2 and all(
                len(cells) >= 2 and not cells[1].strip() for _, _, cells in row_infos
            )

            if not (wants_one_col_upgrade or wants_two_col_fill):
                out_lines.extend(table_block)
                continue

            codes = [code for _, code, _ in row_infos]
            mapping = _map_codes_to_images(pdf_path, page_number=current_page, codes=codes)
            if len(mapping) < len(codes):
                # Can't confidently backfill; preserve original.
                out_lines.extend(table_block)
                continue

            page_index = current_page - 1
            next_pic = _next_picture_index(output_folder, page_index)
            xref_to_filename: dict[int, str] = {}

            code_to_md_image: dict[str, str] = {}
            for code in codes:
                xref = mapping[code]
                if xref not in xref_to_filename:
                    filename = f"_page_{page_index}_Picture_{next_pic}.png"
                    next_pic += 1
                    _save_xref_as_png(pdf_path, xref=xref, out_path=output_folder / filename)
                    xref_to_filename[xref] = filename
                    images_written += 1
                code_to_md_image[code] = f"![]({md_path_for_filename(xref_to_filename[xref])})"

            # Re-render the table with a second "icon" column.
            new_table: list[str] = []
            for row in table_block:
                if _is_md_table_separator_row(row):
                    new_table.append("| --- | --- |")
                    continue
                cells = _split_md_table_row(row)
                code_m = SERVICE_SIGN_CODE_RE.search(cells[0])
                if not code_m:
                    new_table.append(row)
                    continue
                code = code_m.group(0)
                img_cell = code_to_md_image.get(code)
                if not img_cell:
                    new_table.append(row)
                    continue
                new_table.append(f"| {cells[0].strip()} | {img_cell} |")

            out_lines.extend(new_table)
            continue

        # Backfill run-on "service sign" lines like: **S-114** ... **S-115** ...
        if current_page is not None:
            matches = list(SERVICE_SIGN_BOLD_RE.finditer(line))
            # Avoid splitting short ranges like "**S-820** y **S-821**" or "**S-850** a **S-853**".
            if len(matches) >= 3:
                entries: list[tuple[str, str]] = []
                for idx, mm in enumerate(matches):
                    start = mm.start()
                    end = matches[idx + 1].start() if idx + 1 < len(matches) else len(line)
                    chunk = line[start:end].strip()
                    if not chunk:
                        continue
                    # Extract code token and description.
                    m_code = SERVICE_SIGN_BOLD_RE.match(chunk)
                    if not m_code:
                        continue
                    code_token = m_code.group(0)
                    code = code_token.strip("*")
                    desc = chunk[len(code_token) :].strip()
                    entries.append((code, f"{code_token} {desc}".strip()))

                if len(entries) >= 3:
                    codes = [c for c, _ in entries]
                    mapping = _map_codes_to_images(pdf_path, page_number=current_page, codes=codes)
                    if len(mapping) == len(codes):
                        page_index = current_page - 1
                        next_pic = _next_picture_index(output_folder, page_index)
                        xref_to_filename: dict[int, str] = {}

                        for code, rendered in entries:
                            out_lines.append(rendered)
                            out_lines.append("")

                            xref = mapping[code]
                            if xref not in xref_to_filename:
                                filename = f"_page_{page_index}_Picture_{next_pic}.png"
                                next_pic += 1
                                _save_xref_as_png(
                                    pdf_path, xref=xref, out_path=output_folder / filename
                                )
                                xref_to_filename[xref] = filename
                                images_written += 1

                            out_lines.append(f"![]({md_path_for_filename(xref_to_filename[xref])})")
                            out_lines.append("")

                        # Trim trailing blank line.
                        while out_lines and out_lines[-1] == "":
                            out_lines.pop()
                        i += 1
                        continue

        out_lines.append(line)
        i += 1

    return "\n".join(out_lines).rstrip() + "\n", images_written


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
        "--no-backfill-sign-images",
        action="store_true",
        help=(
            "Skip a best-effort fixup pass that backfills some missing service sign icons "
            "(e.g. S-107..S-120) from the source PDF into the Markdown output."
        ),
    )
    parser.add_argument(
        "--no-page-markers",
        action="store_true",
        help="Do not rewrite marker's {N}----- pagination lines into <!-- Page: N --> comments.",
    )
    parser.add_argument(
        "--backfill-sign-images-only",
        action="store_true",
        help=(
            "Do not run marker extraction; only run the sign icon backfill pass against an "
            "existing extracted Markdown file in the target output folder."
        ),
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

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # marker output conventions
    base_filename = pdf_path.stem
    out_folder = output_dir / base_filename
    md_path = out_folder / f"{base_filename}.md"

    if args.backfill_sign_images_only:
        if not md_path.exists():
            print(
                "ERROR: Expected existing marker output not found.\n"
                f"  markdown: {md_path}\n\n"
                "Tip: run extraction first:\n"
                "  python scripts/marker_extract_to_md.py <pdf> <output_dir> --disable-ocr",
                file=sys.stderr,
            )
            return 2

        if not args.no_page_markers:
            md_raw = md_path.read_text(encoding="utf-8")
            md_fixed = _convert_marker_pagination_to_page_markers(md_raw)
            md_path.write_text(md_fixed, encoding="utf-8")

        if not args.disable_image_extraction and not args.no_backfill_sign_images:
            try:
                md_current = md_path.read_text(encoding="utf-8")
                md_current, images_written = _maybe_backfill_service_signs(
                    md_current, pdf_path=pdf_path, output_folder=out_folder
                )
                if images_written:
                    md_path.write_text(md_current, encoding="utf-8")
                    print(f"[fixup] backfilled {images_written} service sign icons from PDF")
            except Exception as exc:  # pragma: no cover
                print(f"[warn] sign icon backfill failed: {exc}", file=sys.stderr)

        # Targeted cleanup for marker's broken speed-limit tables (pages 252-257).
        try:
            from markdown_postprocess import fix_marker_speed_tables_252_257

            md_current = md_path.read_text(encoding="utf-8")
            md_fixed, tables_rewritten = fix_marker_speed_tables_252_257(md_current)
            if tables_rewritten:
                md_path.write_text(md_fixed, encoding="utf-8")
                print(f"[fixup] rewrote {tables_rewritten} speed tables (pages 252-257)")
        except Exception as exc:  # pragma: no cover
            print(f"[warn] speed table fixup failed: {exc}", file=sys.stderr)

        print(f"[ok] markdown {md_path}")
        print(f"[ok] images live alongside markdown in {out_folder}")
        return 0

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

        if not args.disable_image_extraction and not args.no_backfill_sign_images:
            try:
                md_current = md_path.read_text(encoding="utf-8")
                md_current, images_written = _maybe_backfill_service_signs(
                    md_current, pdf_path=pdf_path, output_folder=out_folder
                )
                if images_written:
                    md_path.write_text(md_current, encoding="utf-8")
                    print(f"[fixup] backfilled {images_written} service sign icons from PDF")
            except Exception as exc:  # pragma: no cover
                print(f"[warn] sign icon backfill failed: {exc}", file=sys.stderr)

        # Targeted cleanup for marker's broken speed-limit tables (pages 252-257).
        try:
            from markdown_postprocess import fix_marker_speed_tables_252_257

            md_current = md_path.read_text(encoding="utf-8")
            md_fixed, tables_rewritten = fix_marker_speed_tables_252_257(md_current)
            if tables_rewritten:
                md_path.write_text(md_fixed, encoding="utf-8")
                print(f"[fixup] rewrote {tables_rewritten} speed tables (pages 252-257)")
        except Exception as exc:  # pragma: no cover
            print(f"[warn] speed table fixup failed: {exc}", file=sys.stderr)

    print(f"[ok] markdown {md_path}")
    print(f"[ok] images live alongside markdown in {out_folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
