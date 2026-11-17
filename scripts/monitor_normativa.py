"""Monitor de normativa (scraping, sesión 03): avisa cuando SUNAT publica algo que puede cambiar ValidaSIRE.

Lee el índice oficial de Resoluciones de Superintendencia del año, compara contra las normas ya
vistas (data/normativa/normas_vistas.json) y filtra las nuevas por palabras clave (IGV, IPM,
SIRE, comprobantes, liquidación de compra, restaurantes, UIT...). No cambia ninguna tasa: genera
un reporte para que una persona lo revise y actualice data/normativa/parametros.json.

Uso:
  python scripts/monitor_normativa.py              # muestra normas nuevas relevantes
  python scripts/monitor_normativa.py --guardar    # además las marca como vistas
  python scripts/monitor_normativa.py --reporte docs/normativa_pendiente.md
En GitHub Actions corre cada lunes y abre un issue si hay algo nuevo
(.github/workflows/monitor_normativa.yml).
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
VISTAS = RAIZ / "data" / "normativa" / "normas_vistas.json"
INDICE = "https://www.sunat.gob.pe/legislacion/superin/{anio}/indices/indcor.htm"
BASE_PDF = "https://www.sunat.gob.pe/legislacion/superin/{anio}/"

PALABRAS = re.compile(
    r"\bIGV\b|\bIPM\b|impuesto general a las ventas|promoci[oó]n municipal|\bSIRE\b|registro de (compras|ventas)|"
    r"\bRCE\b|\bRVIE\b|liquidaci[oó]n de compra|comprobantes? de pago|emisi[oó]n electr[oó]nica|contingencia|"
    r"restaurante|r[eé]gimen mype|r[eé]gimen especial|nuevo rus|\bUIT\b|detracci|bancariz|cr[eé]dito fiscal",
    re.I,
)
RE_FILA = re.compile(
    r'<a href="\.\./([\w-]+)\.pdf"[^>]*>\s*(R\.\s*Superintendencia[^<]*)</a>\s*</td>\s*'
    r'<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>',
    re.S | re.I,
)


def _limpiar(t: str) -> str:
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", html.unescape(t)).strip(" -")


def descargar(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ValidaSIRE monitor de normativa)"})
    datos = urllib.request.urlopen(req, timeout=30).read()
    try:
        return datos.decode("utf-8")
    except UnicodeDecodeError:
        return datos.decode("latin-1", "ignore")


def parsear_indice(contenido: str, anio: int) -> list[dict]:
    normas = []
    for archivo, nombre, sumilla, fecha in RE_FILA.findall(contenido):
        normas.append({
            "id": archivo,
            "nombre": _limpiar(nombre),
            "sumilla": _limpiar(sumilla),
            "fecha": _limpiar(fecha) or None,
            "url": BASE_PDF.format(anio=anio) + archivo + ".pdf",
        })
    return normas


def relevantes(normas: list[dict]) -> list[dict]:
    return [n for n in normas if PALABRAS.search(n["sumilla"])]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--anio", type=int, default=date.today().year)
    ap.add_argument("--guardar", action="store_true", help="marca como vistas las normas actuales")
    ap.add_argument("--reporte", type=Path, help="escribe un reporte en Markdown si hay novedades")
    args = ap.parse_args()

    normas = parsear_indice(descargar(INDICE.format(anio=args.anio)), args.anio)
    if not normas:
        print("No se pudo leer el índice (¿cambió el formato de la página?).")
        return 2
    vistas = set(json.loads(VISTAS.read_text(encoding="utf-8"))["ids"]) if VISTAS.exists() else set()
    nuevas = [n for n in relevantes(normas) if n["id"] not in vistas]

    print(f"{len(normas)} normas en el índice {args.anio}; {len(nuevas)} nuevas y relevantes para ValidaSIRE.")
    for n in nuevas:
        print(f"- {n['nombre']} ({n['fecha'] or 's/f'}): {n['sumilla'][:160]}")

    if nuevas and args.reporte:
        lineas = [f"# Normas de SUNAT por revisar ({date.today():%d/%m/%Y})", "",
                  "Detectadas por `scripts/monitor_normativa.py`. Revisa cada una y, si cambia una tasa, un tope o un "
                  "procedimiento, actualiza `data/normativa/parametros.json` (incluida la fecha `actualizado_al`).", ""]
        lineas += [f"- [ ] [{n['nombre']}]({n['url']}) ({n['fecha'] or 's/f'}): {n['sumilla']}" for n in nuevas]
        args.reporte.write_text("\n".join(lineas) + "\n", encoding="utf-8")
        print("Reporte:", args.reporte)

    if args.guardar:
        VISTAS.parent.mkdir(parents=True, exist_ok=True)
        VISTAS.write_text(json.dumps({"revisado_al": date.today().isoformat(),
                                      "ids": sorted(vistas | {n["id"] for n in normas})}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
