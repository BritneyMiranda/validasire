"""Esquema del comprobante extraído. Lo usa el LLM (salida estructurada) y el parser de reglas."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Comprobante(BaseModel):
    tipo_comprobante: Literal["Factura", "Boleta", "Nota de credito", "Otro"] = Field(
        description="Tipo de comprobante de pago impreso en el documento."
    )
    serie_numero: str = Field(description="Serie y número, p. ej. F001-004512 o 0001-004689.")
    fecha_emision: Optional[str] = Field(None, description="Fecha de emisión en formato AAAA-MM-DD.")
    ruc_emisor: Optional[str] = Field(None, description="RUC de 11 dígitos de quien emite (el proveedor).")
    razon_social_emisor: Optional[str] = Field(None, description="Nombre o razón social del proveedor.")
    ruc_cliente: Optional[str] = Field(
        None, description="RUC del cliente (el comprador). Vacío si no figura, como en muchas boletas."
    )
    descripcion: Optional[str] = Field(None, description="Resumen corto de lo comprado.")
    base_gravada: Optional[float] = Field(None, description="Operación gravada / valor de venta, en soles.")
    igv: Optional[float] = Field(None, description="IGV (incluye IPM) en soles, si está desglosado.")
    total: Optional[float] = Field(None, description="Importe total en soles.")
    contingencia: bool = Field(
        False, description="True si el documento dice que fue emitido en contingencia o es un comprobante físico."
    )
