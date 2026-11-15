# Caso sintético: Cevichería Mar y Sazón E.I.R.L., octubre 2026

**Todo es ficticio**: RUC, razones sociales, montos y comprobantes. No hay datos de personas reales.

- **Negocio:** restaurante MYPE en el Régimen MYPE Tributario, RUC ficticio 20999990017.
- **Tasas 2026:** ventas al 10,5 % (8 % IGV + 2,5 % IPM, tasa especial de restaurantes MYPE, Ley 31556 modificada por la Ley 32219); compras al 18 % (15,5 % IGV + 2,5 % IPM, Ley 32387). Las tasas están en `data/normativa/parametros.json`.

## Archivos

| Archivo | Qué es |
|---|---|
| `comprobantes/` | 15 imágenes (13 facturas, 2 boletas). C07 está también como foto de celular (`_foto.jpg`), que es la que usa el demo. Se regeneran con `python scripts/generar_comprobantes.py`. |
| `compras_reales.csv` | Verdad terreno: lo que el OCR debería extraer, la categoría y si da crédito fiscal. |
| `propuesta_rce.csv` | Propuesta de compras de SUNAT con errores plantados. Formato simplificado, no el layout oficial del SIRE (el cargador acepta varios nombres de columna). |
| `ventas_resumen.csv` | Ventas del mes. |
| `discrepancias_esperadas.csv` | Lo que el conciliador debe detectar. |
| `resultado_esperado.json` | Cifras correctas; las usan los tests. |
| `extraccion_cache.json` | Lectura ya calculada, para el "modo rápido" del demo. |

## Errores plantados

1. **D1 – Falta en la propuesta:** la factura física `0001-004689` de Pesquera Costa Azul (base S/ 4,100, IGV S/ 738) fue emitida en contingencia y SUNAT no la tenía al generar la propuesta. Es válida y está a nombre del restaurante: hay que agregarla al RCE.
2. **D2 – Sobra en la propuesta:** la factura `F001-000458` de Electro Hogar Perú (un televisor, IGV S/ 378) es un gasto personal del dueño. Si se acepta, se toma crédito indebido.
3. **A1 y A2 – Boletas:** `B001-008841` (mercado) y `B002-003377` (ferretería) no dan crédito. Con factura se habrían recuperado S/ 216.

**Descripciones gravadas:** las verduras, frutas y otros productos frescos suelen estar exonerados de IGV (Apéndice I, prorrogado hasta 2028). Como el caso cobra 18 % en todas las compras, las descripciones son de productos gravados (pulpo cocido, pastas de ají, salsas envasadas). Las compras frescas sin comprobante aparecen en el diagnóstico de la app como "exonerado (probable)".

**Cambio respecto de la primera versión:** antes, D1 era una factura electrónica con el RUC del cliente mal digitado. Esa factura no da crédito en el mes, porque está emitida a otro RUC: el proveedor tiene que anularla y emitir una nueva. Por eso la cambiamos por una factura de contingencia, que sí es crédito del mes. La app igual detecta el caso del RUC errado como alerta (`RUC_CLIENTE_ERRADO`).

## Resultado esperado

| Concepto | Soles |
|---|---|
| Base de ventas | 52,000.00 |
| IGV de ventas (10,5 %) | 5,460.00 |
| Crédito fiscal correcto | 3,985.20 |
| **IGV a pagar correcto** | **1,474.80** |
| Crédito según la propuesta de SUNAT | 3,625.20 |
| IGV a pagar si acepta la propuesta tal cual | 1,834.80 |
| **Pago de más** | **360.00** |
| Pago a cuenta de renta (1 % de ventas) | 520.00 |

Los dos errores casi se compensan (−738 + 378 = −360): el total parece razonable y aun así hay dos problemas distintos, uno de dinero perdido y otro de riesgo ante SUNAT.

**Advertencia:** es un caso inventado para probar el flujo, no evidencia de que estos errores ocurran con esa frecuencia; eso se valida en las entrevistas (`docs/research/`).
