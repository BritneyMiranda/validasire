# Arquitectura de Cuadra

```mermaid
flowchart LR
    A[Fotos de facturas<br/>y boletas] --> B[OCR PP-OCR<br/>RapidOCR / ONNX<br/>backend/ocr.py]
    B --> C[Reglas + geometría<br/>backend/extraccion.py]
    B --> D[Claude: salida estructurada<br/>Pydantic · ai/prompts/]
    C --> E{¿Coinciden?}
    D --> E
    E -- no --> F[Fila marcada<br/>para revisar]
    E -- sí --> G[Tabla editable<br/>de compras]
    F --> G
    H[Propuesta RCE<br/>CSV del SIRE] --> I[Conciliación determinista<br/>backend/conciliacion.py]
    G --> I
    J[Ventas del mes] --> K[Cálculo de IGV]
    I --> K
    P[data/normativa/parametros.json<br/>tasas, UIT, fecha de revisión] --> K
    S[Monitor semanal<br/>índice de normas SUNAT] -. issue para revisar .-> P
    K --> Dg[Diagnóstico determinista<br/>backend/diagnostico.py]
    N[Compras sin comprobante<br/>estimadas por el dueño] --> Dg
    Dg --> L[Dashboard Streamlit<br/>reporte · Excel · mensaje]
    K --> L
```

| Capa | Archivo | Qué hace |
|---|---|---|
| Interfaz | `app/streamlit_app.py` | Subida de comprobantes, tabla editable, resultado, diagnóstico y descargas. |
| OCR (Document AI) | `backend/ocr.py` | Modelos PP-OCRv6 de PaddleOCR en ONNX (RapidOCR), en CPU. Devuelve texto con posición. |
| Extracción | `backend/extraccion.py`, `backend/modelos.py`, `ai/prompts/` | Reglas por geometría + Claude con salida estructurada (Pydantic). Si difieren, se marca para revisar. |
| Conciliación y cálculo | `backend/conciliacion.py` | Compara contra la propuesta del RCE y calcula el IGV con tasas parametrizadas. |
| Diagnóstico | `backend/diagnostico.py` | IGV recuperable, exonerados, renta por costos sin sustento, recomendaciones y Excel. |
| Normativa | `data/normativa/parametros.json`, `scripts/monitor_normativa.py` | Tasas con fecha de revisión; monitor semanal del índice de SUNAT (GitHub Actions). |
| Calidad | `tests/`, `scripts/evaluar_extraccion.py` | 18 tests y evaluación por campo contra la verdad terreno. |

El LLM solo lee los comprobantes: ningún impuesto se calcula con IA.
