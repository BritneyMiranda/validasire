"""Diagnóstico del negocio: dónde se pierde IGV, cuánto se podría recuperar y qué hacer.

Nace de las entrevistas (docs/research/): dos de los tres dueños no saben qué compras sirven
para descontar IGV ni cuándo pedir factura, sobre todo en el mercado, y el contador pidió que
cada número sea auditable. Por eso todo aquí es determinista: reglas por palabras clave y
fórmulas simples, que se muestran junto a cada monto. El LLM no participa.

Supuestos que se muestran al usuario:
- "IGV recuperable" = lo que se recuperaría si se consigue el MISMO precio total con factura:
  monto × 18/118. Si el proveedor formal cobra más, el ahorro es menor.
- Productos frescos (frutas, hortalizas y, según la partida, pescado fresco) suelen estar
  exonerados de IGV (Apéndice I de la Ley del IGV, prorrogado hasta el 31/12/2028 por la
  Ley 32542): pedir factura no recupera IGV, pero el comprobante sí sustenta el costo para Renta.
- Renta: en el RMT el costo sin comprobante no se puede deducir. Estimamos el impacto con la
  tasa del primer tramo (10 %), que es la estimación más conservadora. En el RER la renta es un %
  de los ingresos, así que el costo no cambia el impuesto.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from .conciliacion import Resumen
from .parametros import TASAS, tasa

TASA_RENTA_RMT_TRAMO1 = 0.10
RETENCION_RENTA_LIQUIDACION = 0.015

# (patrón, categoría). El orden importa: gana el primero que calza.
CATEGORIAS: list[tuple[str, str]] = [
    (r"pescad|marisc|pulpo|calamar|langost|concha|terminal pesquero", "Pescados y mariscos"),
    (r"verdur|hortaliz|fruta|lim[oó]n|cebolla|camote|culantro|hierba|tomate|papa\b|papas\b|choclo|lechuga", "Verduras y frutas"),
    (r"pollo|carne|lomo|chancho|res\b|aves", "Carnes y aves"),
    (r"aceite|arroz|abarrot|sillao|vinagre|especia|aj[ií]|rocoto|salsa|sal\b|az[uú]car", "Abarrotes y salsas"),
    (r"cerveza|gaseosa|bebida|agua mineral|hielo", "Bebidas y hielo"),
    (r"glp|gas\b|bal[oó]n|carb[oó]n|le[ñn]a", "Gas y combustible"),
    (r"envase|bolsa|t[aá]per|empaque|descartable|servillet", "Empaques"),
    (r"alquiler", "Alquiler"),
    (r"energ|luz\b|agua\b|internet|telef", "Servicios públicos"),
    (r"delivery|comisi[oó]n", "Comisiones de delivery"),
    (r"fumig|limpieza|reparac|ferret|mantenim|campana", "Mantenimiento"),
]
# Productos frescos que suelen estar en el Apéndice I (exonerados de IGV). Procesados,
# cocidos, congelados o envasados quedan fuera de este patrón a propósito.
RE_EXONERADO = re.compile(
    r"\b(verduras?|hortalizas?|frutas?|lim[oó]n(es)?|cebollas?|camotes?|culantro|hierbas?|tomates?|papas?|"
    r"choclo|lechugas?|pescado fresco|terminal pesquero)\b", re.I)
RE_PROCESADO = re.compile(r"cocid|congelad|precocid|envasad|pasta|salsa|enlatad", re.I)
RE_PRIMARIO_SIN_RUC = re.compile(r"verdur|hortaliz|fruta|lim[oó]n|cebolla|hierba|pescado|terminal pesquero|chacra|productor|le[ñn]a", re.I)


def categorizar(texto: str) -> str:
    t = str(texto or "").lower()
    for patron, cat in CATEGORIAS:
        if re.search(patron, t):
            return cat
    return "Otros"


def tratamiento_igv(texto: str) -> str:
    t = str(texto or "")
    if RE_EXONERADO.search(t) and not RE_PROCESADO.search(t):
        return "exonerado (probable)"
    return "gravado"


@dataclass
class Diagnostico:
    lineas: pd.DataFrame
    por_categoria: pd.DataFrame
    igv_recuperable_mes: float
    renta_costo_sin_sustento_mes: float
    impuesto_renta_estimado_mes: float
    impacto_2027_mes: float
    pct_compras_con_credito: float
    recomendaciones: list[dict] = field(default_factory=list)

    @property
    def igv_recuperable_anio(self) -> float:
        return round(self.igv_recuperable_mes * 12, 2)


def _lineas(compras: pd.DataFrame, sin_comprobante: pd.DataFrame, t_compras: float, regimen_renta: str) -> pd.DataFrame:
    filas = []
    comp = compras
    if "incluir" in comp:
        comp = comp[comp["incluir"].fillna(True).astype(bool)]
    for _, c in comp.iterrows():
        if c.get("tipo_comprobante") != "Boleta":
            continue
        texto = f"{c.get('descripcion') or ''} {c.get('razon_social_emisor') or ''}"
        total = float(c.get("total") or 0)
        trat = tratamiento_igv(texto)
        filas.append({
            "origen": "Boleta", "descripcion": c.get("descripcion") or c.get("razon_social_emisor"),
            "proveedor": c.get("razon_social_emisor"), "ruc_proveedor": str(c.get("ruc_emisor") or ""),
            "monto": total, "categoria": categorizar(texto), "tratamiento": trat,
            "igv_recuperable": round(total * t_compras / (1 + t_compras), 2) if trat == "gravado" else 0.0,
            "costo_sin_sustento": 0.0,  # la boleta con tus datos sustenta costo (con límites)
        })
    if sin_comprobante is not None and not sin_comprobante.empty:
        for _, s in sin_comprobante.iterrows():
            monto = float(pd.to_numeric(s.get("monto"), errors="coerce") or 0)
            if monto <= 0:
                continue
            texto = str(s.get("descripcion") or "")
            trat = tratamiento_igv(texto)
            base = monto / (1 + t_compras) if trat == "gravado" else monto
            filas.append({
                "origen": "Sin comprobante", "descripcion": texto, "proveedor": "", "ruc_proveedor": "",
                "monto": monto, "categoria": categorizar(texto), "tratamiento": trat,
                "igv_recuperable": round(monto * t_compras / (1 + t_compras), 2) if trat == "gravado" else 0.0,
                "costo_sin_sustento": round(base, 2) if regimen_renta == "RMT" else 0.0,
            })
    cols = ["origen", "descripcion", "proveedor", "ruc_proveedor", "monto", "categoria", "tratamiento",
            "igv_recuperable", "costo_sin_sustento"]
    return pd.DataFrame(filas, columns=cols)


def _accion(fila: pd.Series) -> str:
    ruc = fila["ruc_proveedor"]
    if fila["tratamiento"] != "gravado":
        if fila["origen"] == "Sin comprobante" and RE_PRIMARIO_SIN_RUC.search(fila["descripcion"] or ""):
            return ("Producto fresco: no hay IGV que recuperar. Pide boleta o factura (aunque sea exonerada) para "
                    "sustentar el costo; si el vendedor es productor o pescador artesanal sin RUC, emite tú una liquidación de compra.")
        return "Producto fresco: no hay IGV que recuperar, pero pide comprobante para sustentar el costo."
    if fila["origen"] == "Boleta" and ruc.startswith("20"):
        return "El proveedor es una empresa: pide factura a tu RUC antes de pagar."
    if fila["origen"] == "Boleta" and ruc.startswith("10"):
        return ("Persona con negocio: si está en el Nuevo RUS no puede darte factura. Pregúntale; si no puede, "
                "evalúa comprar lo mismo a un proveedor que facture.")
    return "Compra a un proveedor que te dé factura a tu RUC (o pídela a tu proveedor actual)."


def diagnosticar(compras: pd.DataFrame, sin_comprobante: pd.DataFrame, con: pd.DataFrame, res: Resumen,
                 anio: int, regimen_renta: str = "RMT", confianza_minima: float | None = None,
                 vinculacion: bool = False) -> Diagnostico:
    t_compras = tasa(anio, "general").total
    lineas = _lineas(compras, sin_comprobante, t_compras, regimen_renta)
    if not lineas.empty:
        lineas["que_hacer"] = lineas.apply(_accion, axis=1)
        lineas = lineas.sort_values("igv_recuperable", ascending=False).reset_index(drop=True)
    else:
        lineas["que_hacer"] = []

    por_cat = (lineas.groupby(["categoria", "tratamiento"], as_index=False)
               .agg(monto=("monto", "sum"), igv_recuperable=("igv_recuperable", "sum"),
                    costo_sin_sustento=("costo_sin_sustento", "sum"))
               .sort_values(["igv_recuperable", "monto"], ascending=False).reset_index(drop=True)) if not lineas.empty \
        else pd.DataFrame(columns=["categoria", "tratamiento", "monto", "igv_recuperable", "costo_sin_sustento"])

    igv_rec = round(float(lineas["igv_recuperable"].sum()), 2) if not lineas.empty else 0.0
    costo_ss = round(float(lineas["costo_sin_sustento"].sum()), 2) if not lineas.empty else 0.0
    renta_est = round(costo_ss * TASA_RENTA_RMT_TRAMO1, 2)

    compras_con_credito = float(con.loc[con["credito_revisado"] > 0, "credito_revisado"].sum()) * (1 + t_compras) / t_compras
    total_compras = compras_con_credito + (float(lineas["monto"].sum()) if not lineas.empty else 0.0)
    pct = compras_con_credito / total_compras if total_compras else 0.0

    # Impacto de la tasa especial del año siguiente, si ya está cargada y el negocio usa la tasa especial.
    impacto_2027 = 0.0
    sig = anio + 1
    if sig in TASAS and abs(res.tasa_ventas - tasa(anio, "restaurante_mype").total) < 1e-9:
        impacto_2027 = round(res.base_ventas * (tasa(sig, "restaurante_mype").total - res.tasa_ventas), 2)

    recs = _recomendaciones(lineas, con, res, regimen_renta, igv_rec, renta_est, impacto_2027, pct,
                            confianza_minima, vinculacion)
    return Diagnostico(lineas=lineas, por_categoria=por_cat, igv_recuperable_mes=igv_rec,
                       renta_costo_sin_sustento_mes=costo_ss, impuesto_renta_estimado_mes=renta_est,
                       impacto_2027_mes=impacto_2027, pct_compras_con_credito=round(pct, 4), recomendaciones=recs)


def _s(x: float) -> str:
    return f"S/ {x:,.2f}"


def _recomendaciones(lineas, con, res, regimen_renta, igv_rec, renta_est, impacto_2027, pct, confianza_minima,
                     vinculacion) -> list[dict]:
    r: list[dict] = []
    est = con["estado"]

    if res.credito_indebido_evitado:
        r.append({"prioridad": 1, "tema": "Riesgo", "titulo": "Separa las compras personales del negocio",
                  "detalle": f"La propuesta trae {_s(res.credito_indebido_evitado)} de IGV de compras que no son del negocio. "
                             "Usa una tarjeta o cuenta solo para el negocio y no des el RUC del negocio en compras de la casa: "
                             "en una fiscalización ese crédito se repara con intereses y multa.",
                  "monto": res.credito_indebido_evitado})
    if res.credito_recuperado:
        r.append({"prioridad": 1, "tema": "Dinero", "titulo": "Agrega al RCE las facturas que SUNAT no tiene",
                  "detalle": f"Recuperas {_s(res.credito_recuperado)} de crédito este mes. Suelen ser facturas físicas de "
                             "contingencia o informadas tarde por el proveedor: guárdalas aparte y mándale la foto a tu contador el mismo día.",
                  "monto": res.credito_recuperado})
    if (est == "RUC_CLIENTE_ERRADO").any():
        m = float(con.loc[est == "RUC_CLIENTE_ERRADO", "igv_comprobante"].sum())
        r.append({"prioridad": 1, "tema": "Dinero", "titulo": "Pide que reemitan las facturas con tu RUC mal escrito",
                  "detalle": f"{_s(m)} de IGV no te sirven hasta que el proveedor anule y emita una nueva factura. "
                             "Al recibir una factura, revisa tu RUC antes de pagar: es el error más caro y el más fácil de evitar.",
                  "monto": m})

    if not lineas.empty:
        grav = lineas[lineas["tratamiento"] == "gravado"]
        b20 = grav[(grav["origen"] == "Boleta") & grav["ruc_proveedor"].str.startswith("20")]
        if not b20.empty:
            r.append({"prioridad": 2, "tema": "Dinero", "titulo": "Pide factura a las empresas que hoy te dan boleta",
                      "detalle": f"{len(b20)} compra(s) a empresas con RUC 20 vinieron con boleta. Con factura recuperas "
                                 f"{_s(float(b20['igv_recuperable'].sum()))} al mes. Dilo antes de pagar: \"factura a mi RUC, por favor\".",
                      "monto": float(b20["igv_recuperable"].sum())})
        b10 = grav[(grav["origen"] == "Boleta") & grav["ruc_proveedor"].str.startswith("10")]
        if not b10.empty:
            r.append({"prioridad": 2, "tema": "Dinero", "titulo": "Revisa a los proveedores personas naturales",
                      "detalle": f"{len(b10)} compra(s) con boleta de personas con negocio (RUC 10), por {_s(float(b10['igv_recuperable'].sum()))} de IGV. "
                                 "Si están en el Nuevo RUS no pueden emitir factura: compara precio con un proveedor que sí facture.",
                      "monto": float(b10["igv_recuperable"].sum())})
        sc = grav[grav["origen"] == "Sin comprobante"]
        if not sc.empty:
            top = sc.sort_values("igv_recuperable", ascending=False).iloc[0]
            r.append({"prioridad": 2, "tema": "Dinero", "titulo": "Formaliza las compras gravadas que haces sin comprobante",
                      "detalle": f"Pagas {_s(float(sc['monto'].sum()))} al mes sin comprobante en productos gravados "
                                 f"(la mayor: {top['descripcion']}). Con factura al mismo precio total recuperarías "
                                 f"{_s(float(sc['igv_recuperable'].sum()))} de IGV al mes.",
                      "monto": float(sc["igv_recuperable"].sum())})
        exo = lineas[(lineas["tratamiento"] != "gravado") & (lineas["origen"] == "Sin comprobante")]
        if not exo.empty:
            det = (f"{_s(float(exo['monto'].sum()))} al mes en productos frescos sin comprobante. Ahí no hay IGV que recuperar "
                   "(suelen estar exonerados), así que no te preocupes por la factura en sí: lo que pierdes es poder "
                   "deducirlos como costo.")
            if regimen_renta == "RMT":
                det += (f" En el RMT eso puede subir tu Impuesto a la Renta en unos {_s(float(exo['costo_sin_sustento'].sum()) * TASA_RENTA_RMT_TRAMO1)} "
                        "al mes (estimado con 10 %). Pide boleta con tus datos o factura exonerada; si el vendedor es productor "
                        "o pescador artesanal sin RUC, emite una liquidación de compra.")
            else:
                det += " En el RER tu renta es un % de las ventas, así que el costo no cambia tu impuesto: aquí la prioridad es baja."
            r.append({"prioridad": 3 if regimen_renta == "RER" else 2, "tema": "Renta",
                      "titulo": "Compras frescas del mercado: no es IGV, es sustento de costo", "detalle": det,
                      "monto": float(exo["costo_sin_sustento"].sum()) * TASA_RENTA_RMT_TRAMO1 if regimen_renta == "RMT" else 0.0})

    if pct and pct < 0.8:
        r.append({"prioridad": 3, "tema": "Orden", "titulo": "Sube la parte de tus compras con factura",
                  "detalle": f"Hoy {pct:.0%} de tus compras (en soles) tiene crédito fiscal. Empieza por los proveedores de la lista "
                             "de arriba: son pocos y concentran casi todo el monto.", "monto": 0.0})
    if confianza_minima is not None and confianza_minima < 0.9:
        r.append({"prioridad": 3, "tema": "Orden", "titulo": "Toma mejores fotos de tus comprobantes",
                  "detalle": "Una foto por comprobante, de día o con buena luz, de frente y con los cuatro bordes visibles. "
                             "Así el OCR no se equivoca y tu contador no te pide la foto de nuevo.", "monto": 0.0})
    r.append({"prioridad": 3, "tema": "Orden", "titulo": "Manda tus comprobantes cada semana, no a fin de mes",
              "detalle": "Un día fijo (por ejemplo, el lunes) subes las fotos de la semana. Si algo falta o tiene el RUC mal, "
                         "todavía estás a tiempo de pedir la corrección antes de declarar. Guarda los papeles en tres sobres: "
                         "facturas, boletas y físicas/contingencia.", "monto": 0.0})
    if impacto_2027 > 0:
        r.append({"prioridad": 2, "tema": "Futuro", "titulo": "Prepárate para la tasa de 2027",
                  "detalle": f"Si el próximo año la tasa especial pasa de {res.tasa_ventas:.1%} a 15 % (12 % IGV + 3 % IPM), con estas "
                             f"mismas ventas tu IGV de ventas subiría {_s(impacto_2027)} al mes. Cada sol de crédito que hoy "
                             "pierdes pesará más: conviene ordenar las facturas desde ahora.", "monto": impacto_2027})
    if vinculacion:
        r.append({"prioridad": 1, "tema": "Riesgo", "titulo": "Revisa la vinculación económica con tu contador",
                  "detalle": "Si compartes más de 30 % del capital con otra empresa del rubro, SUNAT puede sumar sus ingresos "
                             "y quitarte la tasa especial: reliquidarías a 18 % con intereses y multa.", "monto": 0.0})
    return sorted(r, key=lambda x: (x["prioridad"], -x["monto"]))


# ---------------------------------------------------------------- contenido educativo (estático)

REQUISITOS_CREDITO = """
Para que el IGV de una compra **te sirva para descontar** (crédito fiscal) se tienen que cumplir **todos** estos puntos:

1. **Comprobante que discrimine el IGV y a tu RUC:** factura (electrónica o física válida), ticket de máquina registradora con tu RUC, liquidación de compra o nota de crédito/débito vinculada. **La boleta de venta no da crédito fiscal**, aunque tenga tu DNI o RUC.
2. **Datos correctos:** tu RUC y razón social bien escritos. Si el RUC está mal, la factura es de otro contribuyente: el proveedor debe anularla y emitir una nueva.
3. **Compra del negocio:** tiene que ser costo o gasto del restaurante (insumos, alquiler, gas, empaques, comisiones). Compras de la casa o de la familia no dan crédito, aunque tengan factura a tu RUC.
4. **Producto gravado:** si el producto está exonerado (muchos frescos: verduras, frutas y, según la partida, pescado fresco), no hay IGV que descontar.
5. **Anotada en tu Registro de Compras (RCE del SIRE):** dentro de los 12 meses siguientes a la emisión. Si no está en la propuesta de SUNAT, hay que agregarla.
6. **Proveedor válido:** con RUC activo y habido, y comprobante válido. Puedes consultarlo en SUNAT ("Consulta de validez del comprobante de pago").
7. **Pagos grandes por banco:** si el pago supera el monto de bancarización (S/ 2,000 o US$ 500), usa transferencia, depósito o tarjeta. Si pagas en efectivo, pierdes el crédito.
8. **Detracciones:** algunos servicios (por ejemplo, ciertos mantenimientos o transporte) exigen depositar la detracción antes de usar el crédito.

_Referencia: Ley del IGV (arts. 18 y 19) y su reglamento. Los montos y supuestos pueden cambiar: confírmalos con tu contador._
"""

TALONARIOS = """
Desde que casi todos los negocios están obligados a emitir comprobantes **electrónicos**, un talonario físico **solo es válido en casos puntuales**:

- **Contingencia:** el proveedor es emisor electrónico pero su sistema o internet falló. Debe usar comprobantes físicos impresos por una imprenta autorizada y **luego informarlos a SUNAT**. Estos son los que suelen **faltar en la propuesta del SIRE** y hay que agregarlos al RCE.
- **Proveedores no obligados** a emitir electrónicamente (cada vez menos).

**Cómo saber si tu talonario sirve:**

1. Fíjate que tenga el **número de autorización de imprenta** y la fecha de impresión al pie.
2. Que diga **factura** (no boleta ni nota de venta), tu RUC y el IGV separado.
3. Consúltalo en SUNAT en **"Consulta de validez del comprobante de pago"** con el RUC del proveedor, la serie y el número.
4. Si el proveedor ya es emisor electrónico y te da talonarios "de siempre" sin estar en contingencia, **ese comprobante puede no ser válido**: pídele la factura electrónica.

Una nota de venta, un recibo hecho a mano o un "ticket" sin RUC **no son comprobantes de pago**: no dan crédito ni sustentan costo.
"""

LIQUIDACION = """
La **liquidación de compra** es un comprobante que **emites tú** (el comprador) cuando compras a una **persona natural sin RUC** que no puede darte comprobante, por ejemplo:

- productores o acopiadores de productos agropecuarios (la chacra que te vende limón o papa),
- pescadores artesanales,
- extracción de madera o productos silvestres, artesanía, y desechos o residuos.

**Cómo funciona (resumen):**

- Se emite de forma electrónica desde SUNAT Operaciones en Línea (SOL) con los datos del vendedor (DNI).
- Si el producto está **gravado**, tú **retienes el IGV** y lo pagas a SUNAT. Desde julio de 2026 ese pago se hace por SUNAT Virtual vinculado a cada liquidación y se declara en el Formulario Virtual 617. **El crédito fiscal recién nace cuando pagas ese IGV** y anotas la liquidación en tu registro de compras.
- Si el producto está **exonerado** (muchos frescos), no hay IGV, pero la liquidación **sí sustenta tu costo** para el Impuesto a la Renta.
- Para Renta se retiene **1,5 %** al vendedor (no aplica en operaciones de hasta S/ 700, y los productos primarios agropecuarios están excluidos de esta retención hasta el 31/12/2026).
- Solo se puede emitir si las operaciones de ese vendedor no superan el límite que fija SUNAT (75 UIT).

**¿Me conviene?** Si compras mucho a productores sin RUC y estás en el RMT, sí: conviertes un gasto sin sustento en un costo deducible. Si el vendedor tiene RUC (aunque sea del Nuevo RUS), **no aplica**: pídele su boleta o factura.

_Fuentes: SUNAT, Orientación "Declaración y pago IGV/IPM por operaciones en que se emite liquidación de compra"; RS 000047-2026/SUNAT; RS 234-2005/SUNAT. Verifica los detalles con tu contador._
"""


def a_excel(ruta_o_buffer, compras: pd.DataFrame, con: pd.DataFrame, res: Resumen, diag: Diagnostico | None,
            ventas: pd.DataFrame | None = None) -> None:
    """Exporta todo a Excel (pedido del contador entrevistado: auditable y en su herramienta)."""
    with pd.ExcelWriter(ruta_o_buffer, engine="openpyxl") as xw:
        pd.DataFrame(list(res.a_dict().items()), columns=["concepto", "valor"]).to_excel(xw, sheet_name="Resumen", index=False)
        con.drop(columns=["clave", "severidad"], errors="ignore").to_excel(xw, sheet_name="Compras_RCE", index=False)
        if ventas is not None:
            ventas.to_excel(xw, sheet_name="Ventas_RVIE", index=False)
        compras.to_excel(xw, sheet_name="Compras_leidas", index=False)
        if diag is not None:
            diag.lineas.to_excel(xw, sheet_name="Diagnostico_lineas", index=False)
            diag.por_categoria.to_excel(xw, sheet_name="Diagnostico_categorias", index=False)
            pd.DataFrame(diag.recomendaciones).to_excel(xw, sheet_name="Recomendaciones", index=False)
        pd.DataFrame({"formula": [
            "IGV a pagar = max(0, base de ventas x tasa de ventas - credito fiscal)",
            "IGV recuperable (gravado sin factura) = monto x 0.18 / 1.18",
            "Costo sin sustento (RMT) = monto sin comprobante (sin IGV si es gravado)",
            "Impacto Renta estimado = costo sin sustento x 10 % (primer tramo RMT)",
            "Impacto 2027 = base de ventas x (15 % - 10.5 %)",
            "Nota de credito = resta: en compras baja el credito fiscal; en ventas baja la base (montos en negativo)",
            "Nota de debito = suma: en compras sube el credito fiscal; en ventas sube la base",
            "IGV de un descuento sin nota = (total de la factura - notas - pagado) x IGV / total",
        ]}).to_excel(xw, sheet_name="Formulas", index=False)
