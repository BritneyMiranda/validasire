"""Genera las ventas ficticias del caso (octubre 2026): reporte del POS y propuesta del RVIE.

Boletas agrupadas por resumen diario (RC-AAAAMMDD-1) y facturas a empresas. La propuesta del RVIE
coincide con el reporte (en el caso, los errores están en las compras). Total base: S/ 52,000.
Correr: python scripts/generar_ventas.py
"""
from __future__ import annotations

import random
from pathlib import Path

import pandas as pd

DIR = Path(__file__).resolve().parents[1] / "data" / "caso_sintetico"
TASA = 0.105
random.seed(17)

# boletas: 31 días que suman 47,500 (más los fines de semana)
pesos = [1.6 if pd.Timestamp(2026, 10, d).weekday() >= 4 else 1.0 for d in range(1, 32)]
pesos = [p * random.uniform(0.85, 1.15) for p in pesos]
montos = [round(47_500 * p / sum(pesos), 2) for p in pesos]
montos[-1] = round(47_500 - sum(montos[:-1]), 2)
filas = [{"fecha_emision": f"2026-10-{d:02d}", "tipo_cp": "03", "serie_numero": f"RC-202610{d:02d}-1",
          "cliente": "Resumen diario de boletas (B001)", "base_gravada": m} for d, m in zip(range(1, 32), montos)]

facturas = [("2026-10-03", "F001-000311", "EVENTOS CORPORATIVOS DEL SUR SAC", 950.0),
            ("2026-10-09", "F001-000312", "CONSULTORA ANDINA SAC", 620.0),
            ("2026-10-14", "F001-000313", "TRANSPORTES COSTA AZUL SAC", 480.0),
            ("2026-10-17", "F001-000314", "COLEGIO SAN MARCOS SAC", 1100.0),
            ("2026-10-23", "F001-000315", "INMOBILIARIA LOS PINOS SAC", 730.0),
            ("2026-10-29", "F001-000316", "CLINICA DENTAL SONRISA SAC", 620.0)]
filas += [{"fecha_emision": f, "tipo_cp": "01", "serie_numero": s, "cliente": c, "base_gravada": b}
          for f, s, c, b in facturas]

df = pd.DataFrame(filas)
df["igv"] = (df["base_gravada"] * TASA).round(2)
df["total"] = (df["base_gravada"] + df["igv"]).round(2)
assert abs(df["base_gravada"].sum() - 52_000) < 0.01
df.to_csv(DIR / "ventas_registro.csv", index=False)
df.assign(periodo="202610")[["periodo", "fecha_emision", "tipo_cp", "serie_numero", "cliente", "base_gravada", "igv", "total"]] \
    .to_csv(DIR / "propuesta_rvie.csv", index=False)
print(f"{len(df)} filas, base {df['base_gravada'].sum():,.2f}")
