"""Parámetros tributarios. NUNCA se los pedimos al LLM: viven aquí, con su fuente.

Las tasas cambian por ley (y hay una reforma tributaria en discusión), así que todo cálculo
lee de este archivo. Si cambia la norma, se cambia una línea y los tests lo cuidan.

Fuentes (revisadas el 15/11/2026; verificar antes de usar en producción):
- Ley 32387 (jun. 2025): el IPM sube 0,5 pp por año desde 2026 (2026: 2,5 %; 2027: 3 %;
  2028: 3,5 %; 2029: 4 %) y el IGV baja en la misma medida, de modo que la tasa general
  sigue en 18 %.
- Ley 31556, modificada por la Ley 32219 (dic. 2024): tasa especial de IGV para MYPE de
  restaurantes, hoteles y alojamientos turísticos: 8 % hasta el 31/12/2026 y 12 % en 2027.
  Requisitos: ingresos anuales de hasta 1,700 UIT y que la actividad del rubro sea la
  principal (según la norma, al menos 70 % de los ingresos).
- UIT 2026: S/ 5,500.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tasa:
    igv: float
    ipm: float

    @property
    def total(self) -> float:
        return round(self.igv + self.ipm, 4)


# año -> régimen de la venta -> tasa
TASAS: dict[int, dict[str, Tasa]] = {
    2026: {"general": Tasa(igv=0.155, ipm=0.025), "restaurante_mype": Tasa(igv=0.08, ipm=0.025)},
    2027: {"general": Tasa(igv=0.15, ipm=0.03), "restaurante_mype": Tasa(igv=0.12, ipm=0.03)},
}

UIT: dict[int, float] = {2026: 5500.0, 2027: 5500.0}  # 2027: placeholder hasta que se publique

# Régimen MYPE Tributario: pago a cuenta de renta de 1 % si los ingresos netos del año no
# superan 300 UIT; si los superan, se usa coeficiente o 1,5 % (simplificado aquí a 1,5 %).
RMT_TOPE_PAGO_1PCT_UIT = 300
RMT_TOPE_REGIMEN_UIT = 1700
TOPE_TASA_ESPECIAL_UIT = 1700
MINIMO_INGRESOS_RUBRO = 0.70
VINCULACION_MAX_CAPITAL = 0.30

TOLERANCIA_SOLES = 0.05  # diferencia máxima para considerar iguales dos montos


def tasa(anio: int, regimen: str) -> Tasa:
    if anio not in TASAS:
        raise ValueError(f"No hay tasas cargadas para {anio}. Agrega el año en backend/parametros.py")
    return TASAS[anio][regimen]


def tasa_pago_a_cuenta_rmt(ingresos_anuales: float, anio: int) -> float:
    return 0.01 if ingresos_anuales <= RMT_TOPE_PAGO_1PCT_UIT * UIT[anio] else 0.015
