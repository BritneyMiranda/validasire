"""Del texto del OCR a un comprobante estructurado.

Dos extractores independientes:
1. Reglas + geometría (determinista, gratis, sin internet). Busca cada etiqueta ("Op. gravada",
   "IGV", "TOTAL") y toma el monto que está en la misma fila, a la derecha. Usa la posición de
   las cajas del OCR, no el orden del texto, porque en una foto girada el orden cambia.
2. Claude con salidas estructuradas (sesión 08): recibe el texto ordenado y devuelve un
   `Comprobante` validado con Pydantic. Corrige lo que las reglas no entienden (formatos de
   otros proveedores, razones sociales mal leídas).

Si hay API key se usan ambos y se comparan: si discrepan en un campo clave, la fila queda
marcada "revisar" para que la persona confirme. Si no hay key o la llamada falla, quedan las
reglas. Ningún monto de impuesto lo calcula el LLM: solo lee lo que está impreso.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .modelos import Comprobante
from .ocr import Caja, ResultadoOCR
from .parametros import TOLERANCIA_SOLES

RE_RUC = re.compile(r"(?<!\d)((?:10|15|16|17|20)\d{9})(?!\d)")
RE_SERIE = re.compile(r"^\s*(?:N[°ºo.]*\s*)?([FBE][A-Z0-9]{3}|\d{4})\s*-\s*(\d{3,8})\s*$", re.I)
RE_FECHA_ISO = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")
RE_FECHA_PE = re.compile(r"(\d{2})/(\d{2})/(20\d{2})")
RE_MONTO = re.compile(r"S\s*/\.?\s*([\d,]+\.\d{2})")

ETIQUETAS = {
    "base_gravada": re.compile(r"op\.?\s*gravada|valor\s+de\s+venta|sub\s*-?\s*total", re.I),
    "igv": re.compile(r"^\s*i\.?g\.?v\.?\b", re.I),
    "total": re.compile(r"^\s*(importe\s+)?total\b", re.I),
}
RE_CLIENTE = re.compile(r"se[ñn]or|cliente|adquir", re.I)
PALABRAS_NO_RAZON = re.compile(r"factura|boleta|ruc|nota de|electr|contingencia|fecha", re.I)

PROMPT_PATH = Path(__file__).resolve().parents[1] / "ai" / "prompts" / "extraccion_comprobante.md"
CAMPOS_CLAVE = ("serie_numero", "ruc_emisor", "ruc_cliente", "igv", "total")


def _monto(texto: str) -> Optional[float]:
    m = RE_MONTO.search(texto)
    return float(m.group(1).replace(",", "")) if m else None


def _monto_en_fila(etiqueta: Caja, cajas: list[Caja]) -> Optional[float]:
    propio = _monto(etiqueta.texto)
    if propio is not None:
        return propio
    candidatos = [c for c in cajas if c is not etiqueta and c.x > etiqueta.x and _monto(c.texto) is not None]
    if not candidatos:
        return None
    mejor = min(candidatos, key=lambda c: abs(c.y - etiqueta.y))
    if abs(mejor.y - etiqueta.y) > 1.2 * max(etiqueta.alto, mejor.alto):
        return None
    return _monto(mejor.texto)


def extraer_con_reglas(ocr: ResultadoOCR) -> Comprobante:
    cajas = ocr.cajas
    texto = " \n".join(c.texto for c in cajas)

    if re.search(r"boleta", texto, re.I):
        tipo = "Boleta"
    elif re.search(r"nota\s+de\s+cr[eé]dito", texto, re.I):
        tipo = "Nota de credito"
    elif re.search(r"factura", texto, re.I):
        tipo = "Factura"
    else:
        tipo = "Otro"

    serie = ""
    for c in cajas:
        m = RE_SERIE.match(c.texto)
        if m:
            serie = f"{m.group(1).upper()}-{m.group(2)}"
            break
    if tipo == "Otro" and serie:
        # Series electrónicas de SUNAT: facturas F### o E001; boletas B### o EB01.
        if serie.startswith(("EB", "B")):
            tipo = "Boleta"
        elif serie.startswith(("F", "E0")):
            tipo = "Factura"

    fecha = None
    for c in cajas:
        if m := RE_FECHA_ISO.search(c.texto):
            fecha = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            break
        if m := RE_FECHA_PE.search(c.texto):
            fecha = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
            break

    cajas_ruc = sorted([c for c in cajas if RE_RUC.search(c.texto)], key=lambda c: c.y)
    ruc_emisor = RE_RUC.search(cajas_ruc[0].texto).group(1) if cajas_ruc else None
    ruc_cliente = None
    etiqueta_cliente = next((c for c in sorted(cajas, key=lambda c: c.y) if RE_CLIENTE.search(c.texto)), None)
    if etiqueta_cliente is not None:
        debajo = [c for c in cajas_ruc if c.y > etiqueta_cliente.y - 0.5 * etiqueta_cliente.alto]
        debajo = [c for c in debajo if RE_RUC.search(c.texto).group(1) != ruc_emisor]
        if debajo:
            cerca = min(debajo, key=lambda c: c.y - etiqueta_cliente.y)
            if cerca.y - etiqueta_cliente.y < 4 * etiqueta_cliente.alto:
                ruc_cliente = RE_RUC.search(cerca.texto).group(1)

    razon = None
    for c in sorted(cajas, key=lambda c: c.y):
        if not PALABRAS_NO_RAZON.search(c.texto) and not RE_RUC.search(c.texto) and len(c.texto) > 4:
            razon = c.texto
            break

    descripcion = None
    cab = next((c for c in cajas if re.fullmatch(r"\s*descripci[oó]n\s*", c.texto, re.I)), None)
    if cab is not None:
        abajo = [c for c in cajas if c.y > cab.y + 0.5 * cab.alto and _monto(c.texto) is None]
        if abajo:
            descripcion = min(abajo, key=lambda c: (c.y - cab.y) + 0.05 * abs(c.x0 - cab.x0)).texto

    montos: dict[str, Optional[float]] = {k: None for k in ETIQUETAS}
    for campo, patron in ETIQUETAS.items():
        for c in cajas:
            if patron.search(c.texto):
                montos[campo] = _monto_en_fila(c, cajas)
                if montos[campo] is not None:
                    break

    return Comprobante(
        tipo_comprobante=tipo,
        serie_numero=serie,
        fecha_emision=fecha,
        ruc_emisor=ruc_emisor,
        razon_social_emisor=razon,
        ruc_cliente=ruc_cliente,
        descripcion=descripcion,
        base_gravada=montos["base_gravada"],
        igv=montos["igv"],
        total=montos["total"],
        contingencia=bool(re.search(r"contingencia", texto, re.I) or re.match(r"^\d{4}-", serie)),
    )


# ---------------------------------------------------------------- Claude (salida estructurada)


def cliente_claude():
    """Devuelve un cliente de Anthropic si hay credenciales; si no, None (modo sin LLM)."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic

        return anthropic.Anthropic(max_retries=2, timeout=60)
    except Exception:
        return None


def modelo_claude() -> str:
    return os.getenv("CLAUDE_MODEL", "claude-opus-5-5")


def extraer_con_claude(ocr: ResultadoOCR, client, modelo: str) -> tuple[Comprobante, dict]:
    sistema = PROMPT_PATH.read_text(encoding="utf-8")
    resp = client.messages.parse(
        model=modelo,
        max_tokens=4000,
        system=sistema,
        messages=[{"role": "user", "content": f"<texto_ocr>\n{ocr.texto_ordenado()}\n</texto_ocr>"}],
        output_format=Comprobante,
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError(f"Claude no devolvió un comprobante (stop_reason={resp.stop_reason})")
    uso = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
    return resp.parsed_output, uso


# ---------------------------------------------------------------- validaciones deterministas


def _iguales(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) or isinstance(b, float):
        return abs(float(a) - float(b)) <= TOLERANCIA_SOLES
    return str(a).strip().upper() == str(b).strip().upper()


def validar(c: Comprobante, ruc_propio: str, tasa_compras: float = 0.18) -> list[str]:
    obs: list[str] = []
    if not c.serie_numero:
        obs.append("No se leyó la serie y número.")
    if c.ruc_emisor and len(c.ruc_emisor) != 11:
        obs.append("El RUC del proveedor no tiene 11 dígitos.")
    if c.base_gravada is not None and c.igv is not None and c.total is not None:
        if abs(c.base_gravada + c.igv - c.total) > 0.05:
            obs.append("Base + IGV no cuadra con el total.")
        if abs(c.base_gravada * tasa_compras - c.igv) > 1.0:
            obs.append(f"El IGV no es {tasa_compras:.0%} de la base: revisa si hay montos exonerados.")
    if c.tipo_comprobante == "Factura":
        if not c.ruc_cliente:
            obs.append("No se leyó el RUC del cliente.")
        elif c.ruc_cliente != ruc_propio:
            obs.append(f"La factura está a nombre de otro RUC ({c.ruc_cliente}). Pide al proveedor que la anule y emita una nueva.")
    return obs


@dataclass
class Extraccion:
    archivo: str
    comprobante: Comprobante
    motor: str
    confianza_ocr: float
    segundos_ocr: float
    texto_ocr: str
    observaciones: list[str] = field(default_factory=list)
    discrepancias_motores: list[str] = field(default_factory=list)
    uso_tokens: dict = field(default_factory=dict)

    @property
    def revisar(self) -> bool:
        return bool(self.discrepancias_motores) or self.confianza_ocr < 0.85 or bool(self.observaciones)


def extraer(archivo: str, ocr: ResultadoOCR, ruc_propio: str, client=None, modelo: str | None = None) -> Extraccion:
    por_reglas = extraer_con_reglas(ocr)
    elegido, motor, uso, difs = por_reglas, "reglas", {}, []
    if client is not None:
        try:
            por_llm, uso = extraer_con_claude(ocr, client, modelo or modelo_claude())
            difs = [k for k in CAMPOS_CLAVE if not _iguales(getattr(por_llm, k), getattr(por_reglas, k))]
            elegido, motor = por_llm, "claude+reglas"
        except Exception as e:  # sin internet, sin saldo, etc.: el flujo sigue con reglas
            motor = f"reglas (Claude falló: {type(e).__name__})"
    return Extraccion(
        archivo=archivo,
        comprobante=elegido,
        motor=motor,
        confianza_ocr=ocr.confianza_media,
        segundos_ocr=ocr.segundos,
        texto_ocr=ocr.texto_ordenado(),
        observaciones=validar(elegido, ruc_propio),
        discrepancias_motores=difs,
        uso_tokens=uso,
    )
