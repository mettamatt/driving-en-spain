# Spanish Driving Theory Manual — English Translation

An English translation of Spain's official DGT driving theory manual, using British English to match the language used in the DGT's English exam. Designed to help English speakers study for the Spanish driving test.

## Download

- **English (Markdown):** [`TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.en.md`](out/marker_es/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.en.md)
- **English (EPUB):** [`TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.en.epub`](out/marker_es/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.en.epub)
- **English (PDF):** [`TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.en.pdf`](out/marker_es/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.en.pdf)

**Disclaimer:** This is not official material — verify against current DGT sources.

---

# Developer Notes

This repo contains a small toolchain for extracting the Spanish driving theory PDF into Markdown (with images), then translating it to English via the OpenAI API.

Notes:
- The source PDF (`TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.pdf`) is gitignored; obtain it separately from the DGT.

## What's in here

- `scripts/marker_extract_to_md.py` → extract PDF to Spanish Markdown via `marker-pdf` (includes images).
- `scripts/translate_md_openai.py` → translate Markdown to English via the OpenAI API in resumable chunks.
- `scripts/markdown_postprocess.py` → post-process Markdown (paragraph reflow + a few known table conversions). This is called automatically by `translate_md_openai.py`.

Optional utilities:
- `scripts/postprocess_markdown_cli.py` → run the post-processor on a Markdown file.
- `scripts/slice_md_pages.py` → extract specific `<!-- Page: N -->` segments into a new Markdown file.
- `scripts/diff_pages.py` → diff specific page segments between two Markdown files.
- `scripts/backfill_sign_images.py` → backfill missing service-sign icons (e.g. S-107..S-120) from the PDF into an existing Markdown file.

## Requirements

- Python 3.10+
- `marker-pdf` (downloads model weights; GPU optional)
- A working OpenAI API key (for translation)

## Quickstart

### 1) Setup

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install marker-pdf openai
```

### 2) Extract Spanish Markdown (with images) using marker

```sh
. .venv/bin/activate
python scripts/marker_extract_to_md.py \
  TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.pdf \
  out/marker_es \
  --disable-ocr
```

This writes a folder under `out/marker_es/` containing:
- `TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.md` (Spanish Markdown)
- extracted images like `_page_123_Picture_4.jpeg`

`marker_extract_to_md.py` also rewrites marker’s pagination into `<!-- Page: N -->` comments so the translator can use them as “anchors”.

Notes:
- First run downloads several GB of model weights.
- On Apple Silicon, some parts of marker (notably table recognition) may fall back to CPU.

### 3) Translate to English with OpenAI

Set credentials either by exporting env vars, or by creating a `.env` file (see `env.example`). The translation script auto-loads `.env` from the repo root.

```sh
export OPENAI_API_KEY="YOUR_KEY"
export OPENAI_MODEL="gpt-5.2"              # default is gpt-5.2
export OPENAI_ENGLISH_VARIANT="uk"         # uk | us | international
export OPENAI_MD_OUTPUT_STYLE="paragraphs" # paragraphs | preserve

. .venv/bin/activate
python scripts/translate_md_openai.py \
  out/marker_es/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.md \
  out/marker_es/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.en.md \
  --output-style preserve
```

Resuming:
- If it stops mid-run, re-run the same command.
- Per-chunk outputs and a manifest are written to `<out_md>.work/` (by default); completed chunks are skipped.

### 4) (Optional) Generate a large-font PDF from Markdown

For iPhone/Apple Books, a **reflowable EPUB** is usually more pleasant than a PDF. You can generate one with:

```sh
python scripts/md_to_epub.py \
  out/marker_es/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.en.md \
  out/marker_es/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.en.epub \
  --toc \
  --split-level 2
```

`md_to_epub.py` also fixes a common Apple Books issue where raw HTML `<br>` tags inside Markdown tables can create invalid XHTML in the EPUB.

This repo includes `scripts/md_to_pdf.py`, which renders Markdown to a readable PDF with bigger defaults (font size, line spacing, margins).

It uses **pandoc + Chrome headless** by default (no TeX install required if you already have Google Chrome). If Chrome headless crashes on your macOS version, install `tectonic` and use `--backend=latex` (this also tends to produce nicer typography).

Install dependencies (macOS/Homebrew):

```sh
brew install pandoc
brew install tectonic   # only needed for --backend=latex
```

Example (LaTeX backend, 16pt font):

```sh
python scripts/md_to_pdf.py \
  out/marker_es/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.en.md \
  /tmp/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.en.large.pdf \
  --backend=latex \
  --pdf-engine=tectonic \
  --toc \
  --font-size-pt 16 \
  --line-height 1.35 \
  --paper a4
```

Tip (iPhone): PDFs are fixed-layout, so making them comfortable on a small screen usually means using a *smaller paper size* plus a bigger font. Try `--paper iphone` (or `--paper a6`) and a larger `--font-size-pt` (e.g. 22–26).

```sh
python scripts/md_to_pdf.py \
  out/marker_es/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.en.md \
  /tmp/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.en.iphone.pdf \
  --backend=latex \
  --pdf-engine=tectonic \
  --paper iphone \
  --font-size-pt 24 \
  --line-height 1.4 \
  --margin 6mm
```

Example (Chrome backend, 16pt font):

```sh
python scripts/md_to_pdf.py \
  out/marker_es/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.en.md \
  /tmp/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.en.large.pdf \
  --backend=chrome \
  --font-size-pt 16 \
  --line-height 1.35 \
  --paper a4
```

## Configuration

The translator reads configuration from environment variables. For local development, copy `env.example` to `.env` and fill it in.

- Required: `OPENAI_API_KEY`
- Optional: `OPENAI_MODEL` (defaults to `gpt-5.2`)
- Optional: `OPENAI_ENGLISH_VARIANT` (`uk` | `us` | `international`)
- Optional: `OPENAI_MD_OUTPUT_STYLE` (`paragraphs` | `preserve`)
- Optional (advanced): `OPENAI_BASE_URL`, `OPENAI_ORG_ID`, `OPENAI_PROJECT_ID`

All of the “optional” settings can also be set via CLI flags (run `python scripts/translate_md_openai.py --help`).

## Optional: glossary (recommended for consistency)

Create a `glossary.json` mapping Spanish → preferred English:

```json
{
  "permiso B": "Category B driving licence",
  "Dirección General de Tráfico": "Directorate-General for Traffic (DGT)"
}
```

Then run:

```sh
python scripts/translate_md_openai.py \
  out/marker_es/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.md \
  out/marker_es/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.en.md \
  --output-style preserve \
  --glossary-json glossary.json
```

## Optional: estimate chunking (no API calls)

```sh
python scripts/translate_md_openai.py \
  out/marker_es/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.md \
  out/marker_es/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.en.md \
  --dry-run
```

## Tests

This repo has a small regression test suite for Markdown post-processing.

```sh
. .venv/bin/activate
python -m unittest discover -s tests
```

## Troubleshooting

- Missing `<!-- Page: N -->` markers during translation:
  - Re-extract with `scripts/marker_extract_to_md.py` (default behaviour includes page markers).
  - Or translate with smaller chunks: `--max-chars 8000` (and/or raise `--max-output-tokens`).
- Want to restart translation from scratch:
  - Delete `<out_md>.work/`, or re-run with `--force`.
