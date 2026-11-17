# Síntesis de las 3 conversaciones

| | Perfil | Régimen | Archivo |
|---|---|---|---|
| A | Dueña de cevichería, 14 trabajadores, Lima | RMT | [entrevista_A_duena_cevicheria.md](entrevista_A_duena_cevicheria.md) |
| B | Dueño de pollería familiar, 4 trabajadores, Lima | RER | [entrevista_B_dueno_polleria.md](entrevista_B_dueno_polleria.md) |
| C | Contador independiente, ~25 MYPE, Ayacucho | — | [entrevista_C_contador_freelance.md](entrevista_C_contador_freelance.md) |

## Hipótesis

| Hipótesis | A | B | C | Conclusión |
|---|---|---|---|---|
| H1: se acepta la propuesta sin comparar | Sí: "el contador lo acepta casi siempre" | No sabe qué es el SIRE | No: compara a mano en Excel, pero le cuesta horas | **Parcial.** El dueño no revisa; el contador diligente sí, a mano. |
| H2: buena parte de las compras no tiene factura a su RUC | Mercado con boleta o nada | Carbón y bolsas sin comprobante; boletas, tickets y notas | — | **Confirmada (cualitativa)**, sin un % medido. |
| H3: aparecen compras ajenas al negocio | Prefirió no responder | — | Clientes le piden "meter" compras de la casa | **Confirmada** por el contador. |
| H4: paga el contador, no el dueño | Pagaría si es simple y cuesta menos que el contador | Le interesa si aligera el trabajo de su sobrino | Pagaría por volumen tras probar casos reales | **Mixta:** los dos pagan, por razones distintas. |
| H5: los comprobantes llegan como foto | Sobre y fotos con el celular | Fotos por WhatsApp, a veces borrosas | Fotos y facturas por WhatsApp | **Confirmada.** |

## Problemas con el SIRE que mencionaron

- **No lo entienden:** "si lo toco, lo malogro" (A); "¿eso no es solo para empresas grandes?" (B, que está en el RER y sí está obligado).
- **Facturas que faltan en la propuesta:** RUC del cliente mal digitado (B, C), proveedor que no informa o informa tarde (A, C).
- **Comprobantes físicos:** talonarios que el dueño no sabe si sirven (B); los de contingencia no vienen en la propuesta (C).
- **Cargas con errores:** comprobantes rechazados por validaciones y archivos "procesados con errores" (C).
- **El cuello de botella no es el SIRE sino juntar los papeles** a tiempo y legibles (C, B).

## Qué cambió en el producto por estas conversaciones

| Hallazgo | Cambio en ValidaSIRE |
|---|---|
| Dos dueños no saben qué compras sirven para descontar IGV ni cuándo pedir factura (A, B) | Sección **4 · Diagnóstico**: IGV recuperable por compra, distinción entre gravado y exonerado (frescos del mercado), y pestaña "¿Qué compras me sirven?" con los requisitos del crédito fiscal. |
| Talonarios que no saben si aplican (B) | Pestaña "¿Mi talonario sirve?": contingencia, imprenta autorizada y consulta de validez en SUNAT. |
| Compras al mercado y a productores sin RUC (A, B) | Explicación de la **liquidación de compra** y de por qué, en el RMT, comprar sin comprobante sube la renta aunque no haya IGV. |
| RUC mal digitado (B, C) | Alerta `RUC_CLIENTE_ERRADO` y recomendación de revisar el RUC antes de pagar. |
| Fotos borrosas y envíos a fin de mes (B, C) | Confianza del OCR por fila y recomendación de envío semanal. |
| "Que exporte a Excel, auditable, que no invente números" (C) | **Descarga en Excel** con hojas de resumen, conciliación, diagnóstico y fórmulas; el LLM solo lee, nunca calcula. |
| Restaurante en el RER (B) | Selector de régimen de renta: en el RER la cuota es 1,5 % de ingresos y los costos sin sustento no cambian el impuesto. |
| Miedo a la vinculación económica (A, C) | Verificación de la tasa especial y recomendación cuando hay socios en común. |
| Dudas sobre 2027 (C) | Impacto estimado de la tasa de 2027 sobre el IGV de ventas. |
