"""Acceso al caso sintético de demo (Cevichería Mar y Sazón E.I.R.L., octubre 2026). Todo es ficticio."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

DIR = Path(__file__).resolve().parents[1] / "data" / "caso_sintetico"
RUC_PROPIO = "20999990017"
RAZON_SOCIAL = "CEVICHERIA MAR Y SAZON E.I.R.L."
PERIODO = "2026-10"


def archivos_demo() -> list[Path]:
    """Los 15 comprobantes del demo. C07 se usa en su versión 'foto de celular'."""
    archivos = sorted((DIR / "comprobantes").glob("C*.*"))
    return [p for p in archivos if p.name != "C07_0001-004689.png"]


DIR_NOTAS = DIR / "notas"


def archivos_notas() -> list[Path]:
    """Notas de crédito extra del caso 'con devoluciones y descuentos'."""
    return sorted(DIR_NOTAS.glob("C*.png"))


def pagos_notas() -> dict[str, float]:
    """Lo que de verdad se pagó por factura (serie-número -> soles), según el estado de cuenta."""
    df = pd.read_csv(DIR_NOTAS / "pagos.csv", dtype={"serie_numero": str})
    return dict(zip(df["serie_numero"], df["pagado"].astype(float)))


def base_ventas() -> float:
    return float(pd.read_csv(DIR / "ventas_resumen.csv")["base_gravada"].sum())


def extraccion_a_dict(ext) -> dict:
    return {
        "archivo": ext.archivo,
        "comprobante": ext.comprobante.model_dump(),
        "motor": ext.motor,
        "confianza_ocr": round(ext.confianza_ocr, 3),
        "segundos_ocr": round(ext.segundos_ocr, 2),
        "texto_ocr": ext.texto_ocr,
        "observaciones": ext.observaciones,
        "discrepancias_motores": ext.discrepancias_motores,
        "uso_tokens": ext.uso_tokens,
    }


def cache() -> list[dict]:
    p = DIR / "extraccion_cache.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
