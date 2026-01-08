# Repository Guidelines

## Project Structure & Module Organization
- `scripts/` holds the Python tooling:
  - `pdf_extract_to_md.py` extracts text from the source PDF into Spanish Markdown.
  - `translate_md_openai.py` translates Markdown to English via the OpenAI API in resumable chunks.
- `out/` stores generated Markdown outputs (e.g., `out/teorica_es.md`). Treat as build artifacts.
- `TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.pdf` is the source document.
- `.env`/`.env.example` contain translation credentials and model config.

## Build, Test, and Development Commands
Set up a virtual environment and install dependencies:
```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install pymupdf pypdf openai
```
Extract Spanish Markdown (with page markers for safer translation):
```sh
python scripts/pdf_extract_to_md.py \
  TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.pdf \
  out/teorica_es.md \
  --page-markers
```
Translate to English (uses `.env` or exported vars):
```sh
python scripts/translate_md_openai.py out/teorica_es.md out/teorica_en.md
```
Optional HTML export (if `pandoc` is installed):
```sh
pandoc out/teorica_en.md -o out/teorica_en.html
```

## Coding Style & Naming Conventions
- Python 3 scripts; follow PEP 8 with 4‑space indentation.
- Use clear, descriptive names for new scripts and outputs (e.g., `teorica_es.md`).
- Keep generated files in `out/` and avoid committing large intermediates unless necessary.

## Testing Guidelines
- No automated test suite is present. If you add tests, place them under a `tests/` directory and document how to run them.

## Commit & Pull Request Guidelines
- No git history or commit convention is available in this repository. If you introduce one, document it here.
- For PRs, include a brief summary, list of commands run, and before/after samples for translated output when applicable.

## Security & Configuration Tips
- Keep `.env` private; do not commit real API keys.
- Prefer `.env.example` for sharing configuration defaults.
