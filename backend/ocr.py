"""Document AI (sesión 10): OCR de comprobantes con los modelos PP-OCR de PaddleOCR.

En clase usamos PaddleOCR 3.7 con PyTorch. Para que el producto corra en un servidor gratuito
(Streamlit Community Cloud, sin GPU y con poca RAM) usamos los MISMOS modelos PP-OCR exportados
a ONNX mediante RapidOCR: ~32 MB de modelos y ~1,5 s por comprobante en CPU. Las imágenes no
salen del servidor para el OCR; al LLM solo le llega el texto.
"""
from __future__ import annotations

import io
import time
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from PIL import Image, ImageOps


@dataclass
class Caja:
    texto: str
    confianza: float
    x: float  # centro
    y: float  # centro
    alto: float
    x0: float
    x1: float


@dataclass
class ResultadoOCR:
    cajas: list[Caja] = field(default_factory=list)
    segundos: float = 0.0

    @property
    def confianza_media(self) -> float:
        return float(np.mean([c.confianza for c in self.cajas])) if self.cajas else 0.0

    def texto_ordenado(self) -> str:
        """Texto por filas (arriba-abajo, izquierda-derecha), útil para el LLM y para mostrar."""
        if not self.cajas:
            return ""
        cajas = sorted(self.cajas, key=lambda c: c.y)
        filas: list[list[Caja]] = [[cajas[0]]]
        for c in cajas[1:]:
            ref = filas[-1][0]
            if abs(c.y - ref.y) < 0.6 * max(ref.alto, c.alto):
                filas[-1].append(c)
            else:
                filas.append([c])
        return "\n".join("   ".join(c.texto for c in sorted(f, key=lambda c: c.x)) for f in filas)


@lru_cache(maxsize=1)
def _motor():
    from rapidocr import RapidOCR  # import diferido: carga los modelos una sola vez

    return RapidOCR()


def preparar_imagen(datos: bytes, lado_max: int = 1600) -> np.ndarray:
    img = Image.open(io.BytesIO(datos))
    img = ImageOps.exif_transpose(img).convert("RGB")  # respeta la orientación de fotos de celular
    if max(img.size) > lado_max:
        img.thumbnail((lado_max, lado_max))
    return np.array(img)[:, :, ::-1].copy()  # RGB -> BGR (OpenCV)


def leer(datos: bytes) -> ResultadoOCR:
    t0 = time.perf_counter()
    salida = _motor()(preparar_imagen(datos))
    cajas: list[Caja] = []
    if salida is not None and salida.boxes is not None:
        for caja, txt, sc in zip(salida.boxes, salida.txts, salida.scores):
            pts = np.asarray(caja, dtype=float)
            cajas.append(
                Caja(
                    texto=str(txt).strip(),
                    confianza=float(sc),
                    x=float(pts[:, 0].mean()),
                    y=float(pts[:, 1].mean()),
                    alto=float(pts[:, 1].max() - pts[:, 1].min()),
                    x0=float(pts[:, 0].min()),
                    x1=float(pts[:, 0].max()),
                )
            )
    return ResultadoOCR(cajas=cajas, segundos=time.perf_counter() - t0)
