import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from markdown_postprocess import postprocess_markdown  # noqa: E402


class TestMarkdownPostprocess(unittest.TestCase):
    def test_postprocess_is_idempotent(self) -> None:
        src = """# Demo

<!-- Page: 8 -->

Light trailer
Heavy trailer

| Category | Use |
| --- | --- |
| M | Motor vehicles made to carry people. |
| O | Trailers. |

- First bullet
- Second bullet

```txt
do not touch this
```
"""
        once = postprocess_markdown(src, style="paragraphs", enable_tables=True)
        twice = postprocess_markdown(once, style="paragraphs", enable_tables=True)
        self.assertEqual(once, twice)

    def test_postprocess_preserves_markdown_tables(self) -> None:
        src = """Intro line.

| A | B |
| --- | --- |
| left | right |

Outro line.
"""
        out = postprocess_markdown(src, style="paragraphs", enable_tables=True)
        self.assertIn("| A | B |", out)
        self.assertIn("| --- | --- |", out)
        self.assertIn("| left | right |", out)


if __name__ == "__main__":
    unittest.main()

