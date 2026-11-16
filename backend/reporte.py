"""Textos de salida (deterministas, sin LLM): reporte para el contador y mensaje para el dueño."""
from __future__ import annotations

import pandas as pd

from .conciliacion import Resumen


def _s(x: float) -> str:
    return f"S/ {x:,.2f}"


def reporte_contador(r: Resumen, con: pd.DataFrame, ruc: str, con_ventas: pd.DataFrame | None = None) -> str:
    if con_ventas is None:
        sec_ventas = "No se cargó la propuesta del RVIE: la base de ventas se tomó del resumen ingresado."
    else:
        hv = con_ventas[con_ventas["estado"] != "OK"]
        sec_ventas = (f"{len(con_ventas)} comprobantes/resúmenes cruzados contra la propuesta del RVIE: "
                      + ("todos coinciden." if hv.empty else f"{len(hv)} con diferencias:\n\n" + "\n".join(
                          f"- {h.serie_numero} · {h.estado} · base registro {h.base_registro:,.2f} / propuesta {h.base_propuesta:,.2f}"
                          for h in hv.itertuples())))
    hall = con[con["estado"] != "OK"]
    filas = "\n".join(
        f"| {h.estado} | {h.serie_numero} | {h.proveedor} | {h.igv_comprobante:,.2f} | {h.igv_propuesta:,.2f} | {h.que_hacer} |"
        for h in hall.itertuples()
    ) or "| - | - | - | - | - | Sin hallazgos |"
    return f"""# Revisión de IGV · RUC {ruc} · periodo {r.periodo}

| Concepto | Soles |
|---|---|
| Base de ventas | {r.base_ventas:,.2f} |
| IGV + IPM de ventas ({r.tasa_ventas:.1%}) | {r.igv_debito_ventas:,.2f} |
| Crédito fiscal según propuesta SIRE | {r.credito_fiscal_segun_propuesta:,.2f} |
| Crédito fiscal revisado | {r.credito_fiscal_revisado:,.2f} |
| IGV a pagar si se acepta la propuesta | {r.igv_a_pagar_si_acepta_propuesta:,.2f} |
| **IGV a pagar revisado** | **{r.igv_a_pagar_revisado:,.2f}** |
| Saldo a favor (revisado) | {r.saldo_a_favor_revisado:,.2f} |
| Pago a cuenta de renta RMT ({r.tasa_pago_a_cuenta:.1%}) | {r.pago_a_cuenta_renta:,.2f} |

## Ventas (RVIE)

{sec_ventas}

## Compras (RCE): hallazgos

| Estado | Serie-número | Proveedor | IGV comprobante | IGV propuesta | Acción |
|---|---|---|---|---|---|
{filas}

Generado por ValidaSire. Los cálculos son deterministas (backend/conciliacion.py); verifique antes de presentar.
"""


def mensaje_dueno(r: Resumen, con: pd.DataFrame) -> str:
    partes = [f"Hola, revisé el IGV de {r.periodo}."]
    if r.credito_recuperado:
        partes.append(f"Hay facturas tuyas que SUNAT no tenía (crédito {_s(r.credito_recuperado)}): las agregamos.")
    if r.credito_indebido_evitado:
        partes.append(f"La propuesta incluye compras que no son del negocio ({_s(r.credito_indebido_evitado)} de IGV): las sacamos para evitar una multa.")
    if r.igv_referencial_perdido_por_boletas:
        partes.append(f"Por comprar con boleta dejaste de recuperar {_s(r.igv_referencial_perdido_por_boletas)}. Pide factura a tu RUC.")
    partes.append(f"IGV a pagar: {_s(r.igv_a_pagar_revisado)} (si aceptabas la propuesta tal cual: {_s(r.igv_a_pagar_si_acepta_propuesta)}).")
    partes.append(f"Pago a cuenta de renta: {_s(r.pago_a_cuenta_renta)}.")
    return " ".join(partes)
