#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


DEFAULT_FONT_SIZE_PT = 14.0
DEFAULT_LINE_HEIGHT = 1.35
DEFAULT_MARGIN = "22mm"
DEFAULT_PAPER = "a4"

PAPER_SIZES_MM: dict[str, tuple[float, float]] = {
    "a4": (210.0, 297.0),
    "letter": (216.0, 279.0),
    "a5": (148.0, 210.0),
    "a6": (105.0, 148.0),
    # Approximate phone-friendly size. If you want a specific model, pass --paper-width/--paper-height
    # (not currently supported) or add a preset.
    "iphone": (72.0, 152.0),
}


def _which(cmd: str) -> str | None:
    return shutil.which(cmd)


def _find_chrome(explicit_path: str | None) -> str | None:
    if explicit_path:
        explicit = Path(explicit_path).expanduser()
        if explicit.exists():
            return str(explicit)
        resolved = _which(explicit_path)
        if resolved:
            return resolved

    env_path = os.environ.get("CHROME_PATH")
    if env_path:
        candidate = Path(env_path).expanduser()
        if candidate.exists():
            return str(candidate)
        resolved = _which(env_path)
        if resolved:
            return resolved

    candidates: list[str] = []
    # Standalone headless binary (recommended by Chrome team).
    resolved = _which("chrome-headless-shell")
    if resolved:
        candidates.append(resolved)

    for name in ("google-chrome", "chromium", "chromium-browser"):
        resolved = _which(name)
        if resolved:
            candidates.append(resolved)

    # macOS default (if Chrome is installed).
    mac_app = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if mac_app.exists():
        candidates.append(str(mac_app))

    return candidates[0] if candidates else None


def _find_pdf_engine(explicit_engine: str | None) -> str | None:
    if explicit_engine:
        return _which(explicit_engine) and explicit_engine

    for engine in ("tectonic", "xelatex", "lualatex", "pdflatex"):
        if _which(engine):
            return engine
    return None


def _paper_dimensions_mm(paper: str) -> tuple[float, float]:
    key = paper.lower()
    if key not in PAPER_SIZES_MM:
        raise ValueError(f"Unsupported paper: {paper}")
    return PAPER_SIZES_MM[key]


def _css_page_size(paper: str) -> str:
    width_mm, height_mm = _paper_dimensions_mm(paper)
    return f"{width_mm:g}mm {height_mm:g}mm"


def _html_header(
    *,
    base_uri: str,
    font_size_pt: float,
    line_height: float,
    margin: str,
    paper: str,
) -> str:
    paper_css = _css_page_size(paper)
    # Keep CSS conservative so it prints predictably.
    return "\n".join(
        [
            f'<base href="{base_uri}">',
            "<style>",
            f"@page {{ size: {paper_css}; margin: {margin}; }}",
            f"html {{ font-size: {font_size_pt}pt; }}",
            "body {",
            '  font-family: -apple-system, BlinkMacSystemFont, "Georgia", "Times New Roman", serif;',
            f"  line-height: {line_height};",
            "  color: #111;",
            "}",
            "img { max-width: 100%; height: auto; }",
            "p { margin: 0.6em 0; }",
            "h1 { font-size: 1.6em; margin: 1.2em 0 0.5em; }",
            "h2 { font-size: 1.35em; margin: 1.1em 0 0.45em; }",
            "h3 { font-size: 1.2em; margin: 1.0em 0 0.4em; }",
            "h4 { font-size: 1.1em; margin: 0.9em 0 0.35em; }",
            "ul, ol { margin: 0.5em 0 0.7em 1.35em; }",
            "li { margin: 0.25em 0; }",
            "table { width: 100%; border-collapse: collapse; margin: 1em 0; }",
            "th, td { border: 1px solid #ddd; padding: 6px 8px; vertical-align: top; "
            "word-break: break-word; }",
            "a { color: #0645ad; }",
            "code, pre {",
            '  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;',
            "  font-size: 0.95em;",
            "}",
            "pre { white-space: pre-wrap; }",
            "</style>",
            "",
        ]
    )


def _run(cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Required command not found: {cmd[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Command failed (exit={exc.returncode}): {cmd[0]}") from exc


def _render_via_chrome(
    *,
    in_md: Path,
    out_pdf: Path,
    resource_root: Path,
    title: str,
    toc: bool,
    font_size_pt: float,
    line_height: float,
    margin: str,
    paper: str,
    chrome_path: str | None,
    keep_html: bool,
) -> None:
    pandoc = _which("pandoc")
    if not pandoc:
        raise RuntimeError("pandoc is required. Install it (e.g. `brew install pandoc`).")

    chrome = _find_chrome(chrome_path)
    if not chrome:
        raise RuntimeError(
            "Chrome/Chromium is required for the `chrome` backend. "
            "Install Google Chrome, or pass --backend=latex with a TeX engine."
        )

    base_uri = resource_root.expanduser().resolve().as_uri()
    if not base_uri.endswith("/"):
        base_uri += "/"

    with tempfile.TemporaryDirectory(prefix="md_to_pdf_") as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        header_path = tmp_dir_path / "header.html"
        html_path = tmp_dir_path / "document.html"
        chrome_profile = tmp_dir_path / "chrome_profile"
        chrome_profile.mkdir(parents=True, exist_ok=True)

        header_path.write_text(
            _html_header(
                base_uri=base_uri,
                font_size_pt=font_size_pt,
                line_height=line_height,
                margin=margin,
                paper=paper,
            ),
            encoding="utf-8",
        )

        pandoc_cmd = [
            pandoc,
            str(in_md),
            "--from=gfm+raw_html",
            "--to=html",
            "--standalone",
            "--metadata",
            f"title={title}",
            "--include-in-header",
            str(header_path),
            "-o",
            str(html_path),
        ]
        if toc:
            pandoc_cmd.append("--toc")

        _run(pandoc_cmd)

        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        chrome_cmd = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={chrome_profile}",
            "--print-to-pdf-no-header",
            f"--print-to-pdf={out_pdf}",
            html_path.as_uri(),
        ]
        try:
            _run(chrome_cmd)
        except Exception as exc:
            raise RuntimeError(
                "Chrome headless failed while printing. "
                "If you're on macOS and see Chrome crash with SIGABRT, "
                "try installing a TeX engine and using `--backend=latex` "
                "(recommended: `brew install tectonic`). "
                "Alternatively, install `chromium` or `chrome-headless-shell` and "
                "pass `--chrome-path`."
            ) from exc

        if keep_html:
            keep_path = out_pdf.with_suffix(".html")
            shutil.copy2(html_path, keep_path)


def _render_via_latex(
    *,
    in_md: Path,
    out_pdf: Path,
    resource_root: Path,
    title: str,
    toc: bool,
    font_size_pt: float,
    line_height: float,
    margin: str,
    paper: str,
    pdf_engine: str | None,
) -> None:
    pandoc = _which("pandoc")
    if not pandoc:
        raise RuntimeError("pandoc is required. Install it (e.g. `brew install pandoc`).")

    engine = _find_pdf_engine(pdf_engine)
    if not engine:
        raise RuntimeError(
            "No TeX engine found for the `latex` backend. "
            "Install one (recommended: `brew install tectonic`), "
            "or use --backend=chrome."
        )

    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    paper_width_mm, paper_height_mm = _paper_dimensions_mm(paper)

    cmd = [
        pandoc,
        str(in_md),
        "--from=gfm+raw_html",
        "--pdf-engine",
        engine,
        "--metadata",
        f"title={title}",
        "--variable",
        f"fontsize={font_size_pt}pt",
        "--variable",
        f"linestretch={line_height}",
        "--variable",
        f"geometry:margin={margin}",
        "--variable",
        f"geometry:paperwidth={paper_width_mm:g}mm",
        "--variable",
        f"geometry:paperheight={paper_height_mm:g}mm",
        "--resource-path",
        str(resource_root),
        "-o",
        str(out_pdf),
    ]

    if toc:
        cmd.append("--toc")

    # Fonts (works for tectonic/xelatex/lualatex; ignored by pdflatex).
    if engine in {"tectonic", "xelatex", "lualatex"}:
        cmd.extend(
            [
                "--variable",
                "mainfont=TeX Gyre Pagella",
                "--variable",
                "sansfont=TeX Gyre Heros",
                "--variable",
                "monofont=TeX Gyre Cursor",
            ]
        )

    _run(cmd)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Render a Markdown file to a readable PDF with a larger default font.\n\n"
            "Backends:\n"
            "  - auto  : prefer LaTeX if a TeX engine is installed, else use Chrome headless\n"
            "  - chrome: pandoc -> HTML -> Chrome headless print-to-pdf\n"
            "  - latex : pandoc -> PDF via a TeX engine (tectonic/xelatex/lualatex/pdflatex)\n"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("in_md", type=Path, help="Input Markdown file")
    parser.add_argument("out_pdf", type=Path, help="Output PDF path")
    parser.add_argument(
        "--backend",
        default="auto",
        choices=["auto", "chrome", "latex"],
        help="PDF backend (default: auto).",
    )
    parser.add_argument(
        "--resource-root",
        type=Path,
        default=None,
        help="Base directory for relative images/links (default: input file's folder).",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Document title (default: input filename).",
    )
    parser.add_argument(
        "--toc",
        action="store_true",
        help="Generate a table of contents from headings.",
    )
    parser.add_argument(
        "--font-size-pt",
        type=float,
        default=DEFAULT_FONT_SIZE_PT,
        help=f"Base font size in points (default: {DEFAULT_FONT_SIZE_PT:g}).",
    )
    parser.add_argument(
        "--line-height",
        type=float,
        default=DEFAULT_LINE_HEIGHT,
        help=f"Line height multiplier (default: {DEFAULT_LINE_HEIGHT:g}).",
    )
    parser.add_argument(
        "--margin",
        default=DEFAULT_MARGIN,
        help=f"Page margin (CSS unit, e.g. 22mm / 1in) (default: {DEFAULT_MARGIN}).",
    )
    parser.add_argument(
        "--paper",
        default=DEFAULT_PAPER,
        choices=sorted(PAPER_SIZES_MM.keys()),
        help=f"Paper size (default: {DEFAULT_PAPER}).",
    )
    parser.add_argument(
        "--chrome-path",
        default=None,
        help="Chrome/Chromium executable path (optional; env CHROME_PATH also works).",
    )
    parser.add_argument(
        "--pdf-engine",
        default=None,
        help="TeX engine for --backend=latex (e.g. tectonic, xelatex).",
    )
    parser.add_argument(
        "--keep-html",
        action="store_true",
        help="Also save the intermediate HTML next to the PDF.",
    )

    args = parser.parse_args(argv)

    in_md = args.in_md.expanduser()
    if not in_md.exists():
        print(f"ERROR: Input file not found: {in_md}", file=sys.stderr)
        return 2

    out_pdf = args.out_pdf.expanduser().resolve()
    resource_root = (args.resource_root or in_md.parent).expanduser()
    title = args.title or in_md.stem

    backend = args.backend
    if backend == "auto":
        backend = "latex" if _find_pdf_engine(args.pdf_engine) else "chrome"

    try:
        if backend == "chrome":
            _render_via_chrome(
                in_md=in_md,
                out_pdf=out_pdf,
                resource_root=resource_root,
                title=title,
                toc=args.toc,
                font_size_pt=args.font_size_pt,
                line_height=args.line_height,
                margin=args.margin,
                paper=args.paper,
                chrome_path=args.chrome_path,
                keep_html=args.keep_html,
            )
        else:
            _render_via_latex(
                in_md=in_md,
                out_pdf=out_pdf,
                resource_root=resource_root,
                title=title,
                toc=args.toc,
                font_size_pt=args.font_size_pt,
                line_height=args.line_height,
                margin=args.margin,
                paper=args.paper,
                pdf_engine=args.pdf_engine,
            )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"[ok] wrote {out_pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
