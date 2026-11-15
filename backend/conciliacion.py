"""Conciliación de compras contra la propuesta del RCE (SIRE) y cálculo del IGV del mes.

Todo aquí es determinista y está cubierto por tests/test_conciliacion.py.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass

import pandas as pd

from .parametros import TOLERANCIA_SOLES, tasa, tasa_pago_a_cuenta_rmt

# ---------------------------------------------------------------- normalización


def normalizar_serie(serie_numero: str) -> str:
    """'F001-004512' -> 'F001-4512'; '0001 - 004689' -> '0001-4689'."""
    s = re.sub(r"\s+", "", str(serie_numero or "")).upper()
    m = re.match(r"^([A-Z0-9]{4})-0*(\d+)$", s)
    return f"{m.group(1)}-{int(m.group(2))}" if m else s


def clave(ruc: str, serie_numero: str) -> str:
    return f"{str(ruc or '').strip()}|{normalizar_serie(serie_numero)}"


def _norm_col(c: str) -> str:
    c = unicodedata.normalize("NFKD", str(c)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", c).strip("_")


# Alias para aceptar tanto el CSV simplificado del caso como nombres parecidos a la exportación
# del SIRE. Si tu exportación trae otros nombres, agrégalos aquí.
ALIAS_PROPUESTA = {
    "serie_numero": ["serie_numero", "serie_y_numero", "comprobante"],
    "serie": ["serie", "serie_del_cdp", "serie_cdp"],
    "numero": ["numero", "nro_cp_o_doc_nro_inicial_rango", "nro_cp", "numero_cdp"],
    "ruc_proveedor": ["ruc_proveedor", "nro_doc_identidad", "ruc", "numero_documento_identidad"],
    "razon_social": ["razon_social", "apellidos_nombres_razon_social", "proveedor"],
    "fecha_emision": ["fecha_emision", "fecha_de_emision", "fec_emision"],
    "tipo_cp": ["tipo_cp", "tipo_cp_doc", "tipo_de_comprobante"],
    "base_gravada": ["base_gravada", "bi_gravado_dg", "base_imponible"],
    "igv": ["igv", "igv_ipm_dg", "igv_ipm"],
    "total": ["total", "total_cp", "importe_total"],
}


def cargar_propuesta(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty and len(df.columns) == 0:  # propuesta vacía (p. ej. negocio nuevo)
        return pd.DataFrame(columns=["serie_numero", "ruc_proveedor", "razon_social", "igv", "base_gravada", "total"])
    cols = {_norm_col(c): c for c in df.columns}
    out = pd.DataFrame(index=df.index)
    for destino, alias in ALIAS_PROPUESTA.items():
        for a in alias:
            if a in cols:
                out[destino] = df[cols[a]]
                break
    if "serie_numero" not in out and {"serie", "numero"} <= set(out.columns):
        out["serie_numero"] = out["serie"].astype(str) + "-" + out["numero"].astype(str)
    faltan = {"serie_numero", "ruc_proveedor", "igv"} - set(out.columns)
    if faltan:
        raise ValueError(f"A la propuesta le faltan columnas: {sorted(faltan)}")
    out["ruc_proveedor"] = out["ruc_proveedor"].astype(str).str.replace(r"\.0$", "", regex=True)
    for c in ("base_gravada", "igv", "total"):
        if c in out:
            out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0)
    if "razon_social" not in out:
        out["razon_social"] = ""
    return out.reset_index(drop=True)


# ---------------------------------------------------------------- conciliación

ACCION = {
    "OK": "Coincide con tu comprobante.",
    "FALTA_EN_PROPUESTA": "Agrégala al RCE antes de aceptar: SUNAT no la tiene y pierdes ese crédito.",
    "SOBRA_EN_PROPUESTA": "No tienes este comprobante en tus compras. Si no es del negocio (p. ej. gasto personal), exclúyelo: usar ese crédito es indebido.",
    "DIFERENCIA_MONTO": "El IGV de la propuesta no coincide con tu comprobante: revisa si hay nota de crédito o error de digitación.",
    "DUPLICADO_EN_PROPUESTA": "Aparece dos veces en la propuesta: deja solo una.",
    "RUC_CLIENTE_ERRADO": "La factura está a nombre de otro RUC: no da crédito hasta que el proveedor la anule y emita una nueva.",
    "SIN_CREDITO": "Compra con boleta: no da crédito fiscal. Pide factura a tu RUC la próxima vez.",
}
SEVERIDAD = {
    "OK": 0, "SIN_CREDITO": 1, "DIFERENCIA_MONTO": 2, "DUPLICADO_EN_PROPUESTA": 2,
    "RUC_CLIENTE_ERRADO": 2, "FALTA_EN_PROPUESTA": 3, "SOBRA_EN_PROPUESTA": 3,
}


def conciliar(compras: pd.DataFrame, propuesta: pd.DataFrame, ruc_propio: str,
              tasa_compras: float = 0.18, validados_por_usuario: set[str] | None = None) -> pd.DataFrame:
    """Compara los comprobantes del negocio contra la propuesta del RCE.

    compras: columnas tipo_comprobante, serie_numero, ruc_emisor, razon_social_emisor,
             ruc_cliente, igv, total, contingencia (opcional), incluir (opcional, bool).
    validados_por_usuario: claves de filas de la propuesta que el usuario confirmó como del
             negocio aunque no subió el comprobante.
    """
    validados_por_usuario = validados_por_usuario or set()
    filas: list[dict] = []
    prop = propuesta.copy()
    prop["_clave"] = [clave(r, s) for r, s in zip(prop["ruc_proveedor"], prop["serie_numero"])]
    dup = prop["_clave"].duplicated(keep="first")
    usadas: set[str] = set()

    comp = compras.copy()
    if "incluir" in comp:
        comp = comp[comp["incluir"].fillna(True).astype(bool)]

    for _, c in comp.iterrows():
        k = clave(c.get("ruc_emisor"), c.get("serie_numero"))
        base = {
            "serie_numero": c.get("serie_numero"),
            "ruc_proveedor": c.get("ruc_emisor"),
            "proveedor": c.get("razon_social_emisor"),
            "clave": k,
        }
        igv_c = c.get("igv")
        if c.get("tipo_comprobante") == "Boleta":
            total = float(c.get("total") or 0)
            igv_ref = float(igv_c) if pd.notna(igv_c) and igv_c else round(total * tasa_compras / (1 + tasa_compras), 2)
            filas.append({**base, "estado": "SIN_CREDITO", "igv_comprobante": igv_ref, "igv_propuesta": 0.0,
                          "credito_revisado": 0.0, "credito_propuesta": 0.0})
            continue
        igv_c = float(igv_c or 0)
        ruc_cli = str(c.get("ruc_cliente") or "").strip()
        en_prop = prop[(prop["_clave"] == k) & ~dup]
        if ruc_cli and ruc_cli != ruc_propio:
            estado, cred = "RUC_CLIENTE_ERRADO", 0.0
        elif en_prop.empty:
            estado, cred = "FALTA_EN_PROPUESTA", igv_c
        elif abs(float(en_prop["igv"].iloc[0]) - igv_c) > TOLERANCIA_SOLES:
            estado, cred = "DIFERENCIA_MONTO", igv_c
        else:
            estado, cred = "OK", igv_c
        igv_p = float(en_prop["igv"].iloc[0]) if not en_prop.empty else 0.0
        if not en_prop.empty:
            usadas.add(k)
        nota = " (comprobante físico/contingencia)" if estado == "FALTA_EN_PROPUESTA" and bool(c.get("contingencia")) else ""
        filas.append({**base, "estado": estado, "igv_comprobante": igv_c, "igv_propuesta": igv_p,
                      "credito_revisado": cred, "credito_propuesta": igv_p, "nota": nota})

    for i, p in prop.iterrows():
        k = p["_clave"]
        base = {"serie_numero": p["serie_numero"], "ruc_proveedor": p["ruc_proveedor"],
                "proveedor": p.get("razon_social", ""), "clave": k, "igv_comprobante": 0.0,
                "igv_propuesta": float(p["igv"])}
        if dup.iloc[i]:
            filas.append({**base, "estado": "DUPLICADO_EN_PROPUESTA", "credito_revisado": 0.0,
                          "credito_propuesta": float(p["igv"])})
        elif k not in usadas:
            if k in validados_por_usuario:
                filas.append({**base, "estado": "OK", "credito_revisado": float(p["igv"]),
                              "credito_propuesta": float(p["igv"]), "nota": " (validado por el usuario)"})
            else:
                filas.append({**base, "estado": "SOBRA_EN_PROPUESTA", "credito_revisado": 0.0,
                              "credito_propuesta": float(p["igv"])})

    df = pd.DataFrame(filas)
    if "nota" not in df:
        df["nota"] = ""
    df["nota"] = df["nota"].fillna("")
    df["que_hacer"] = df["estado"].map(ACCION) + df["nota"]
    df["severidad"] = df["estado"].map(SEVERIDAD)
    df["efecto_en_credito"] = (df["credito_revisado"] - df["credito_propuesta"]).round(2)
    return df.sort_values(["severidad", "igv_comprobante"], ascending=[False, False]).reset_index(drop=True)


# ---------------------------------------------------------------- cálculo del IGV


@dataclass
class Resumen:
    periodo: str
    tasa_ventas: float
    base_ventas: float
    igv_debito_ventas: float
    credito_fiscal_segun_propuesta: float
    credito_fiscal_revisado: float
    igv_a_pagar_si_acepta_propuesta: float
    igv_a_pagar_revisado: float
    diferencia: float
    credito_recuperado: float
    credito_indebido_evitado: float
    igv_referencial_perdido_por_boletas: float
    saldo_a_favor_revisado: float
    pago_a_cuenta_renta: float
    tasa_pago_a_cuenta: float

    def a_dict(self) -> dict:
        return {k: (v if k.startswith("tasa") else round(v, 2) if isinstance(v, float) else v)
                for k, v in asdict(self).items()}


def calcular(conciliacion: pd.DataFrame, base_ventas: float, anio: int, periodo: str,
             regimen_ventas: str = "restaurante_mype", ingresos_anuales: float | None = None) -> Resumen:
    t = tasa(anio, regimen_ventas).total
    debito = round(base_ventas * t, 2)
    cred_prop = round(float(conciliacion["credito_propuesta"].sum()), 2)
    cred_rev = round(float(conciliacion["credito_revisado"].sum()), 2)
    pagar_prop = round(max(0.0, debito - cred_prop), 2)
    pagar_rev = round(max(0.0, debito - cred_rev), 2)
    est = conciliacion["estado"]
    recuperado = float(conciliacion.loc[est == "FALTA_EN_PROPUESTA", "credito_revisado"].sum())
    indebido = float(conciliacion.loc[est.isin(["SOBRA_EN_PROPUESTA", "DUPLICADO_EN_PROPUESTA"]), "credito_propuesta"].sum())
    boletas = float(conciliacion.loc[est == "SIN_CREDITO", "igv_comprobante"].sum())
    ingresos = ingresos_anuales if ingresos_anuales is not None else base_ventas * 12
    tpac = tasa_pago_a_cuenta_rmt(ingresos, anio)
    return Resumen(
        periodo=periodo,
        tasa_ventas=t,
        base_ventas=round(base_ventas, 2),
        igv_debito_ventas=debito,
        credito_fiscal_segun_propuesta=cred_prop,
        credito_fiscal_revisado=cred_rev,
        igv_a_pagar_si_acepta_propuesta=pagar_prop,
        igv_a_pagar_revisado=pagar_rev,
        diferencia=round(pagar_prop - pagar_rev, 2),
        credito_recuperado=round(recuperado, 2),
        credito_indebido_evitado=round(indebido, 2),
        igv_referencial_perdido_por_boletas=round(boletas, 2),
        saldo_a_favor_revisado=round(max(0.0, cred_rev - debito), 2),
        pago_a_cuenta_renta=round(base_ventas * tpac, 2),
        tasa_pago_a_cuenta=tpac,
    )


def base_ventas_desde_csv(df: pd.DataFrame) -> float:
    cols = {_norm_col(c): c for c in df.columns}
    for a in ("base_gravada", "bi_gravada", "base_imponible", "valor_venta"):
        if a in cols:
            return float(pd.to_numeric(df[cols[a]], errors="coerce").fillna(0).sum())
    raise ValueError("El CSV de ventas necesita una columna base_gravada")


def rce_ajustado(conciliacion: pd.DataFrame) -> pd.DataFrame:
    """Lista final de compras con crédito para registrar en el RCE (para el contador)."""
    ok = conciliacion[conciliacion["credito_revisado"] > 0]
    filas = []
    for _, r in ok.iterrows():
        filas.append({"serie_numero": r["serie_numero"], "ruc_proveedor": r["ruc_proveedor"],
                      "proveedor": r["proveedor"], "igv": r["credito_revisado"],
                      "origen": "agregado por Cuadra" if r["estado"] == "FALTA_EN_PROPUESTA" else "propuesta SUNAT"})
    return pd.DataFrame(filas)
