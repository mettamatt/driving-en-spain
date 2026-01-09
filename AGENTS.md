# Repository Guidelines

## Project Structure & Module Organization
- `scripts/` holds the Python tooling:
  - `marker_extract_to_md.py` extracts the source PDF into Spanish Markdown via `marker-pdf` (includes images).
  - `translate_md_openai.py` translates Markdown to English via the OpenAI API in resumable chunks.
  - `markdown_postprocess.py` post-processes Markdown (paragraph reflow + a few known table conversions).
- `out/` stores generated Markdown outputs. The checked-in marker outputs live under `out/marker_es/...`.
- `TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.pdf` is the source document.
- `.env`/`env.example` contain translation credentials and model config.

## Build, Test, and Development Commands
Set up a virtual environment and install dependencies:
```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install marker-pdf openai
```
Extract Spanish Markdown (marker; includes images and page markers):
```sh
python scripts/marker_extract_to_md.py \
  TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.pdf \
  out/marker_es \
  --disable-ocr
```
Translate to English (uses `.env` or exported vars):
```sh
python scripts/translate_md_openai.py \
  out/marker_es/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.md \
  out/marker_es/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo/TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.en.md \
  --output-style preserve
```

## Coding Style & Naming Conventions
- Python 3 scripts; follow PEP 8 with 4‑space indentation.
- Use clear, descriptive names for new scripts and outputs.
- Keep generated files in `out/` and avoid committing large intermediates unless necessary.

## Testing Guidelines
- A small `unittest` regression suite lives under `tests/`.
- Run it with: `python -m unittest discover -s tests`.

## Commit & Pull Request Guidelines
- No git history or commit convention is available in this repository. If you introduce one, document it here.
- For PRs, include a brief summary, list of commands run, and before/after samples for translated output when applicable.

## Security & Configuration Tips
- Keep `.env` private; do not commit real API keys.
- Prefer `env.example` for sharing configuration defaults.
