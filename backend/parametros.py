"""Parámetros tributarios. NUNCA se los pedimos al LLM.

Viven en data/normativa/parametros.json, con la fecha de la última revisión y sus fuentes.
Cuando cambia una norma:
1. scripts/monitor_normativa.py (corre cada semana en GitHub Actions) detecta normas nuevas de
   SUNAT relacionadas con IGV, SIRE, comprobantes, etc. y abre un issue para revisarlas.
2. Una persona lee la norma y, si corresponde, edita el JSON (tasas, UIT, fecha) en un PR.
3. Los tests deben pasar antes de desplegar.
Así la app se entera sola de los cambios, pero no cambia un cálculo sin que alguien lo revise.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

RUTA = Path(__file__).resolve().parents[1] / "data" / "normativa" / "parametros.json"
_P = json.loads(RUTA.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Tasa:
    igv: float
    ipm: float

    @property
    def total(self) -> float:
        return round(self.igv + self.ipm, 4)


# año -> régimen de la venta -> tasa
TASAS: dict[int, dict[str, Tasa]] = {
    int(anio): {reg: Tasa(**v) for reg, v in regs.items()} for anio, regs in _P["tasas"].items()
}
UIT: dict[int, float] = {int(k): float(v) for k, v in _P["uit"].items()}

ACTUALIZADO_AL: date = date.fromisoformat(_P["actualizado_al"])
FUENTES: list[dict] = _P["fuentes"]
PENDIENTES: list[str] = _P.get("pendientes", [])

RMT_TOPE_PAGO_1PCT_UIT = _P["rmt_tope_pago_1pct_uit"]
RMT_TOPE_REGIMEN_UIT = _P["rmt_tope_regimen_uit"]
RER_CUOTA = _P["rer_cuota_ingresos"]
TOPE_TASA_ESPECIAL_UIT = _P["tope_tasa_especial_uit"]
MINIMO_INGRESOS_RUBRO = _P["minimo_ingresos_rubro"]
VINCULACION_MAX_CAPITAL = _P["vinculacion_max_capital"]
TOLERANCIA_SOLES = _P["tolerancia_soles"]


def tasa(anio: int, regimen: str) -> Tasa:
    if anio not in TASAS:
        raise ValueError(f"No hay tasas cargadas para {anio}. Agrégalas en data/normativa/parametros.json")
    return TASAS[anio][regimen]


def tasa_pago_a_cuenta(ingresos_anuales: float, anio: int, regimen_renta: str = "RMT") -> float:
    """RMT: 1 % si los ingresos del año no superan 300 UIT (si los superan, coeficiente o 1,5 %;
    aquí simplificado a 1,5 %). RER: cuota de 1,5 % de los ingresos."""
    if regimen_renta == "RER":
        return RER_CUOTA
    return 0.01 if ingresos_anuales <= RMT_TOPE_PAGO_1PCT_UIT * UIT[anio] else 0.015


def dias_desde_revision(hoy: date | None = None) -> int:
    return ((hoy or date.today()) - ACTUALIZADO_AL).days
