import sys
import unittest
import warnings
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import pdf_extract_to_md  # noqa: E402


class TestPdfExtractLayout(unittest.TestCase):
    def test_layout_extraction_page8_builds_tables(self) -> None:
        pdf_path = REPO_ROOT / "TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.pdf"
        if not pdf_path.exists():
            self.skipTest(f"PDF not present: {pdf_path}")

        try:
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            import fitz  # type: ignore
        except Exception:
            self.skipTest("PyMuPDF not installed (required for layout-aware extraction).")

        doc = fitz.open(str(pdf_path))
        page = doc.load_page(7)  # 0-based; page 8
        lines = pdf_extract_to_md._extract_page_layout_rows(page)
        text = "\n".join(lines)

        self.assertIn("| Remolque ligero | Remolque pesado |", text)
        self.assertIn("| Semirremolque ligero | Semirremolque no ligero |", text)
        # The definition label should survive outside table rows.
        self.assertIn("\nSemirremolque\n", "\n" + text + "\n")


if __name__ == "__main__":
    unittest.main()
