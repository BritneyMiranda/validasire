"""Conciliación de ventas: lo que emitiste (reporte de tu sistema de facturación o POS) contra la
propuesta del RVIE (Registro de Ventas e Ingresos Electrónico) del SIRE.

Las ventas no se suben como fotos: ya son comprobantes electrónicos que tu sistema envió a SUNAT.
Por eso aquí se cruzan dos archivos. Las boletas se agrupan por resumen diario (RC-AAAAMMDD-n).

Devoluciones y descuentos: una factura emitida no se corrige; se emite una nota de crédito
(tipo 07, resta) o una nota de débito (tipo 08, suma). Las notas de crédito quedan con base
negativa aunque el archivo las traiga en positivo, y se unen con el comprobante que modifican.
Todo es determinista y está cubierto por tests/test_ventas.py y tests/test_notas.py.
"""
from __future__ import annotations

import pandas as pd

from .conciliacion import NOTA_CREDITO, NOTA_DEBITO, _norm_col, _vacio, normalizar_serie, tipo_normalizado
from .parametros import TOLERANCIA_SOLES

ALIAS_VENTAS = {
    "serie_numero": ["serie_numero", "comprobante", "resumen", "serie_y_numero"],
    "fecha_emision": ["fecha_emision", "fecha_de_emision", "fecha"],
    "tipo_cp": ["tipo_cp", "tipo_cp_doc", "tipo_de_comprobante"],
    "cliente": ["cliente", "razon_social", "apellidos_nombres_razon_social"],
    "doc_referencia": ["doc_referencia", "documento_que_modifica", "serie_numero_modificado", "comprobante_modificado"],
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
ESTADOS_NOTA_VENTA = {
    "FALTA_EN_PROPUESTA": "Emitiste esta nota de crédito (devolución o descuento) pero SUNAT no la tiene: revisa que tu "
                          "sistema la haya enviado. Si aceptas la propuesta sin ella, pagas IGV de más por esa venta.",
    "SOBRA_EN_PROPUESTA": "SUNAT tiene una nota de crédito a tu nombre que no está en tu reporte. Si no la emitiste, "
                          "revísalo con tu proveedor de facturación: baja tus ventas sin que lo sepas.",
}
ETIQUETA_VENTA = {
    "OK": "✓ Coincide",
    "FALTA_EN_PROPUESTA": "▲ SUNAT no la tiene",
    "SOBRA_EN_PROPUESTA": "▼ No está en tu reporte",
    "DIFERENCIA_MONTO": "≠ SUNAT tiene otro monto",
}


def _tipo_venta(tipo, serie) -> str:
    t = tipo_normalizado(tipo)
    s = str(serie or "").upper()
    if (_vacio(tipo) or t == "Factura") and s.startswith(("FC", "BC")):
        return NOTA_CREDITO
    if (_vacio(tipo) or t == "Factura") and s.startswith(("FD", "BD")):
        return NOTA_DEBITO
    if (_vacio(tipo) or t == "Factura") and s.startswith(("RC-", "B")):
        return "Boleta"
    return t


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
    for c in ("cliente", "tipo_cp", "fecha_emision", "doc_referencia"):
        if c not in out:
            out[c] = ""
    out["doc_referencia"] = out["doc_referencia"].map(lambda x: "" if _vacio(x) else str(x).strip())
    out["tipo"] = [_tipo_venta(t, s) for t, s in zip(out["tipo_cp"], out["serie_numero"])]
    es_nc = out["tipo"] == NOTA_CREDITO
    for c in ("base_gravada", "igv", "total"):
        if c in out:
            out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0)
            out.loc[es_nc, c] = -out.loc[es_nc, c].abs()
    out["_clave"] = out["serie_numero"].map(normalizar_serie)
    return out.reset_index(drop=True)


def conciliar_ventas(registro: pd.DataFrame, propuesta: pd.DataFrame) -> pd.DataFrame:
    """registro y propuesta ya normalizados con cargar_ventas."""
    prop = propuesta.drop_duplicates("_clave").set_index("_clave")
    filas = []
    for _, r in registro.iterrows():
        k = r["_clave"]
        base = {"tipo": r["tipo"], "serie_numero": r["serie_numero"], "fecha_emision": r["fecha_emision"],
                "cliente": r["cliente"], "doc_referencia": r["doc_referencia"], "base_registro": float(r["base_gravada"])}
        if k not in prop.index:
            filas.append({**base, "estado": "FALTA_EN_PROPUESTA", "base_propuesta": 0.0})
        else:
            bp = float(prop.loc[k, "base_gravada"])
            estado = "OK" if abs(bp - float(r["base_gravada"])) <= TOLERANCIA_SOLES else "DIFERENCIA_MONTO"
            filas.append({**base, "estado": estado, "base_propuesta": bp})
    vistos = set(registro["_clave"])
    for k, p in prop.iterrows():
        if k not in vistos:
            filas.append({"tipo": p["tipo"], "serie_numero": p["serie_numero"], "fecha_emision": p["fecha_emision"],
                          "cliente": p["cliente"], "doc_referencia": p["doc_referencia"], "base_registro": 0.0,
                          "estado": "SOBRA_EN_PROPUESTA", "base_propuesta": float(p["base_gravada"])})
    columnas = ["tipo", "serie_numero", "fecha_emision", "cliente", "doc_referencia", "base_registro", "estado", "base_propuesta"]
    df = pd.DataFrame(filas, columns=columnas)
    df["diferencia_base"] = (df["base_registro"] - df["base_propuesta"]).round(2)
    df["que_hacer"] = [ESTADOS_NOTA_VENTA.get(e, ESTADOS_VENTA[e]) if t == NOTA_CREDITO else ESTADOS_VENTA[e]
                       for t, e in zip(df["tipo"], df["estado"])]
    # Unir cada nota con el comprobante que modifica.
    claves = dict(zip(df["serie_numero"].map(normalizar_serie), df.index))
    for i, r in df[(df["tipo"].isin([NOTA_CREDITO, NOTA_DEBITO])) & (df["doc_referencia"] != "")].iterrows():
        j = claves.get(normalizar_serie(r["doc_referencia"]))
        if j is None:
            df.at[i, "que_hacer"] += f" Modifica {r['doc_referencia']}, que no está en este periodo."
        else:
            neto = df.at[j, "base_registro"] + r["base_registro"]
            df.at[i, "que_hacer"] += f" Modifica {df.at[j, 'serie_numero']}: la venta neta queda en S/ {neto:,.2f}."
    orden = {"OK": 0, "DIFERENCIA_MONTO": 1, "FALTA_EN_PROPUESTA": 2, "SOBRA_EN_PROPUESTA": 2}
    return (df.assign(_o=df["estado"].map(orden)).sort_values(["_o", "serie_numero"], ascending=[False, True])
            .drop(columns="_o").reset_index(drop=True))


def base_revisada(registro: pd.DataFrame) -> float:
    """La base de ventas que se declara es la de tus comprobantes emitidos (tu registro), neta de notas."""
    return round(float(registro["base_gravada"].sum()), 2)
