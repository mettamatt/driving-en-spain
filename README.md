# Driving Theory (Spain) → English (Markdown)

Small, practical toolchain for:
1) extracting text from a Spanish driving theory PDF into Markdown, then
2) translating that Markdown into English via the OpenAI API, in resumable chunks.

This repo is optimized for the source document:
`TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.pdf`.

Notes:
- `out/teorica_en.md` is checked in so most users can just read the final result.
- `out/teorica_en.pdf` is checked in as a ready-to-read PDF export.
- `out/teorica_en.md.work/` (manifest + chunk outputs) is checked in so developers can rebuild/post-process without re-translating.
- Other generated files under `out/` are still treated as build artifacts (they’re gitignored by default).
- The PDF is gitignored; you must obtain it separately and place it in the repo root.
- This is not official material and not legal/learning advice — verify against current DGT sources.

## What’s in here

- `scripts/pdf_extract_to_md.py` → extract PDF text into Spanish Markdown (optionally with page markers).
- `scripts/translate_md_openai.py` → translate Markdown to English via the OpenAI API in resumable chunks.
- `scripts/markdown_postprocess.py` → post-process Markdown (paragraph reflow + a few known table conversions). This is called automatically by `translate_md_openai.py`.

## Outputs (checked in)

- English (Markdown): [`out/teorica_en.md`](out/teorica_en.md)
- English (PDF): [`out/teorica_en.pdf`](out/teorica_en.pdf)
- Spanish (Markdown): [`out/teorica_es.md`](out/teorica_es.md)

## Requirements

- Python 3.10+
- A working OpenAI API key (for translation)

## Quickstart

### 1) Setup

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install pymupdf pypdf openai
```

### 2) Extract Spanish Markdown (ignores text inside images)

```sh
. .venv/bin/activate
python scripts/pdf_extract_to_md.py \
  TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.pdf \
  out/teorica_es.md \
  --page-markers
```

`--page-markers` adds invisible `<!-- Page: N -->` comments. The translator uses these as “anchors” so chunked translation is safer and easier to validate.

Useful extractor flags:
- `--start-page N` / `--end-page N` to extract a subset
- `--backend pymupdf|pypdf` (PyMuPDF is usually better)

### 3) Translate to English with OpenAI

Set credentials either by exporting env vars, or by creating a `.env` file (see `env.example`). The translation script auto-loads `.env` from the repo root.

```sh
export OPENAI_API_KEY="YOUR_KEY"
export OPENAI_MODEL="gpt-5.2"              # default is gpt-5.2
export OPENAI_ENGLISH_VARIANT="uk"         # uk | us | international
export OPENAI_MD_OUTPUT_STYLE="paragraphs" # paragraphs | preserve

. .venv/bin/activate
python scripts/translate_md_openai.py out/teorica_es.md out/teorica_en.md
```

Resuming:
- If it stops mid-run, re-run the same command.
- Per-chunk outputs and a manifest are written to `out/teorica_en.md.work/` (by default); completed chunks are skipped.

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
python scripts/translate_md_openai.py out/teorica_es.md out/teorica_en.md --glossary-json glossary.json
```

## Optional: estimate chunking (no API calls)

```sh
python scripts/translate_md_openai.py out/teorica_es.md out/teorica_en.md --dry-run
```

## Optional: convert Markdown → HTML

If you have `pandoc` installed:

```sh
pandoc out/teorica_en.md -o out/teorica_en.html
```

## Troubleshooting

- Missing `<!-- Page: N -->` markers during translation:
  - Re-extract with `scripts/pdf_extract_to_md.py --page-markers`.
  - Or translate with smaller chunks: `--max-chars 8000` (and/or raise `--max-output-tokens`).
- Want to restart translation from scratch:
  - Delete `out/teorica_en.md.work/`, or re-run with `--force`.
- This repo does not do OCR:
  - If the PDF contains scanned images of text, you’ll need an OCR step before these scripts can help.
