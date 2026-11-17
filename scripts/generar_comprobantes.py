"""Genera las imágenes de los 15 comprobantes del caso sintético a partir de compras_reales.csv.

- Facturas electrónicas y boletas: PNG limpios (la razón social larga se parte en líneas
  para que no se monte sobre el recuadro de la serie, defecto de la primera versión).
- C07 es una factura FÍSICA emitida en contingencia: SUNAT no la tiene al generar la
  propuesta del RCE y hay que agregarla. Se genera limpia y como "foto de celular"
  (girada, con sombra y desenfoque), que es la que usa el demo.

Uso:  python scripts/generar_comprobantes.py
Todo es ficticio (RUC, razones sociales, montos).
"""
from __future__ import annotations

import csv
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

RAIZ = Path(__file__).resolve().parents[1]
DIR = RAIZ / "data" / "caso_sintetico"
CLIENTE = "CEVICHERIA MAR Y SAZON E.I.R.L."
W, H = 900, 1100
NEGRO, GRIS, ROJO = (20, 20, 20), (115, 115, 115), (160, 20, 20)


def fuente(negrita: bool, tam: int) -> ImageFont.FreeTypeFont:
    for nombre in (["arialbd.ttf", "DejaVuSans-Bold.ttf"] if negrita else ["arial.ttf", "DejaVuSans.ttf"]):
        try:
            return ImageFont.truetype(nombre, tam)
        except OSError:
            continue
    return ImageFont.load_default()


def partir(d: ImageDraw.ImageDraw, texto: str, f, ancho: int) -> list[str]:
    lineas, actual = [], ""
    for palabra in texto.split():
        prueba = f"{actual} {palabra}".strip()
        if d.textlength(prueba, font=f) <= ancho:
            actual = prueba
        else:
            lineas.append(actual)
            actual = palabra
    return lineas + [actual]


def soles(x: float) -> str:
    return f"S/ {x:,.2f}"


def dibujar(r: dict) -> Image.Image:
    fisica = r["serie_numero"][0].isdigit()
    boleta = r["tipo_comprobante"] == "Boleta"
    img = Image.new("RGB", (W, H), (250, 248, 240) if fisica else (255, 255, 255))
    d = ImageDraw.Draw(img)
    t, n, nb, s, xs = fuente(True, 26), fuente(False, 21), fuente(True, 21), fuente(False, 17), fuente(False, 15)

    d.rectangle([20, 20, W - 20, H - 20], outline=NEGRO, width=3)
    y = 40
    for linea in partir(d, r["proveedor"], t, 450):
        d.text((40, y), linea, font=t, fill=NEGRO)
        y += 34
    d.text((40, y + 6), f"RUC: {r['ruc_proveedor']}", font=n, fill=NEGRO)
    d.text((40, y + 40), "Av. Ejemplo 123, Lima - DOCUMENTO FICTICIO", font=s, fill=GRIS)

    nota = r["tipo_comprobante"] == "Nota de credito"
    titulo = ("NOTA DE CREDITO ELECTRONICA" if nota else "FACTURA" if fisica
              else ("BOLETA DE VENTA ELECTRONICA" if boleta else "FACTURA ELECTRONICA"))
    d.rectangle([520, 40, W - 40, 175], outline=NEGRO, width=2)
    tf = fuente(True, 19) if (boleta or nota) else nb
    d.text((535, 52), titulo, font=tf, fill=NEGRO)
    d.text((535, 88), f"RUC {r['ruc_proveedor']}", font=n, fill=NEGRO)
    d.text((535, 124), r["serie_numero"], font=nb, fill=ROJO if fisica else NEGRO)

    y = max(y + 90, 200)
    if fisica:
        d.text((40, y), "EMITIDA EN CONTINGENCIA", font=nb, fill=ROJO)
        y += 38
    d.text((40, y), f"Fecha de emision: {r['fecha']}", font=n, fill=NEGRO)
    if boleta:
        d.text((40, y + 37), "Cliente: VARIOS", font=n, fill=NEGRO)
    else:
        d.text((40, y + 37), f"Senor(es): {CLIENTE}", font=n, fill=NEGRO)
        d.text((40, y + 74), f"RUC: {r['ruc_cliente_en_comprobante']}", font=n, fill=NEGRO)
    if nota:
        d.text((40, y + 111), f"Documento que modifica: {r['doc_referencia']}", font=nb, fill=NEGRO)
        d.text((40, y + 148), f"Motivo: {r['motivo']}", font=n, fill=NEGRO)
        y += 74

    y += 120
    d.line([40, y, W - 40, y], fill=NEGRO, width=2)
    d.text((40, y + 12), "Descripcion", font=nb, fill=NEGRO)
    d.text((700, y + 12), "Importe", font=nb, fill=NEGRO)
    d.line([40, y + 45, W - 40, y + 45], fill=NEGRO, width=1)
    total, base, igv = float(r["total"]), float(r["base"]), float(r["igv"])
    d.text((40, y + 65), r["descripcion"], font=n, fill=NEGRO)
    d.text((700, y + 65), soles(total if boleta else base), font=n, fill=NEGRO)

    if not boleta:
        d.text((520, 790), "Op. gravada", font=n, fill=NEGRO)
        d.text((700, 790), soles(base), font=n, fill=NEGRO)
        d.text((520, 835), "IGV (18%)", font=n, fill=NEGRO)
        d.text((700, 835), soles(igv), font=n, fill=NEGRO)
    d.text((520, 880), "TOTAL", font=nb, fill=NEGRO)
    d.text((700, 880), soles(total), font=nb, fill=NEGRO)

    pie = ("Imprenta Grafica Ejemplo - Aut. ficticia 0000000" if fisica
           else "Representacion impresa - generado para demo")
    d.text((40, 1030), pie, font=xs, fill=GRIS)
    return img


def como_foto(img: Image.Image) -> Image.Image:
    """Simula una foto de celular: mesa de fondo, giro, sombra y algo de desenfoque."""
    fondo = Image.new("RGB", (1100, 1300), (120, 98, 78))
    fondo.paste(img.rotate(3.5, expand=True, fillcolor=(120, 98, 78), resample=Image.BICUBIC), (60, 60))
    sombra = Image.new("L", fondo.size, 0)
    ImageDraw.Draw(sombra).ellipse([650, 850, 1500, 1700], fill=90)
    sombra = sombra.filter(ImageFilter.GaussianBlur(120))
    fondo = Image.composite(Image.new("RGB", fondo.size, (0, 0, 0)), fondo, sombra)
    return fondo.filter(ImageFilter.GaussianBlur(0.9))


def notas() -> None:
    """Notas de crédito del caso con devoluciones y descuentos (carpeta aparte: no cambian el caso base)."""
    destino = DIR / "notas"
    with (destino / "notas_compras.csv").open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            dibujar(r).save(destino / f"{r['id']}_{r['serie_numero']}.png")
    print("Notas generadas en", destino)


def main() -> None:
    destino = DIR / "comprobantes"
    destino.mkdir(parents=True, exist_ok=True)
    for viejo in destino.glob("C*.*"):
        viejo.unlink()
    with (DIR / "compras_reales.csv").open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            img = dibujar(r)
            nombre = f"{r['id']}_{r['serie_numero']}"
            img.save(destino / f"{nombre}.png")
            if r["serie_numero"][0].isdigit():
                como_foto(img).save(destino / f"{nombre}_foto.jpg", quality=82)
    print("Comprobantes generados en", destino)


if __name__ == "__main__":
    import sys

    notas() if "--notas" in sys.argv else (main(), notas())
