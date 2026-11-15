import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import caso  # noqa: E402
from backend.extraccion import extraer_con_reglas, validar  # noqa: E402
from backend.modelos import Comprobante  # noqa: E402
from backend.ocr import Caja, ResultadoOCR  # noqa: E402


def caja(texto, x, y, alto=24):
    return Caja(texto=texto, confianza=0.99, x=x, y=y, alto=alto, x0=x - 50, x1=x + 50)


def test_reglas_emparejan_monto_por_fila_aunque_el_orden_cambie():
    # Orden desordenado a propósito, como en una foto girada.
    ocr = ResultadoOCR(cajas=[
        caja("FACTURA", 700, 60), caja("PROVEEDOR DEMO SAC", 200, 60), caja("RUC 20111111111", 700, 100),
        caja("F001-000123", 700, 140), caja("Fecha de emision: 2026-10-03", 200, 220),
        caja("Senor(es): CLIENTE", 200, 260), caja("RUC: 20999990017", 200, 300),
        caja("S/ 18.00", 760, 835), caja("IGV (18%)", 560, 832), caja("S/ 100.00", 760, 790),
        caja("Op. gravada", 560, 791), caja("TOTAL", 560, 880), caja("S/ 118.00", 760, 882),
    ])
    c = extraer_con_reglas(ocr)
    assert (c.serie_numero, c.ruc_emisor, c.ruc_cliente) == ("F001-000123", "20111111111", "20999990017")
    assert (c.base_gravada, c.igv, c.total) == (100.0, 18.0, 118.0)
    assert c.fecha_emision == "2026-10-03"
    assert c.tipo_comprobante == "Factura"


def test_validar_detecta_ruc_de_otro_cliente_y_descuadre():
    c = Comprobante(tipo_comprobante="Factura", serie_numero="F001-1", ruc_emisor="20111111111",
                    ruc_cliente="20999990071", base_gravada=100, igv=18, total=120)
    obs = " ".join(validar(c, "20999990017"))
    assert "otro RUC" in obs and "no cuadra" in obs


def test_ocr_real_sobre_foto_de_celular():
    pytest.importorskip("rapidocr")
    from backend.ocr import leer

    ruta = caso.DIR / "comprobantes" / "C07_0001-004689_foto.jpg"
    c = extraer_con_reglas(leer(ruta.read_bytes()))
    assert c.serie_numero == "0001-004689"
    assert c.contingencia
    assert (c.igv, c.total) == (738.0, 4838.0)
