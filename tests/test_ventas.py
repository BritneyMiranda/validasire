import pandas as pd

from backend import caso
from backend.conciliacion import cargar_propuesta, conciliar
from backend.ventas import base_revisada, cargar_ventas, conciliar_ventas


def _caso():
    reg = cargar_ventas(pd.read_csv(caso.DIR / "ventas_registro.csv", dtype=str))
    prop = cargar_ventas(pd.read_csv(caso.DIR / "propuesta_rvie.csv", dtype=str))
    return reg, prop


def test_ventas_del_caso_coinciden_y_suman_52000():
    reg, prop = _caso()
    con = conciliar_ventas(reg, prop)
    assert (con["estado"] == "OK").all()
    assert base_revisada(reg) == 52_000.00


def test_ventas_detecta_faltante_sobrante_y_diferencia():
    reg, prop = _caso()
    prop = prop[prop["serie_numero"] != "F001-000311"].copy()          # SUNAT no tiene una factura
    prop.loc[prop["serie_numero"] == "RC-20261005-1", "base_gravada"] += 100  # otro monto
    extra = prop.iloc[[0]].assign(serie_numero="F001-000399", _clave="F001-399", base_gravada=500.0)
    con = conciliar_ventas(reg, pd.concat([prop, extra], ignore_index=True))
    estados = dict(zip(con["serie_numero"], con["estado"]))
    assert estados["F001-000311"] == "FALTA_EN_PROPUESTA"
    assert estados["RC-20261005-1"] == "DIFERENCIA_MONTO"
    assert estados["F001-000399"] == "SOBRA_EN_PROPUESTA"


def test_factura_de_venta_subida_como_compra_se_marca():
    compras = pd.DataFrame([{"tipo_comprobante": "Factura", "serie_numero": "F001-000311", "ruc_emisor": caso.RUC_PROPIO,
                             "razon_social_emisor": caso.RAZON_SOCIAL, "ruc_cliente": "20999999999",
                             "descripcion": "Almuerzo corporativo", "igv": 99.75, "total": 1049.75}])
    prop = cargar_propuesta(pd.DataFrame(columns=["serie_numero", "ruc_proveedor", "igv"]))
    con = conciliar(compras, prop, caso.RUC_PROPIO)
    assert con.loc[0, "estado"] == "ES_VENTA"
    assert con.loc[0, "credito_revisado"] == 0
