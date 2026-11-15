"""Toma las 3 capturas del README con Playwright, usando el Edge o Chrome instalado.

Uso:  streamlit run app/streamlit_app.py   (en otra terminal)
      python scripts/capturas.py http://localhost:8501
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8501"
OUT = Path(__file__).resolve().parents[1] / "docs" / "capturas"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        nav = p.chromium.launch(channel="msedge") if sys.platform == "win32" else p.chromium.launch()
        pag = nav.new_page(viewport={"width": 1400, "height": 900}, device_scale_factor=1)
        pag.goto(f"{URL}/?demo=1")
        pag.get_by_text("IGV a pagar revisado").wait_for(timeout=120_000)
        pag.wait_for_timeout(2500)

        # 1. Comprobantes leídos por el OCR
        pag.get_by_text("1 · Tus comprobantes de compra").scroll_into_view_if_needed()
        pag.screenshot(path=OUT / "1_comprobantes.png")

        # 2. La foto de celular y lo que leyó el OCR
        pag.get_by_text("Ver una imagen y lo que leyó el OCR").click()
        pag.wait_for_timeout(2000)
        pag.get_by_text("Ver una imagen y lo que leyó el OCR").scroll_into_view_if_needed()
        pag.mouse.wheel(0, 250)
        pag.wait_for_timeout(1000)
        pag.screenshot(path=OUT / "2_foto_y_ocr.png")
        pag.get_by_text("Ver una imagen y lo que leyó el OCR").click()
        pag.wait_for_timeout(1000)

        # 3. Resultado de la conciliación
        pag.get_by_text("3 · Resultado de la revisión").scroll_into_view_if_needed()
        pag.mouse.wheel(0, 120)
        pag.wait_for_timeout(1500)
        pag.screenshot(path=OUT / "3_resultado.png")

        # 4. Diagnóstico del negocio
        pag.get_by_text("4 · Diagnóstico del negocio").scroll_into_view_if_needed()
        pag.mouse.wheel(0, 330)
        pag.wait_for_timeout(1500)
        pag.screenshot(path=OUT / "4_diagnostico.png")
        nav.close()
    print("Capturas en", OUT)


if __name__ == "__main__":
    main()
