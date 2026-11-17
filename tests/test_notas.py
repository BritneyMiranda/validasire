"""Notas de crédito y débito: devoluciones, descuentos y su efecto en compras (RCE) y ventas (RVIE)."""
import json

import pandas as pd
import pytest

from backend import caso
from backend.conciliacion import ESTADOS_ERROR, calcular, cargar_propuesta, conciliar, rce_ajustado
from backend.ventas import base_revisada, cargar_ventas, conciliar_ventas

ESPERADO = json.loads((caso.DIR_NOTAS / "resultado_esperado_notas.json").read_text(encoding="utf-8"))
RUC = caso.RUC_PROPIO


def compras_con_notas() -> pd.DataFrame:
    v = pd.read_csv(caso.DIR / "compras_reales.csv", dtype=str)
    n = pd.read_csv(caso.DIR_NOTAS / "notas_compras.csv", dtype=str)
    todo = pd.concat([v, n], ignore_index=True)
    pagos = caso.pagos_notas()
    return pd.DataFrame({
        "tipo_comprobante": todo["tipo_comprobante"], "serie_numero": todo["serie_numero"],
        "doc_referencia": todo.get("doc_referencia"), "ruc_emisor": todo["ruc_proveedor"],
        "razon_social_emisor": todo["proveedor"], "ruc_cliente": todo["ruc_cliente_en_comprobante"],
        "igv": todo["igv"].astype(float), "total": todo["total"].astype(float),
        "pagado": todo["serie_numero"].map(pagos),
    })


def _caso():
    prop = cargar_propuesta(pd.read_csv(caso.DIR_NOTAS / "propuesta_rce_con_notas.csv", dtype=str))
    con = conciliar(compras_con_notas(), prop, RUC)
    reg = cargar_ventas(pd.read_csv(caso.DIR_NOTAS / "ventas_registro_con_notas.csv", dtype=str))
    rvie = cargar_ventas(pd.read_csv(caso.DIR / "propuesta_rvie.csv", dtype=str))
    return con, reg, rvie


def test_propuesta_pone_notas_de_credito_en_negativo():
    prop = cargar_propuesta(pd.read_csv(caso.DIR_NOTAS / "propuesta_rce_con_notas.csv", dtype=str))
    notas = prop[prop["tipo"] == "Nota de credito"].set_index("serie_numero")
    assert notas.loc["FC01-000087", "igv"] == -90.0   # venía en negativo
    assert notas.loc["FC01-000215", "igv"] == -36.0   # venía en positivo: se corrige el signo
    assert notas.loc["FC01-000215", "doc_referencia"] == "F001-012204"


def test_nota_de_credito_subida_resta_credito_no_suma():
    con, _, _ = _caso()
    nc = con[con["serie_numero"] == "FC01-000087"].iloc[0]
    assert nc["estado"] == "OK"
    assert nc["credito_revisado"] == pytest.approx(-90.0)
    assert "F001-004512" in nc["que_hacer"]
    factura = con[con["serie_numero"] == "F001-004512"].iloc[0]
    assert "S/ 594.00" in factura["que_hacer"]  # 684 - 90


def test_nota_de_sunat_sin_copia_se_queda_y_no_es_error():
    con, _, _ = _caso()
    fila = con[con["serie_numero"] == "FC01-000215"].iloc[0]
    assert fila["estado"] == "NOTA_SIN_COPIA"
    assert fila["credito_revisado"] == pytest.approx(-36.0)
    assert "NOTA_SIN_COPIA" not in ESTADOS_ERROR


def test_descuento_sin_nota_de_credito():
    con, _, _ = _caso()
    fila = con[con["serie_numero"] == "F001-009120"].iloc[0]
    assert fila["estado"] == "DESCUENTO_SIN_NOTA"
    assert fila["igv_descuento"] == pytest.approx(ESPERADO["igv_descuento_sin_nota"])
    assert fila["credito_revisado"] == pytest.approx(405.0)  # el crédito no se toca: se pide la nota


def test_descuento_cubierto_por_nota_no_alerta():
    compras = pd.DataFrame([
        {"tipo_comprobante": "Factura", "serie_numero": "F001-1", "ruc_emisor": "20111111111", "razon_social_emisor": "X",
         "ruc_cliente": RUC, "igv": 180.0, "total": 1180.0, "pagado": 1062.0},
        {"tipo_comprobante": "Nota de credito", "serie_numero": "FC01-1", "doc_referencia": "F001-1",
         "ruc_emisor": "20111111111", "razon_social_emisor": "X", "ruc_cliente": RUC, "igv": 18.0, "total": 118.0},
    ])
    prop = cargar_propuesta(pd.DataFrame([
        {"tipo_cp": "01", "serie_numero": "F001-1", "ruc_proveedor": "20111111111", "igv": 180.0, "total": 1180.0},
        {"tipo_cp": "07", "serie_numero": "FC01-1", "ruc_proveedor": "20111111111", "igv": -18.0, "total": -118.0},
    ]))
    con = conciliar(compras, prop, RUC)
    assert set(con["estado"]) == {"OK"}
    assert con["credito_revisado"].sum() == pytest.approx(162.0)


def test_nota_subida_que_sunat_no_tiene():
    compras = pd.DataFrame([{"tipo_comprobante": "Nota de credito", "serie_numero": "FC01-9", "doc_referencia": "F001-8",
                             "ruc_emisor": "20111111111", "razon_social_emisor": "X", "ruc_cliente": RUC,
                             "igv": 18.0, "total": 118.0}])
    con = conciliar(compras, cargar_propuesta(pd.DataFrame(columns=["serie_numero", "ruc_proveedor", "igv"])), RUC)
    assert con.loc[0, "estado"] == "NC_FALTA_EN_PROPUESTA"
    assert con.loc[0, "credito_revisado"] == pytest.approx(-18.0)
    assert "mes anterior" in con.loc[0, "que_hacer"]
    assert rce_ajustado(con).loc[0, "igv"] == pytest.approx(-18.0)


def test_ventas_nota_de_credito_resta_y_sunat_no_la_tiene():
    _, reg, rvie = _caso()
    assert base_revisada(reg) == pytest.approx(ESPERADO["base_ventas_revisada"])
    cv = conciliar_ventas(reg, rvie)
    nc = cv[cv["serie_numero"] == "FC01-000009"].iloc[0]
    assert nc["estado"] == "FALTA_EN_PROPUESTA"
    assert "nota de crédito" in nc["que_hacer"]
    assert "S/ 1,000.00" in nc["que_hacer"]  # 1,100 - 100


def test_cifras_del_caso_con_notas():
    con, reg, rvie = _caso()
    r = calcular(con, base_revisada(reg), 2026, "2026-10", base_ventas_propuesta=base_revisada(rvie))
    assert r.igv_debito_ventas == pytest.approx(ESPERADO["igv_debito_revisado"])
    assert r.igv_debito_segun_propuesta == pytest.approx(ESPERADO["igv_debito_segun_propuesta"])
    assert r.credito_fiscal_revisado == pytest.approx(ESPERADO["credito_fiscal_revisado"])
    assert r.credito_fiscal_segun_propuesta == pytest.approx(ESPERADO["credito_fiscal_segun_propuesta"])
    assert r.igv_a_pagar_revisado == pytest.approx(ESPERADO["igv_a_pagar_revisado"])
    assert r.igv_a_pagar_si_acepta_propuesta == pytest.approx(ESPERADO["igv_a_pagar_si_acepta_propuesta"])
    assert r.diferencia == pytest.approx(ESPERADO["diferencia_pago_de_mas"])
    assert r.ajuste_notas_credito == pytest.approx(ESPERADO["ajuste_notas_credito_compras"])
    assert r.igv_descuentos_sin_nota == pytest.approx(ESPERADO["igv_descuento_sin_nota"])
