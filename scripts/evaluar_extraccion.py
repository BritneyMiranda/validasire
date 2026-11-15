"""Evalúa la extracción (OCR + reglas, y Claude si hay API key) contra la verdad terreno.

Uso:  python scripts/evaluar_extraccion.py
Escribe docs/evaluacion_extraccion.md y data/caso_sintetico/extraccion_cache.json
(el caché lo usa el "modo rápido" del demo para no esperar el OCR en vivo).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from backend import caso  # noqa: E402
from backend.conciliacion import calcular, cargar_propuesta, conciliar  # noqa: E402
from backend.extraccion import cliente_claude, extraer, modelo_claude  # noqa: E402
from backend.ocr import leer  # noqa: E402

CAMPOS = ["tipo_comprobante", "serie_numero", "fecha_emision", "ruc_emisor", "ruc_cliente", "base_gravada", "igv", "total"]


def main() -> None:
    verdad = pd.read_csv(caso.DIR / "compras_reales.csv", dtype={"ruc_proveedor": str, "ruc_cliente_en_comprobante": str})
    verdad = verdad.set_index("id")
    client = cliente_claude()
    filas, cache = [], []
    for ruta in caso.archivos_demo():
        ext = extraer(ruta.name, leer(ruta.read_bytes()), caso.RUC_PROPIO, client, modelo_claude())
        cache.append(caso.extraccion_a_dict(ext))
        v = verdad.loc[ruta.name[:3]]
        esperado = {
            "tipo_comprobante": v["tipo_comprobante"],
            "serie_numero": v["serie_numero"],
            "fecha_emision": v["fecha"],
            "ruc_emisor": v["ruc_proveedor"],
            "ruc_cliente": v["ruc_cliente_en_comprobante"] if isinstance(v["ruc_cliente_en_comprobante"], str) else None,
            "base_gravada": v["base"] if v["tipo_comprobante"] == "Factura" else None,
            "igv": v["igv"] if v["tipo_comprobante"] == "Factura" else None,
            "total": v["total"],
        }
        obtenido = ext.comprobante.model_dump()
        for campo in CAMPOS:
            e, o = esperado[campo], obtenido[campo]
            ok = (abs(float(e) - float(o)) < 0.01) if isinstance(e, float) and o is not None else (e == o)
            filas.append({"archivo": ruta.name, "campo": campo, "esperado": e, "obtenido": o, "ok": ok})
        print(f"{ruta.name:28s} ocr={ext.confianza_ocr:.2f} {ext.segundos_ocr:.1f}s motor={ext.motor} obs={ext.observaciones}")

    df = pd.DataFrame(filas)
    por_campo = df.groupby("campo", sort=False)["ok"].mean().mul(100).round(1)
    errores = df[~df["ok"]]

    (caso.DIR / "extraccion_cache.json").write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    compras = pd.DataFrame([c["comprobante"] for c in cache])
    prop = cargar_propuesta(pd.read_csv(caso.DIR / "propuesta_rce.csv", dtype=str))
    con = conciliar(compras, prop, caso.RUC_PROPIO)
    res = calcular(con, caso.base_ventas(), 2026, "2026-10").a_dict()
    esperado = json.loads((caso.DIR / "resultado_esperado.json").read_text(encoding="utf-8"))

    lineas = [
        "# Evaluación de la extracción",
        "",
        f"Generado con `python scripts/evaluar_extraccion.py`. Motor: **{'Claude ' + modelo_claude() + ' + reglas' if client else 'OCR PP-OCR (RapidOCR) + reglas, sin LLM'}**.",
        f"Comprobantes: {len(cache)} (13 facturas, una de ellas foto de celular girada, y 2 boletas).",
        "",
        "## Exactitud por campo",
        "",
        "| Campo | % exacto |",
        "|---|---|",
        *[f"| {c} | {p} % |" for c, p in por_campo.items()],
        "",
        f"**Exactitud global: {df['ok'].mean() * 100:.1f} %** ({int(df['ok'].sum())} de {len(df)} campos).",
        "",
        "## Errores",
        "",
        "Ninguno." if errores.empty else errores.to_markdown(index=False),
        "",
        "## Cifras finales contra `resultado_esperado.json`",
        "",
        "| Concepto | Esperado | Obtenido |",
        "|---|---|---|",
    ]
    for k in ("igv_debito_ventas", "credito_fiscal_revisado", "igv_a_pagar_revisado", "credito_fiscal_segun_propuesta",
              "igv_a_pagar_si_acepta_propuesta", "diferencia", "pago_a_cuenta_renta"):
        k_esp = {"credito_fiscal_revisado": "credito_fiscal_correcto", "igv_a_pagar_revisado": "igv_a_pagar_correcto",
                 "diferencia": "diferencia_pago_de_mas", "pago_a_cuenta_renta": "pago_a_cuenta_renta_1pct"}.get(k, k)
        lineas.append(f"| {k} | {esperado[k_esp]:,.2f} | {res[k]:,.2f} |")
    lineas += ["", "Nota: es un caso sintético con imágenes limpias (salvo C07). Con fotos reales la exactitud será menor; por eso la app marca para revisión las filas con baja confianza o con discrepancias entre motores."]
    (RAIZ / "docs" / "evaluacion_extraccion.md").write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print(por_campo.to_string())
    print("Global:", round(df["ok"].mean() * 100, 1), "%")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
