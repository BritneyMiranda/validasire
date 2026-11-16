"""Toma las capturas del README con Playwright, usando el Edge o Chrome instalado.

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
    for viejo in OUT.glob("*.png"):
        viejo.unlink()
    with sync_playwright() as p:
        nav = p.chromium.launch(channel="msedge") if sys.platform == "win32" else p.chromium.launch()
        pag = nav.new_page(viewport={"width": 1400, "height": 900}, device_scale_factor=1)
        errores: list[str] = []
        pag.on("console", lambda m: errores.append(m.text) if m.type == "error" else None)
        pag.goto(f"{URL}/?demo=1")
        pag.get_by_text("Lo que te toca pagar").wait_for(timeout=120_000)
        pag.wait_for_timeout(2500)

        def tomar(nombre: str, texto: str, bajar: int = 0) -> None:
            pag.get_by_text(texto, exact=False).first.scroll_into_view_if_needed()
            if bajar:
                pag.mouse.wheel(0, bajar)
            pag.wait_for_timeout(1200)
            pag.screenshot(path=OUT / nombre)

        tomar("1_portada.png", "Revisa tu SIRE antes de aceptar", -400)
        tomar("2_compras.png", "1 · Compras → RCE", 330)
        tomar("3_resultado.png", "3 · Resultado", 20)
        tomar("4_hallazgos.png", "Compras (RCE): qué revisar", 250)
        pag.get_by_role("tab", name="Diagnóstico del negocio").click()
        pag.wait_for_timeout(2000)
        tomar("5_diagnostico.png", "Recomendaciones", -120)
        pag.get_by_role("tab", name="Para tu contador").click()
        pag.wait_for_timeout(2000)
        tomar("6_contador.png", "Expediente completo", -300)
        nav.close()
    print("Capturas en", OUT, "| errores de consola:", errores or "ninguno")


if __name__ == "__main__":
    main()
