"""Conciliación de ventas: lo que emitiste (reporte de tu sistema de facturación o POS) contra la
propuesta del RVIE (Registro de Ventas e Ingresos Electrónico) del SIRE.

Las ventas no se suben como fotos: ya son comprobantes electrónicos que tu sistema envió a SUNAT.
Por eso aquí se cruzan dos archivos. Las boletas se agrupan por resumen diario (RC-AAAAMMDD-n).
Todo es determinista y está cubierto por tests/test_ventas.py.
"""
from __future__ import annotations

import pandas as pd

from .conciliacion import _norm_col, normalizar_serie
from .parametros import TOLERANCIA_SOLES

ALIAS_VENTAS = {
    "serie_numero": ["serie_numero", "comprobante", "resumen", "serie_y_numero"],
    "fecha_emision": ["fecha_emision", "fecha_de_emision", "fecha"],
    "tipo_cp": ["tipo_cp", "tipo_cp_doc", "tipo_de_comprobante"],
    "cliente": ["cliente", "razon_social", "apellidos_nombres_razon_social"],
    "base_gravada": ["base_gravada", "bi_gravada", "base_imponible", "valor_venta"],
    "igv": ["igv", "igv_ipm", "igv_ipm_10_5"],
    "total": ["total", "total_cp", "importe_total"],
}

ESTADOS_VENTA = {
    "OK": "Coincide con tu registro.",
    "FALTA_EN_PROPUESTA": "Tú la emitiste pero SUNAT no la tiene: revisa que tu sistema la haya enviado. Igual debes declararla.",
    "SOBRA_EN_PROPUESTA": "SUNAT la tiene pero no está en tu reporte. Si fue anulada, comunica la baja; si la emitiste, súmala a tu reporte.",
    "DIFERENCIA_MONTO": "El monto de SUNAT no coincide con tu sistema: revisa notas de crédito o descuentos.",
}
ETIQUETA_VENTA = {
    "OK": "✓ Coincide",
    "FALTA_EN_PROPUESTA": "▲ SUNAT no la tiene",
    "SOBRA_EN_PROPUESTA": "▼ No está en tu reporte",
    "DIFERENCIA_MONTO": "≠ SUNAT tiene otro monto",
}


def cargar_ventas(df: pd.DataFrame) -> pd.DataFrame:
    cols = {_norm_col(c): c for c in df.columns}
    out = pd.DataFrame(index=df.index)
    for destino, alias in ALIAS_VENTAS.items():
        for a in alias:
            if a in cols:
                out[destino] = df[cols[a]]
                break
    faltan = {"serie_numero", "base_gravada"} - set(out.columns)
    if faltan:
        raise ValueError(f"Al archivo de ventas le faltan columnas: {sorted(faltan)}")
    for c in ("base_gravada", "igv", "total"):
        if c in out:
            out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0)
    for c in ("cliente", "tipo_cp", "fecha_emision"):
        if c not in out:
            out[c] = ""
    out["_clave"] = out["serie_numero"].map(normalizar_serie)
    return out.reset_index(drop=True)


def conciliar_ventas(registro: pd.DataFrame, propuesta: pd.DataFrame) -> pd.DataFrame:
    """registro y propuesta ya normalizados con cargar_ventas."""
    prop = propuesta.drop_duplicates("_clave").set_index("_clave")
    filas = []
    for _, r in registro.iterrows():
        k = r["_clave"]
        base = {"serie_numero": r["serie_numero"], "fecha_emision": r["fecha_emision"], "cliente": r["cliente"],
                "base_registro": float(r["base_gravada"])}
        if k not in prop.index:
            filas.append({**base, "estado": "FALTA_EN_PROPUESTA", "base_propuesta": 0.0})
        else:
            bp = float(prop.loc[k, "base_gravada"])
            estado = "OK" if abs(bp - float(r["base_gravada"])) <= TOLERANCIA_SOLES else "DIFERENCIA_MONTO"
            filas.append({**base, "estado": estado, "base_propuesta": bp})
    vistos = set(registro["_clave"])
    for k, p in prop.iterrows():
        if k not in vistos:
            filas.append({"serie_numero": p["serie_numero"], "fecha_emision": p["fecha_emision"], "cliente": p["cliente"],
                          "base_registro": 0.0, "estado": "SOBRA_EN_PROPUESTA", "base_propuesta": float(p["base_gravada"])})
    df = pd.DataFrame(filas, columns=["serie_numero", "fecha_emision", "cliente", "base_registro", "estado", "base_propuesta"])
    df["diferencia_base"] = (df["base_registro"] - df["base_propuesta"]).round(2)
    df["que_hacer"] = df["estado"].map(ESTADOS_VENTA)
    orden = {"OK": 0, "DIFERENCIA_MONTO": 1, "FALTA_EN_PROPUESTA": 2, "SOBRA_EN_PROPUESTA": 2}
    return (df.assign(_o=df["estado"].map(orden)).sort_values(["_o", "serie_numero"], ascending=[False, True])
            .drop(columns="_o").reset_index(drop=True))


def base_revisada(registro: pd.DataFrame) -> float:
    """La base de ventas que se declara es la de tus comprobantes emitidos (tu registro)."""
    return round(float(registro["base_gravada"].sum()), 2)
