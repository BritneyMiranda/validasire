import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from backend import caso  # noqa: E402
from backend.conciliacion import calcular, cargar_propuesta, conciliar  # noqa: E402
from backend.diagnostico import categorizar, diagnosticar, tratamiento_igv  # noqa: E402
from backend.parametros import ACTUALIZADO_AL  # noqa: E402

SIN_COMP = pd.DataFrame([
    {"descripcion": "Pescado fresco del terminal pesquero", "monto": 6500.0},
    {"descripcion": "Limón, cebolla y culantro del mercado", "monto": 2800.0},
    {"descripcion": "Hielo en bloque", "monto": 450.0},
    {"descripcion": "Bolsas y táperes del mercado", "monto": 300.0},
])


def _caso():
    v = pd.read_csv(caso.DIR / "compras_reales.csv", dtype=str)
    compras = pd.DataFrame({
        "tipo_comprobante": v["tipo_comprobante"], "serie_numero": v["serie_numero"], "ruc_emisor": v["ruc_proveedor"],
        "razon_social_emisor": v["proveedor"], "ruc_cliente": v["ruc_cliente_en_comprobante"],
        "descripcion": v["descripcion"], "igv": v["igv"].astype(float), "total": v["total"].astype(float),
    })
    prop = cargar_propuesta(pd.read_csv(caso.DIR / "propuesta_rce.csv", dtype=str))
    con = conciliar(compras, prop, caso.RUC_PROPIO)
    res = calcular(con, caso.base_ventas(), 2026, "2026-10")
    return compras, con, res


def test_frescos_exonerados_y_procesados_gravados():
    assert tratamiento_igv("Limón, cebolla y culantro del mercado") == "exonerado (probable)"
    assert tratamiento_igv("Pescado fresco del terminal pesquero") == "exonerado (probable)"
    assert tratamiento_igv("Pulpo cocido y calamar precocido") == "gravado"
    assert tratamiento_igv("Pastas de aji amarillo y rocoto") == "gravado"
    assert categorizar("Hielo en bloque") == "Bebidas y hielo"


def test_diagnostico_caso_rmt():
    compras, con, res = _caso()
    d = diagnosticar(compras, SIN_COMP, con, res, 2026, "RMT")
    # boletas gravadas 180 + 36; hielo 450*18/118; bolsas 300*18/118; frescos 0
    assert d.igv_recuperable_mes == pytest.approx(180 + 36 + 68.64 + 45.76, abs=0.02)
    assert d.impuesto_renta_estimado_mes == pytest.approx((6500 + 2800 + 450 / 1.18 + 300 / 1.18) * 0.10, abs=0.05)
    assert d.impacto_2027_mes == pytest.approx(52000 * (0.15 - 0.105))
    titulos = " ".join(r["titulo"] for r in d.recomendaciones)
    assert "compras personales" in titulos and "no es IGV" in titulos
    assert d.recomendaciones[0]["prioridad"] == 1


def test_excel_tiene_todas_las_hojas():
    import io

    from backend.diagnostico import a_excel

    compras, con, res = _caso()
    d = diagnosticar(compras, SIN_COMP, con, res, 2026, "RMT")
    buf = io.BytesIO()
    a_excel(buf, compras, con, res, d)
    hojas = pd.ExcelFile(io.BytesIO(buf.getvalue())).sheet_names
    assert {"Resumen", "Conciliacion", "Diagnostico_lineas", "Recomendaciones", "Formulas"} <= set(hojas)


def test_rer_no_suma_renta_por_costos():
    compras, con, res = _caso()
    d = diagnosticar(compras, SIN_COMP, con, res, 2026, "RER")
    assert d.impuesto_renta_estimado_mes == 0
    assert d.igv_recuperable_mes > 0


def test_fecha_de_normativa_cargada():
    assert ACTUALIZADO_AL.year >= 2026


def test_parser_monitor_normativa():
    spec = importlib.util.spec_from_file_location("monitor", RAIZ / "scripts" / "monitor_normativa.py")
    mon = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mon)
    html = """<tr><td valign="top" class="tlin"><a href="../000047-2026.pdf" target="_blank">R. Superintendencia N&ordm; 000047-2026</a></td>
      <td valign="top" class="tlin">Regula el pago del IGV e IPM retenidos por operaciones en las que se emite la liquidaci&oacute;n de compra</td>
      <td class="tlin">27/03/2026</td></tr>
      <tr><td valign="top" class="tlin"><a href="../000046-2026.pdf" target="_blank">R. Superintendencia N&ordm; 000046-2026</a></td>
      <td valign="top" class="tlin">Designa al Oficial de Integridad</td><td class="tlin">&nbsp;</td></tr>"""
    normas = mon.parsear_indice(html, 2026)
    assert [n["id"] for n in normas] == ["000047-2026", "000046-2026"]
    assert [n["id"] for n in mon.relevantes(normas)] == ["000047-2026"]
