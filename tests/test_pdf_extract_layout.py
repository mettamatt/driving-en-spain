import sys
import unittest
import warnings
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import pdf_extract_to_md  # noqa: E402


class TestPdfExtractLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pdf_path = REPO_ROOT / "TeoricaAbreviada_LecturaFacil_2025-06_Interactivo.pdf"
        if not pdf_path.exists():
            raise unittest.SkipTest(f"PDF not present: {pdf_path}")

        try:
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            import fitz  # type: ignore
        except Exception:
            raise unittest.SkipTest("PyMuPDF not installed (required for layout-aware extraction).")

        cls._fitz = fitz
        cls._doc = fitz.open(str(pdf_path))

    @classmethod
    def tearDownClass(cls) -> None:
        doc = getattr(cls, "_doc", None)
        if doc is not None:
            doc.close()

    def _extract(self, page_number: int) -> str:
        page = self._doc.load_page(page_number - 1)  # 0-based
        lines = pdf_extract_to_md._extract_page_layout_rows(page)
        return "\n".join(lines)

    def test_layout_extraction_page8_builds_tables(self) -> None:
        text = self._extract(8)

        self.assertIn("| Remolque ligero | Remolque pesado |", text)
        self.assertIn("| Semirremolque ligero | Semirremolque no ligero |", text)
        # The definition label should survive outside table rows.
        self.assertIn("\nSemirremolque\n", "\n" + text + "\n")

    def test_layout_extraction_page96_table_header_not_merged(self) -> None:
        text = self._extract(96)

        self.assertIn("| Problema | Posible solución |", text)
        self.assertNotIn("| Problema Los frenos |", text)
        self.assertIn("| Los frenos | Soltar el pedal de freno |", text)

    def test_layout_extraction_page100_table_header_not_merged(self) -> None:
        text = self._extract(100)

        # Header should not absorb the first body row ("El neumático ...").
        self.assertIn("Presión de inflado menor", text)
        self.assertIn("Presión de inflado mayor", text)
        self.assertIn("| El neumático se calienta,", text)
        self.assertNotIn("Presión de inflado menor a la recomendada El neumático", text)

    def test_layout_extraction_page157_table_header_not_merged(self) -> None:
        text = self._extract(157)

        self.assertIn("| Señales diferentes | ¿Cuál debes obedecer? |", text)
        self.assertNotIn("Señales diferentes Señal de Stop", text)
        self.assertIn("Señal de Stop", text)

    def test_layout_extraction_page292_table_header_not_merged(self) -> None:
        text = self._extract(292)

        self.assertIn("| Situación | ¿Qué hacer? |", text)
        self.assertNotIn("Situación El vehículo", text)
        self.assertIn("El vehículo", text)

    def test_layout_extraction_page345_detects_three_column_table(self) -> None:
        text = self._extract(345)

        # The PDF table has 3 columns; avoid collapsing the middle column.
        self.assertIn("| Tipo de vehículo | ¿Qué ruedas derrapan? | ¿Qué debes hacer? |", text)
        self.assertIn("| --- | --- | --- |", text)


if __name__ == "__main__":
    unittest.main()
