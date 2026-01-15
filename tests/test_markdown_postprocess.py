import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from markdown_postprocess import postprocess_markdown  # noqa: E402
from markdown_postprocess import fix_marker_speed_tables_252_257  # noqa: E402


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

    def test_postprocess_preserves_markdown_images_as_blocks(self) -> None:
        src = """Intro line wraps
onto the next line.
![](_page_7_Picture_9.jpeg)
Following text continues
on the next line.
"""
        out = postprocess_markdown(src, style="paragraphs", enable_tables=True)
        self.assertIn("\n![](_page_7_Picture_9.jpeg)\n", out)
        self.assertNotIn("Intro line wraps ![](_page_7_Picture_9.jpeg)", out)
        self.assertNotIn("![](_page_7_Picture_9.jpeg) Following text", out)

    def test_postprocess_drops_trailing_empty_table_columns(self) -> None:
        src = """| A | B |  |
| --- | --- | -- |
| left | right |  |
"""
        out = postprocess_markdown(src, style="preserve", enable_tables=True)
        self.assertIn("| A | B |", out)
        self.assertIn("| --- | --- |", out)
        self.assertIn("| left | right |", out)
        self.assertNotIn("| A | B |  |", out)
        self.assertNotIn("| --- | --- | -- |", out)

    def test_postprocess_removes_duplicate_separator_rows(self) -> None:
        src = """| A | B |  |
| --- | --- | -- |
| --- | --- | -- |
| left | right |  |
"""
        out = postprocess_markdown(src, style="preserve", enable_tables=True)
        # Only one separator row should remain.
        self.assertEqual(out.count("| --- | --- |"), 1)
        self.assertIn("| left | right |", out)

    def test_postprocess_fixes_arm_signals_section(self) -> None:
        src = """#### **Señales con el brazo**

# Brazo levantado

![](_page_157_Picture_3.jpeg)

en vertical Se deben detener todos los conductores que se acerquen al agente.

> Cuando esta señal se hace en un cruce pueden seguir circulando los conductores que ya estaban dentro del cruce.

Brazo extendido que se mueve hacia arriba

![](_page_157_Picture_15.jpeg)

y hacia abajo Deben reducir la velocidad del vehículo.

#### **Señales sonoras con un silbato**
"""
        out = postprocess_markdown(src, style="preserve", enable_tables=True)
        self.assertIn("#### **Señales con el brazo**", out)
        self.assertIn("**Brazo levantado en vertical**", out)
        self.assertIn("Se deben detener todos los conductores", out)
        self.assertNotIn("\n# Brazo levantado\n", out)
        self.assertNotIn("en vertical Se deben", out)
        self.assertNotIn("> Cuando esta señal", out)
        self.assertIn("Cuando esta señal se hace en un cruce", out)
        self.assertIn("**Brazo extendido que se mueve hacia arriba y hacia abajo**", out)
        self.assertNotIn("y hacia abajo Deben", out)
        self.assertIn("Deben reducir la velocidad del vehículo.", out)

    def test_postprocess_splits_service_sign_runon_line(self) -> None:
        src = """<!-- Page: 194 -->

**S-114** Puedes parar a comer. **S-115** Desde ese lugar puedes empezar una excursión andando. **S-116** Puedes acampar en ese lugar.
<!-- Page: 195 -->
"""
        out = postprocess_markdown(src, style="preserve", enable_tables=True)
        self.assertIn("**S-114** Puedes parar a comer.", out)
        self.assertIn("**S-115** Desde ese lugar puedes empezar una excursión andando.", out)
        self.assertIn("**S-116** Puedes acampar en ese lugar.", out)
        self.assertIn("**S-114** Puedes parar a comer.\n\n**S-115**", out)
        self.assertIn("**S-115** Desde ese lugar puedes empezar una excursión andando.\n\n**S-116**", out)

    def test_postprocess_does_not_split_service_sign_ranges(self) -> None:
        src = """<!-- Page: 199 -->

**S-820** y **S-821** Estas señales se colocan debajo de una señal de prohibición.
"""
        out = postprocess_markdown(src, style="preserve", enable_tables=True)
        self.assertIn("**S-820** y **S-821** Estas señales", out)
        self.assertNotIn("**S-820**\n\n**S-821**", out)

    def test_fix_marker_speed_tables_252_257_rewrites_tables_and_strips_noise(self) -> None:
        src = """<!-- Page: 252 -->

| Autopista<br>y autovía                     |                                                                              | Carreteras<br>120<br>100<br>120<br>90<br>100                                                                                                      |                                                                                              | Caminos<br>90<br>80<br>70<br>80<br>70<br>50             |                                             |
|--------------------------------------------|------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------|---------------------------------------------------------|---------------------------------------------|
| Velocidad<br>80<br>70<br>máxima            | Velocidad<br>50<br>mínima                                                    | Velocidad<br>45<br>40<br>máxima                                                                                                                   | Velocidad<br>45<br>30<br>120<br>100<br>mínima                                                | Velocidad<br>40<br>30<br>25<br>20<br>90<br>80<br>máxima | Velocidad<br>25<br>20<br>70<br>60<br>mínima |
| 25<br>20120<br>120<br>30<br>25<br>45<br>45 | 100<br>60<br>90<br>100<br>15<br>40<br>45<br>30<br>40<br>35<br>45<br>40<br>40 | 80<br>90<br>70<br>80<br>45<br>40<br>En algunas<br>25<br>30<br>20<br>25<br>carreteras<br>el límite<br>30<br>35<br>25<br>30<br>puede ser<br>de 100. | 45<br>35<br>40<br>50<br>70<br>45<br>50<br>40<br>20<br>45<br>60<br>40<br>60<br>15<br>25<br>15 | 35<br>30<br>30<br>25<br>35<br>30                        | 30<br>25<br>25<br>15<br>20<br>25            |

#### 45 40 15 35 **Tipos de vehículo**

- 40 15 40 15 **■** Vehículos de tres ruedas que no son motocicletas.

<!-- Page: 256 -->

| 100<br>90<br>80 | Calles<br>sin bordillos,<br>donde la calzada<br>y la acera están<br>70<br>50<br>120<br>100<br>a la misma altura | Calles<br>con un solo carril<br>para cada sentido<br>90<br>80<br>70 | Calles<br>con varios carriles<br>en cada sentido<br>50 |
|-----------------|-----------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------|--------------------------------------------------------|
| 40<br>30<br>25  | 20<br>45<br>120<br>40<br>100<br>60                                                                              | 30<br>90<br>25<br>80<br>20<br>70                                    | 50<br>60                                               |
"""

        fixed, tables_rewritten = fix_marker_speed_tables_252_257(src)
        self.assertGreaterEqual(tables_rewritten, 2)

        self.assertIn("| Vía | Velocidad máxima (km/h) | Velocidad mínima (km/h) |", fixed)
        self.assertIn("| Autopista y autovía | 120 | 60 |", fixed)
        self.assertIn("90 (En algunas carreteras el límite puede ser de 100.)", fixed)

        # Numeric noise in headings/list items should be stripped.
        self.assertIn("#### **Tipos de vehículo**", fixed)
        self.assertNotIn("#### 45 40 15 35", fixed)
        self.assertIn("- **■** Vehículos de tres ruedas", fixed)

        # In-town table should be rewritten with clean labels and values.
        self.assertIn("| Tipo de calle | Velocidad máxima (km/h) |", fixed)
        self.assertIn(
            "| Calles sin bordillos, donde la calzada y la acera están a la misma altura | 20 |",
            fixed,
        )
        self.assertIn("| Calles con un solo carril para cada sentido | 30 |", fixed)
        self.assertIn("| Calles con varios carriles en cada sentido | 50 |", fixed)

        # The full postprocessor should also include this fix (preserve mode).
        out = postprocess_markdown(src, style="preserve", enable_tables=True)
        self.assertIn("| Autopista y autovía | 120 | 60 |", out)


if __name__ == "__main__":
    unittest.main()
