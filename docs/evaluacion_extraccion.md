# Evaluación de la extracción

Generado con `python scripts/evaluar_extraccion.py`. Motor: **OCR PP-OCR (RapidOCR) + reglas, sin LLM**.
Comprobantes: 15 (13 facturas, una de ellas foto de celular girada, y 2 boletas).

## Exactitud por campo

| Campo | % exacto |
|---|---|
| tipo_comprobante | 100.0 % |
| serie_numero | 100.0 % |
| fecha_emision | 100.0 % |
| ruc_emisor | 100.0 % |
| ruc_cliente | 100.0 % |
| base_gravada | 100.0 % |
| igv | 100.0 % |
| total | 100.0 % |

**Exactitud global: 100.0 %** (120 de 120 campos).

## Errores

Ninguno.

## Cifras finales contra `resultado_esperado.json`

| Concepto | Esperado | Obtenido |
|---|---|---|
| igv_debito_ventas | 5,460.00 | 5,460.00 |
| credito_fiscal_revisado | 3,985.20 | 3,985.20 |
| igv_a_pagar_revisado | 1,474.80 | 1,474.80 |
| credito_fiscal_segun_propuesta | 3,625.20 | 3,625.20 |
| igv_a_pagar_si_acepta_propuesta | 1,834.80 | 1,834.80 |
| diferencia | 360.00 | 360.00 |
| pago_a_cuenta_renta | 520.00 | 520.00 |

Nota: es un caso sintético con imágenes limpias (salvo C07). Con fotos reales la exactitud será menor; por eso la app marca para revisión las filas con baja confianza o con discrepancias entre motores.
