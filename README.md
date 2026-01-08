# Translate `TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.pdf` to English (Markdown)

This repo includes two scripts:

- `scripts/pdf_extract_to_md.py` → extract PDF text into Spanish Markdown (optionally with page markers).
- `scripts/translate_md_openai.py` → translate that Markdown to English via the OpenAI API in resumable chunks.

## 1) Setup

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install pymupdf pypdf openai
```

## 2) Extract Spanish Markdown (ignores text inside images)

```sh
. .venv/bin/activate
python scripts/pdf_extract_to_md.py \
  TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.pdf \
  out/teorica_es.md \
  --page-markers
```

The `--page-markers` option adds invisible `<!-- Page: N -->` comments, which makes chunked translation safer and easier to validate.

## 3) Translate to English with OpenAI (GPT‑5 / GPT‑5.2)

Set credentials either by exporting env vars, or by creating a `.env` file (see `env.example`). The translation script will load `.env` automatically.

```sh
export OPENAI_API_KEY="YOUR_KEY"
export OPENAI_MODEL="gpt-5.2"   # or "gpt-5"
export OPENAI_ENGLISH_VARIANT="uk"  # uk | us | international
export OPENAI_MD_OUTPUT_STYLE="paragraphs"  # paragraphs | preserve

. .venv/bin/activate
python scripts/translate_md_openai.py out/teorica_es.md out/teorica_en.md
```

Resuming: if it stops mid-run, re-run the same command. It writes per-chunk outputs to `out/teorica_en.md.work/` and will skip completed chunks.

Notes:
- Default is `uk`, which tends to match European road terminology (e.g., “give way”, “roundabout”).
- You can also override via CLI: `--english-variant uk|us|international`.
- Default output style is `paragraphs` (reflowed for reading). Override via env/CLI: `OPENAI_MD_OUTPUT_STYLE` / `--output-style`.

## Optional: glossary (recommended for consistency)

Create `glossary.json`:

```json
{
  "permiso B": "Category B driving licence",
  "Dirección General de Tráfico": "Directorate-General for Traffic (DGT)"
}
```

Then:

```sh
python scripts/translate_md_openai.py out/teorica_es.md out/teorica_en.md --glossary-json glossary.json
```

## Optional: convert Markdown → HTML

If you have pandoc:

```sh
pandoc out/teorica_en.md -o out/teorica_en.html
```
