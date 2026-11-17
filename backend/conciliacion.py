"""Conciliación de compras contra la propuesta del RCE (SIRE) y cálculo del IGV del mes.

Notas de crédito y débito: una factura emitida no se modifica; las devoluciones y los descuentos
posteriores se hacen con una nota de crédito (resta) y los cobros adicionales con una nota de
débito (suma). Aquí las notas de crédito restan crédito fiscal, se unen con la factura que
modifican y generan alertas propias.

Todo aquí es determinista y está cubierto por tests/test_conciliacion.py y tests/test_notas.py.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass

import pandas as pd

from .parametros import TOLERANCIA_SOLES, tasa, tasa_pago_a_cuenta

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


def _vacio(x) -> bool:
    return x is None or (isinstance(x, float) and pd.isna(x)) or str(x).strip() in ("", "nan", "None")


# Códigos de tipo de comprobante de SUNAT (tabla 10).
TIPO_POR_CODIGO = {"01": "Factura", "03": "Boleta", "07": "Nota de credito", "08": "Nota de debito"}
NOTA_CREDITO, NOTA_DEBITO = "Nota de credito", "Nota de debito"


def tipo_normalizado(tipo) -> str:
    t = "" if _vacio(tipo) else str(tipo).strip()
    if t.zfill(2) in TIPO_POR_CODIGO:
        return TIPO_POR_CODIGO[t.zfill(2)]
    t_ascii = _norm_col(t)
    if "credito" in t_ascii:
        return NOTA_CREDITO
    if "debito" in t_ascii:
        return NOTA_DEBITO
    if "boleta" in t_ascii:
        return "Boleta"
    return "Factura" if not t or "factura" in t_ascii else t


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
    "doc_referencia": ["doc_referencia", "documento_que_modifica", "serie_numero_modificado", "comprobante_modificado"],
    "serie_mod": ["serie_cdp_modificado", "serie_cp_modificado"],
    "numero_mod": ["nro_cp_modificado", "numero_cp_modificado"],
    "base_gravada": ["base_gravada", "bi_gravado_dg", "base_imponible"],
    "igv": ["igv", "igv_ipm_dg", "igv_ipm"],
    "total": ["total", "total_cp", "importe_total"],
}


def cargar_propuesta(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza la propuesta. Las notas de crédito quedan con montos negativos."""
    if df.empty and len(df.columns) == 0:  # propuesta vacía (p. ej. negocio nuevo)
        return pd.DataFrame(columns=["serie_numero", "ruc_proveedor", "razon_social", "igv", "base_gravada", "total",
                                     "tipo", "doc_referencia"])
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
    out["tipo"] = out["tipo_cp"].map(tipo_normalizado) if "tipo_cp" in out else "Factura"
    if "doc_referencia" not in out:
        if {"serie_mod", "numero_mod"} <= set(out.columns):
            out["doc_referencia"] = [f"{s}-{n}" if not _vacio(s) else "" for s, n in zip(out["serie_mod"], out["numero_mod"])]
        else:
            out["doc_referencia"] = ""
    out["doc_referencia"] = out["doc_referencia"].map(lambda x: "" if _vacio(x) else str(x).strip())
    es_nc = out["tipo"] == NOTA_CREDITO
    for c in ("base_gravada", "igv", "total"):
        if c in out:
            out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0)
            out.loc[es_nc, c] = -out.loc[es_nc, c].abs()
    if "razon_social" not in out:
        out["razon_social"] = ""
    return out.drop(columns=["serie_mod", "numero_mod"], errors="ignore").reset_index(drop=True)


# ---------------------------------------------------------------- conciliación

ACCION = {
    "OK": "Coincide con tu comprobante.",
    "FALTA_EN_PROPUESTA": "Agrégala al RCE antes de aceptar: SUNAT no la tiene y pierdes ese crédito.",
    "SOBRA_EN_PROPUESTA": "No subiste este comprobante. Si es una compra del negocio, sube su foto y la validamos; "
                          "si no lo es (p. ej. un gasto personal), no la aceptes: descontar ese IGV es indebido.",
    "DIFERENCIA_MONTO": "El IGV de la propuesta no coincide con tu comprobante: revisa si hay una nota de crédito "
                        "(devolución o descuento) o un error de digitación.",
    "DUPLICADO_EN_PROPUESTA": "Aparece dos veces en la propuesta: deja solo una.",
    "RUC_CLIENTE_ERRADO": "La factura está a nombre de otro RUC: no da crédito hasta que el proveedor la anule y emita una nueva.",
    "SIN_CREDITO": "Compra con boleta: no da crédito fiscal. Pide factura a tu RUC la próxima vez.",
    "ES_VENTA": "Esta factura la emitiste tú: es una venta, no una compra. Va en el RVIE (ventas), no en el RCE.",
    "NC_FALTA_EN_PROPUESTA": "Subiste una nota de crédito (devolución o descuento) que SUNAT no tiene. Igual debes "
                             "registrarla: tu crédito baja. Si no la registras, te descuentas IGV de más.",
    "NOTA_SIN_COPIA": "SUNAT tiene una nota de crédito de este proveedor (devolución o descuento) que no subiste. "
                      "Es válida y se queda en tu registro: tu crédito baja. Pide la copia al proveedor y guárdala.",
    "DESCUENTO_SIN_NOTA": "Pagaste menos que el total de la factura y no hay nota de crédito. Pídele al proveedor que "
                          "la emita: sin ella te estás descontando IGV de más.",
}
SEVERIDAD = {
    "OK": 0, "SIN_CREDITO": 1, "NOTA_SIN_COPIA": 1, "DIFERENCIA_MONTO": 2, "DUPLICADO_EN_PROPUESTA": 2,
    "RUC_CLIENTE_ERRADO": 2, "ES_VENTA": 2, "DESCUENTO_SIN_NOTA": 2,
    "FALTA_EN_PROPUESTA": 3, "SOBRA_EN_PROPUESTA": 3, "NC_FALTA_EN_PROPUESTA": 3,
}
# Estados que significan que la propuesta del RCE no está lista para aceptarse tal cual.
ESTADOS_ERROR = {"FALTA_EN_PROPUESTA", "SOBRA_EN_PROPUESTA", "DIFERENCIA_MONTO", "DUPLICADO_EN_PROPUESTA",
                 "RUC_CLIENTE_ERRADO", "ES_VENTA", "NC_FALTA_EN_PROPUESTA", "DESCUENTO_SIN_NOTA"}


def _num(x, defecto: float = 0.0) -> float:
    return defecto if _vacio(x) else float(x)


def conciliar(compras: pd.DataFrame, propuesta: pd.DataFrame, ruc_propio: str,
              tasa_compras: float = 0.18, validados_por_usuario: set[str] | None = None) -> pd.DataFrame:
    """Compara los comprobantes del negocio contra la propuesta del RCE.

    compras: columnas tipo_comprobante, serie_numero, ruc_emisor, razon_social_emisor, ruc_cliente,
             igv, total; opcionales: descripcion, contingencia, incluir (bool), doc_referencia
             (notas) y pagado (lo que de verdad se pagó, para detectar descuentos sin nota).
    validados_por_usuario: claves de filas de la propuesta que el usuario confirmó como del
             negocio aunque no subió el comprobante.
    """
    validados_por_usuario = validados_por_usuario or set()
    filas: list[dict] = []
    prop = propuesta.copy()
    if "tipo" not in prop:
        prop["tipo"] = "Factura"
    if "doc_referencia" not in prop:
        prop["doc_referencia"] = ""
    prop["_clave"] = [clave(r, s) for r, s in zip(prop["ruc_proveedor"], prop["serie_numero"])]
    dup = prop["_clave"].duplicated(keep="first")
    usadas: set[str] = set()

    comp = compras.copy()
    if "incluir" in comp:
        comp = comp[comp["incluir"].fillna(True).astype(bool)]

    for _, c in comp.iterrows():
        tipo = tipo_normalizado(c.get("tipo_comprobante"))
        signo = -1 if tipo == NOTA_CREDITO else 1
        ref = c.get("doc_referencia")
        k = clave(c.get("ruc_emisor"), c.get("serie_numero"))
        base = {
            "tipo": tipo,
            "serie_numero": c.get("serie_numero"),
            "ruc_proveedor": c.get("ruc_emisor"),
            "proveedor": c.get("razon_social_emisor"),
            "descripcion": "" if _vacio(c.get("descripcion")) else c.get("descripcion"),
            "doc_referencia": "" if _vacio(ref) else str(ref),
            "total_comprobante": signo * abs(_num(c.get("total"))),
            "pagado": _num(c.get("pagado"), float("nan")),
            "clave": k,
        }
        igv_c = c.get("igv")
        if str(c.get("ruc_emisor") or "").strip() == ruc_propio:
            filas.append({**base, "estado": "ES_VENTA", "igv_comprobante": _num(igv_c), "igv_propuesta": 0.0,
                          "credito_revisado": 0.0, "credito_propuesta": 0.0})
            continue
        if tipo == "Boleta":
            total = _num(c.get("total"))
            igv_ref = _num(igv_c) if _num(igv_c) else round(total * tasa_compras / (1 + tasa_compras), 2)
            filas.append({**base, "estado": "SIN_CREDITO", "igv_comprobante": igv_ref, "igv_propuesta": 0.0,
                          "credito_revisado": 0.0, "credito_propuesta": 0.0})
            continue
        igv_c = signo * abs(_num(igv_c))
        ruc_cli = str(c.get("ruc_cliente") or "").strip()
        en_prop = prop[(prop["_clave"] == k) & ~dup]
        if ruc_cli and ruc_cli not in ("nan", "None") and ruc_cli != ruc_propio:
            estado, cred = "RUC_CLIENTE_ERRADO", 0.0
        elif en_prop.empty:
            estado, cred = ("NC_FALTA_EN_PROPUESTA" if tipo == NOTA_CREDITO else "FALTA_EN_PROPUESTA"), igv_c
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
        base = {"tipo": p["tipo"], "serie_numero": p["serie_numero"], "ruc_proveedor": p["ruc_proveedor"],
                "proveedor": p.get("razon_social", ""), "descripcion": "", "doc_referencia": p["doc_referencia"],
                "total_comprobante": float(p["total"]) if "total" in p else 0.0, "pagado": float("nan"),
                "clave": k, "igv_comprobante": 0.0, "igv_propuesta": float(p["igv"])}
        if dup.iloc[i]:
            filas.append({**base, "estado": "DUPLICADO_EN_PROPUESTA", "credito_revisado": 0.0,
                          "credito_propuesta": float(p["igv"])})
        elif k not in usadas:
            if p["tipo"] == NOTA_CREDITO:
                # La nota la emitió el proveedor electrónicamente: es válida aunque no tengas la copia.
                filas.append({**base, "estado": "NOTA_SIN_COPIA", "credito_revisado": float(p["igv"]),
                              "credito_propuesta": float(p["igv"])})
            elif k in validados_por_usuario:
                filas.append({**base, "estado": "OK", "credito_revisado": float(p["igv"]),
                              "credito_propuesta": float(p["igv"]), "nota": " (validado por el usuario)"})
            else:
                filas.append({**base, "estado": "SOBRA_EN_PROPUESTA", "credito_revisado": 0.0,
                              "credito_propuesta": float(p["igv"])})

    df = pd.DataFrame(filas)
    if df.empty:
        return df
    if "nota" not in df:
        df["nota"] = ""
    df["nota"] = df["nota"].fillna("")
    df["igv_descuento"] = 0.0
    _unir_notas(df)
    _descuentos_sin_nota(df)
    df["que_hacer"] = df["estado"].map(ACCION) + df["nota"]
    df["severidad"] = df["estado"].map(SEVERIDAD)
    df["efecto_en_credito"] = (df["credito_revisado"] - df["credito_propuesta"]).round(2)
    return df.sort_values(["severidad", "igv_comprobante"], ascending=[False, False]).reset_index(drop=True)


def _original(df: pd.DataFrame, ruc: str, ref: str):
    """Índice de la factura que modifica una nota (mismo proveedor, misma serie-número)."""
    k = clave(ruc, ref)
    m = df[(df["clave"] == k) & ~df["tipo"].isin([NOTA_CREDITO, NOTA_DEBITO])]
    return m.index[0] if not m.empty else None


def _unir_notas(df: pd.DataFrame) -> None:
    """Agrega a cada nota (y a su factura) el IGV neto que queda después de la devolución o descuento."""
    notas = df[df["tipo"].isin([NOTA_CREDITO, NOTA_DEBITO]) & (df["doc_referencia"] != "")]
    netos: dict[int, float] = {}
    for i, n in notas.iterrows():
        j = _original(df, n["ruc_proveedor"], n["doc_referencia"])
        if j is None:
            df.at[i, "nota"] += f" Modifica {n['doc_referencia']}, que no está en este periodo (puede ser de un mes anterior)."
            continue
        netos.setdefault(j, float(df.at[j, "credito_revisado"]))
        netos[j] += float(n["credito_revisado"])
        df.at[i, "nota"] += f" Modifica {df.at[j, 'serie_numero']}."
    for j, neto in netos.items():
        df.at[j, "nota"] += f" Tiene notas de crédito/débito: el IGV neto de esta compra es S/ {neto:,.2f}."
        for i in notas.index:
            if _original(df, df.at[i, "ruc_proveedor"], df.at[i, "doc_referencia"]) == j:
                df.at[i, "nota"] = df.at[i, "nota"].rstrip(".") + f" (IGV neto de esa compra: S/ {neto:,.2f})."


def _descuentos_sin_nota(df: pd.DataFrame) -> None:
    """Facturas pagadas por menos de su total sin una nota de crédito que lo explique."""
    for j, f in df[(df["tipo"] == "Factura") & df["pagado"].notna()].iterrows():
        if f["estado"] in ("SIN_CREDITO", "RUC_CLIENTE_ERRADO", "ES_VENTA") or f["total_comprobante"] <= 0:
            continue
        notas = df[(df["tipo"] == NOTA_CREDITO) & (df["doc_referencia"] != "")]
        cubierto = sum(abs(float(n["total_comprobante"])) for i, n in notas.iterrows()
                       if _original(df, n["ruc_proveedor"], n["doc_referencia"]) == j)
        falta = round(float(f["total_comprobante"]) - cubierto - float(f["pagado"]), 2)
        if falta > TOLERANCIA_SOLES:
            igv_desc = round(falta * float(f["igv_comprobante"]) / float(f["total_comprobante"]), 2)
            df.at[j, "igv_descuento"] = igv_desc
            df.at[j, "nota"] += (f" Diferencia sin nota: S/ {falta:,.2f}, de los que S/ {igv_desc:,.2f} son IGV.")
            if f["estado"] == "OK":
                df.at[j, "estado"] = "DESCUENTO_SIN_NOTA"


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
    base_ventas_propuesta: float = 0.0
    igv_debito_segun_propuesta: float = 0.0
    ajuste_notas_credito: float = 0.0
    igv_descuentos_sin_nota: float = 0.0

    def a_dict(self) -> dict:
        return {k: (v if k.startswith("tasa") else round(v, 2) if isinstance(v, float) else v)
                for k, v in asdict(self).items()}


def calcular(conciliacion: pd.DataFrame, base_ventas: float, anio: int, periodo: str,
             regimen_ventas: str = "restaurante_mype", ingresos_anuales: float | None = None,
             regimen_renta: str = "RMT", base_ventas_propuesta: float | None = None) -> Resumen:
    """base_ventas: la de tus comprobantes emitidos (revisada). base_ventas_propuesta: la del RVIE,
    si se cargó; si no, se asume igual a base_ventas."""
    t = tasa(anio, regimen_ventas).total
    debito = round(base_ventas * t, 2)
    bvp = base_ventas if base_ventas_propuesta is None else base_ventas_propuesta
    debito_prop = round(bvp * t, 2)
    cred_prop = round(float(conciliacion["credito_propuesta"].sum()), 2)
    cred_rev = round(float(conciliacion["credito_revisado"].sum()), 2)
    pagar_prop = round(max(0.0, debito_prop - cred_prop), 2)
    pagar_rev = round(max(0.0, debito - cred_rev), 2)
    est = conciliacion["estado"]
    recuperado = float(conciliacion.loc[est == "FALTA_EN_PROPUESTA", "credito_revisado"].sum())
    indebido = float(conciliacion.loc[est.isin(["SOBRA_EN_PROPUESTA", "DUPLICADO_EN_PROPUESTA"]), "credito_propuesta"].sum())
    boletas = float(conciliacion.loc[est == "SIN_CREDITO", "igv_comprobante"].sum())
    tipos = conciliacion["tipo"] if "tipo" in conciliacion else pd.Series("Factura", index=conciliacion.index)
    notas = float(conciliacion.loc[tipos == NOTA_CREDITO, "credito_revisado"].sum())
    desc = float(conciliacion["igv_descuento"].sum()) if "igv_descuento" in conciliacion else 0.0
    ingresos = ingresos_anuales if ingresos_anuales is not None else base_ventas * 12
    tpac = tasa_pago_a_cuenta(ingresos, anio, regimen_renta)
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
        base_ventas_propuesta=round(bvp, 2),
        igv_debito_segun_propuesta=debito_prop,
        ajuste_notas_credito=round(notas, 2),
        igv_descuentos_sin_nota=round(desc, 2),
    )


def base_ventas_desde_csv(df: pd.DataFrame) -> float:
    cols = {_norm_col(c): c for c in df.columns}
    for a in ("base_gravada", "bi_gravada", "base_imponible", "valor_venta"):
        if a in cols:
            return float(pd.to_numeric(df[cols[a]], errors="coerce").fillna(0).sum())
    raise ValueError("El CSV de ventas necesita una columna base_gravada")


def rce_ajustado(conciliacion: pd.DataFrame) -> pd.DataFrame:
    """Lista final de compras con crédito (y notas que lo restan) para registrar en el RCE."""
    ok = conciliacion[conciliacion["credito_revisado"].abs() > 0]
    filas = []
    for _, r in ok.iterrows():
        filas.append({"tipo": r.get("tipo", "Factura"), "serie_numero": r["serie_numero"],
                      "ruc_proveedor": r["ruc_proveedor"], "proveedor": r["proveedor"],
                      "doc_referencia": r.get("doc_referencia", ""), "igv": r["credito_revisado"],
                      "origen": "agregado por ValidaSire" if r["estado"] in ("FALTA_EN_PROPUESTA", "NC_FALTA_EN_PROPUESTA")
                      else "propuesta SUNAT"})
    return pd.DataFrame(filas)
