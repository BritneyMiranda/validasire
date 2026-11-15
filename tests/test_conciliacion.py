import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import caso  # noqa: E402
from backend.conciliacion import calcular, cargar_propuesta, conciliar, normalizar_serie  # noqa: E402
from backend.parametros import tasa  # noqa: E402

ESPERADO = json.loads((caso.DIR / "resultado_esperado.json").read_text(encoding="utf-8"))


def compras_verdad() -> pd.DataFrame:
    v = pd.read_csv(caso.DIR / "compras_reales.csv", dtype=str)
    return pd.DataFrame({
        "tipo_comprobante": v["tipo_comprobante"],
        "serie_numero": v["serie_numero"],
        "ruc_emisor": v["ruc_proveedor"],
        "razon_social_emisor": v["proveedor"],
        "ruc_cliente": v["ruc_cliente_en_comprobante"],
        "igv": v["igv"].astype(float),
        "total": v["total"].astype(float),
    })


def propuesta() -> pd.DataFrame:
    return cargar_propuesta(pd.read_csv(caso.DIR / "propuesta_rce.csv", dtype=str))


def test_tasas_2026():
    assert tasa(2026, "restaurante_mype").total == pytest.approx(0.105)
    assert tasa(2026, "general").total == pytest.approx(0.18)
    assert tasa(2027, "restaurante_mype").total == pytest.approx(0.15)


def test_normalizar_serie():
    assert normalizar_serie("F001-004512") == "F001-4512"
    assert normalizar_serie(" 0001 - 004689 ") == "0001-4689"


def test_caso_sintetico_cifras():
    con = conciliar(compras_verdad(), propuesta(), caso.RUC_PROPIO)
    r = calcular(con, caso.base_ventas(), 2026, "2026-10")
    assert r.igv_debito_ventas == pytest.approx(ESPERADO["igv_debito_ventas"])
    assert r.credito_fiscal_revisado == pytest.approx(ESPERADO["credito_fiscal_correcto"])
    assert r.igv_a_pagar_revisado == pytest.approx(ESPERADO["igv_a_pagar_correcto"])
    assert r.credito_fiscal_segun_propuesta == pytest.approx(ESPERADO["credito_fiscal_segun_propuesta"])
    assert r.igv_a_pagar_si_acepta_propuesta == pytest.approx(ESPERADO["igv_a_pagar_si_acepta_propuesta"])
    assert r.diferencia == pytest.approx(ESPERADO["diferencia_pago_de_mas"])
    assert r.pago_a_cuenta_renta == pytest.approx(ESPERADO["pago_a_cuenta_renta_1pct"])
    assert r.igv_referencial_perdido_por_boletas == pytest.approx(ESPERADO["igv_referencial_perdido_por_boletas"])


def test_caso_sintetico_discrepancias():
    con = conciliar(compras_verdad(), propuesta(), caso.RUC_PROPIO)
    esperadas = pd.read_csv(caso.DIR / "discrepancias_esperadas.csv", dtype=str)
    mapa = {"FALTA_EN_PROPUESTA": "FALTA_EN_PROPUESTA", "SOBRA_EN_PROPUESTA": "SOBRA_EN_PROPUESTA",
            "ALERTA_SIN_CREDITO": "SIN_CREDITO"}
    no_ok = con[con["estado"] != "OK"]
    assert len(no_ok) == len(esperadas)
    for _, e in esperadas.iterrows():
        fila = con[con["serie_numero"] == e["serie_numero"]]
        assert len(fila) == 1, e["serie_numero"]
        assert fila["estado"].iloc[0] == mapa[e["tipo"]]


def test_usuario_valida_compra_de_la_propuesta():
    p = propuesta()
    k = "20999991155|F001-458"
    con = conciliar(compras_verdad(), p, caso.RUC_PROPIO, validados_por_usuario={k})
    assert (con["estado"] == "SOBRA_EN_PROPUESTA").sum() == 0


def _mini(**cambios):
    base = {"tipo_comprobante": "Factura", "serie_numero": "F001-1", "ruc_emisor": "20111111111",
            "razon_social_emisor": "X", "ruc_cliente": caso.RUC_PROPIO, "igv": 18.0, "total": 118.0}
    base.update(cambios)
    return pd.DataFrame([base])


def _prop(*filas):
    return cargar_propuesta(pd.DataFrame(
        [{"serie_numero": s, "ruc_proveedor": r, "razon_social": "X", "igv": i} for s, r, i in filas]))


def test_duplicado_y_diferencia_de_monto():
    con = conciliar(_mini(igv=20.0), _prop(("F001-0001", "20111111111", 18.0), ("F001-1", "20111111111", 18.0)),
                    caso.RUC_PROPIO)
    assert set(con["estado"]) == {"DIFERENCIA_MONTO", "DUPLICADO_EN_PROPUESTA"}
    assert con["credito_revisado"].sum() == pytest.approx(20.0)


def test_factura_a_otro_ruc_no_da_credito():
    con = conciliar(_mini(ruc_cliente="20999990071"), _prop(), caso.RUC_PROPIO)
    assert con["estado"].iloc[0] == "RUC_CLIENTE_ERRADO"
    assert con["credito_revisado"].sum() == 0


def test_saldo_a_favor_no_da_igv_negativo():
    con = conciliar(_mini(igv=9000.0), _prop(("F001-1", "20111111111", 9000.0)), caso.RUC_PROPIO)
    r = calcular(con, 10000.0, 2026, "2026-10")
    assert r.igv_a_pagar_revisado == 0
    assert r.saldo_a_favor_revisado == pytest.approx(9000.0 - 1050.0)


def test_alias_de_columnas_tipo_sire():
    df = pd.DataFrame([{"Serie del CDP": "F001", "Nro CP o Doc. Nro Inicial (Rango)": "45",
                        "Nro Doc Identidad": "20111111111", "IGV / IPM DG": "18.00", "Total CP": "118"}])
    p = cargar_propuesta(df)
    assert p.loc[0, "serie_numero"] == "F001-45"
    assert p.loc[0, "igv"] == 18.0
