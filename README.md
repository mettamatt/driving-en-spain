# Spanish Driving Theory Manual — English Translation

An English translation of Spain's official DGT driving theory manual, using British English to match the language used in the DGT's English exam. Designed to help English speakers study for the Spanish driving test.

## Download

- **English (Markdown):** [`TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.en.md`](out/marker_es/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.en.md)
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
