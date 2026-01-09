import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "postprocess"

sys.path.insert(0, str(SCRIPTS_DIR))

from markdown_postprocess import postprocess_markdown  # noqa: E402


class TestPostprocessFixtures(unittest.TestCase):
    def test_fixtures_match_expected(self) -> None:
        in_files = sorted(FIXTURES_DIR.glob("*.in.md"))
        if not in_files:
            self.fail(f"No fixtures found under {FIXTURES_DIR}")

        for in_path in in_files:
            out_path = in_path.with_suffix("").with_suffix(".out.md")
            with self.subTest(fixture=in_path.name):
                src = in_path.read_text(encoding="utf-8")
                expected = out_path.read_text(encoding="utf-8")
                actual = postprocess_markdown(src, style="paragraphs", enable_tables=True)
                self.assertEqual(actual, expected)

                # Guardrail: processing should be stable (no clobbering across passes).
                again = postprocess_markdown(actual, style="paragraphs", enable_tables=True)
                self.assertEqual(again, actual)


if __name__ == "__main__":
    unittest.main()

