"""ValidaSire: revisa tu SIRE (compras y ventas) antes de aceptar la propuesta de SUNAT.

Flujo: fotos de comprobantes de compra -> OCR (PP-OCR) -> extracción (reglas + Claude) -> conciliación
contra la propuesta del RCE; reporte de ventas del POS -> conciliación contra la propuesta del RVIE;
IGV a pagar calculado con código determinista; diagnóstico del negocio.
Correr:  streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from backend import caso  # noqa: E402
from backend.conciliacion import (  # noqa: E402
    ESTADOS_ERROR, calcular, cargar_propuesta, conciliar, rce_ajustado,
)
from backend.diagnostico import LIQUIDACION, REQUISITOS_CREDITO, TALONARIOS, a_excel, diagnosticar  # noqa: E402
from backend.extraccion import cliente_claude, extraer, modelo_claude  # noqa: E402
from backend.ocr import leer  # noqa: E402
from backend.parametros import (  # noqa: E402
    ACTUALIZADO_AL, FUENTES, MINIMO_INGRESOS_RUBRO, PENDIENTES, TASAS, TOPE_TASA_ESPECIAL_UIT, UIT,
    VINCULACION_MAX_CAPITAL, dias_desde_revision, tasa,
)
from backend.reporte import mensaje_dueno, reporte_contador  # noqa: E402
from backend.ventas import ETIQUETA_VENTA, base_revisada, cargar_ventas, conciliar_ventas  # noqa: E402

MASCOTA = RAIZ / "assets" / "mascota"

SIN_COMPROBANTE_CASO = pd.DataFrame([
    {"descripcion": "Pescado fresco del terminal pesquero", "monto": 6500.0},
    {"descripcion": "Limón, cebolla y culantro del mercado", "monto": 2800.0},
    {"descripcion": "Hielo en bloque", "monto": 450.0},
    {"descripcion": "Bolsas y táperes del mercado", "monto": 300.0},
])
SIN_COMPROBANTE_VACIO = pd.DataFrame([{"descripcion": "", "monto": 0.0}])

st.set_page_config(page_title="ValidaSire · Revisa tu SIRE", page_icon=":material/fact_check:", layout="wide")

# Secretos: en local, archivo .env; en Streamlit Cloud, Settings > Secrets. Nunca en el repo.
try:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / ".env")
except ImportError:
    pass
try:
    if "ANTHROPIC_API_KEY" in st.secrets and not os.getenv("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = st.secrets["ANTHROPIC_API_KEY"]
except Exception:
    pass

# ---------------------------------------------------------------- estilo (tonos azules)
TONOS = [("#EFF6FF", "#1D4ED8"), ("#DBEAFE", "#1E3A8A"), ("#E0F2FE", "#0369A1"), ("#E0E7FF", "#3730A3"),
         ("#F0F9FF", "#0284C7")]
css_tonos = "\n".join(
    f'[class*="st-key-rec_t{i}_"] {{ background: {fondo}; border-left: 6px solid {borde} !important; }}'
    for i, (fondo, borde) in enumerate(TONOS))
st.html(f"""
<style>
.st-key-tu_negocio {{
  background: linear-gradient(135deg, #1E3A8A 0%, #1D4ED8 60%, #2563EB 100%);
  border-radius: 16px; padding: 1.1rem 1.4rem 1.3rem;
}}
.st-key-tu_negocio h3, .st-key-tu_negocio label, .st-key-tu_negocio label p,
.st-key-tu_negocio [data-testid="stMarkdownContainer"] p, .st-key-tu_negocio [data-testid="stCaptionContainer"],
.st-key-tu_negocio [data-testid="stCaptionContainer"] p {{ color: #FFFFFF !important; }}
.st-key-tu_negocio [data-testid="stCaptionContainer"] {{ opacity: .9; }}
.st-key-casos_especiales {{ background: rgba(255,255,255,.10); border-radius: 12px; padding: .8rem 1rem; }}
[class*="st-key-rec_t"] {{ border-radius: 10px; padding: .75rem 1rem; color: #0F172A; }}
{css_tonos}
.st-key-banner_ok {{ background: #EFF6FF; border: 2px solid #1D4ED8; border-radius: 16px; padding: .6rem 1rem; }}
.st-key-banner_alerta {{ background: #FFF7ED; border: 2px solid #EA580C; border-radius: 16px; padding: .6rem 1rem; }}
.st-key-banner_parcial {{ background: #F0F9FF; border: 2px solid #0284C7; border-radius: 16px; padding: .6rem 1rem; }}
.st-key-premium {{ background: linear-gradient(135deg, #EFF6FF, #DBEAFE); border: 1px solid #93C5FD;
  border-radius: 14px; padding: 1rem 1.2rem; }}
</style>
""")

ETIQUETA_ESTADO = {
    "OK": "✓ Todo bien",
    "FALTA_EN_PROPUESTA": "▲ SUNAT no la tiene",
    "SOBRA_EN_PROPUESTA": "▼ No subiste su comprobante",
    "DIFERENCIA_MONTO": "≠ SUNAT tiene otro monto",
    "DUPLICADO_EN_PROPUESTA": "▼ SUNAT la repite",
    "RUC_CLIENTE_ERRADO": "✕ Factura a otro RUC",
    "SIN_CREDITO": "• Boleta: no descuenta IGV",
    "ES_VENTA": "⇄ Es una venta, no una compra",
}
ICONO_TEMA = {"Dinero": ":material/savings:", "Riesgo": ":material/warning:", "Renta": ":material/account_balance:",
              "Orden": ":material/folder_open:", "Futuro": ":material/event_upcoming:"}
MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Setiembre",
         "Octubre", "Noviembre", "Diciembre"]


@st.cache_resource
def _claude():
    return cliente_claude()


@st.cache_data(show_spinner=False, max_entries=500)
def procesar(nombre: str, datos: bytes, ruc_propio: str, usar_claude: bool) -> dict:
    client = _claude() if usar_claude else None
    return caso.extraccion_a_dict(extraer(nombre, leer(datos), ruc_propio, client, modelo_claude()))


def soles(x: float) -> str:
    return f"S/ {x:,.2f}"


def leer_csv(archivo) -> pd.DataFrame:
    return pd.read_csv(archivo, dtype=str, sep=None, engine="python")


# ---------------------------------------------------------------- estado
ss = st.session_state
ss.setdefault("imagenes", {})        # nombre -> bytes
ss.setdefault("extracciones", [])    # lista de dicts
ss.setdefault("propuesta_df", None)  # propuesta RCE (compras)
ss.setdefault("ventas_df", None)     # reporte de ventas del POS
ss.setdefault("rvie_df", None)       # propuesta RVIE (ventas)
ss.setdefault("ventas_base", None)
ss.setdefault("es_caso", False)
ss.setdefault("sin_comprobante", SIN_COMPROBANTE_VACIO)


def cargar_caso(rapido: bool) -> None:
    ss.sin_comprobante = SIN_COMPROBANTE_CASO
    ss.pop("editor_sin_comprobante", None)
    ss.imagenes = {p.name: p.read_bytes() for p in caso.archivos_demo()}
    ss.propuesta_df = pd.read_csv(caso.DIR / "propuesta_rce.csv", dtype=str)
    ss.ventas_df = pd.read_csv(caso.DIR / "ventas_registro.csv", dtype=str)
    ss.rvie_df = pd.read_csv(caso.DIR / "propuesta_rvie.csv", dtype=str)
    ss.ventas_base = caso.base_ventas()
    ss.es_caso = True
    ss.extracciones = caso.cache() if rapido else []


# ---------------------------------------------------------------- barra lateral
with st.sidebar:
    st.subheader(":material/gavel: Normativa")
    dias = dias_desde_revision()
    st.caption(f"Información tributaria actualizada al **{ACTUALIZADO_AL:%d/%m/%Y}**.")
    if dias > 45:
        st.warning(f"Han pasado {dias} días desde la última revisión de normas: verifica las tasas antes de declarar.",
                   icon=":material/schedule:")
    with st.popover("Fuentes y pendientes", icon=":material/menu_book:", width="stretch"):
        for f in FUENTES:
            st.markdown(f"- **{f['norma']}**: {f['tema']}")
        if PENDIENTES:
            st.markdown("**Por confirmar:**\n" + "\n".join(f"- {p}" for p in PENDIENTES))
        st.caption("Cada lunes un monitor revisa las nuevas resoluciones de SUNAT y abre un aviso para revisarlas "
                   "(`scripts/monitor_normativa.py`). Ningún cambio de tasa se aplica sin revisión humana.")

    st.divider()
    st.subheader(":material/smart_toy: Lectura de comprobantes")
    hay_key = _claude() is not None
    usar_claude = st.toggle("Verificar con Claude", value=hay_key, disabled=not hay_key,
                            help="Claude lee el texto del OCR y devuelve un JSON validado. Si no coincide con las reglas, la fila se marca para revisar.")
    if not hay_key:
        st.caption("Sin API key: se usa OCR + reglas (gratis, sin internet).")
    else:
        st.caption(f"Modelo: `{modelo_claude()}`")
    rapido = st.toggle("Modo rápido (resultados guardados)", value=False,
                       help="Usa la lectura ya calculada del caso de ejemplo, por si el servidor está lento.")

if st.query_params.get("demo") == "1" and not ss.extracciones and not ss.es_caso:
    cargar_caso(rapido=True)

# ---------------------------------------------------------------- encabezado
h1, h2 = st.columns([4, 1], vertical_alignment="center")
with h1:
    st.title("ValidaSire")
    st.markdown("**Revisa tu SIRE antes de aceptar la propuesta de SUNAT.** Sube tus facturas de compra y tus ventas: "
                "te decimos qué falta, qué sobra, cuánto te toca pagar de IGV y dónde puedes ahorrar.")
    st.caption(f":material/verified: Con información tributaria actualizada al {ACTUALIZADO_AL:%d/%m/%Y}.")
h2.image(str(MASCOTA / "portada.png"), width=170)

# ---------------------------------------------------------------- tu negocio (azul)
with st.container(key="tu_negocio"):
    st.subheader(":material/storefront: Tu negocio")
    c1, c2, c3, c4 = st.columns([2, 1, 1, 2])
    ruc_propio = c1.text_input("RUC del negocio", value=caso.RUC_PROPIO, max_chars=11)
    anio = c2.selectbox("Año", sorted(TASAS), index=0)
    mes = c3.selectbox("Mes", MESES, index=9)
    regimen_renta = c4.radio("Régimen de renta", ["RMT", "RER"], horizontal=True,
                             format_func=lambda r: "MYPE Tributario (RMT)" if r == "RMT" else "Especial (RER)")

    with st.container(key="casos_especiales"):
        caso_especial = st.radio(
            "Casos especiales: ¿tu negocio tiene una tasa de IGV distinta?",
            ["ninguno", "restaurante"], index=1, horizontal=True,
            format_func=lambda c: f"Ninguno: tasa general {tasa(anio, 'general').total:.0%}" if c == "ninguno"
            else f"Restaurante, hotel o alojamiento turístico MYPE: {tasa(anio, 'restaurante_mype').total:.1%}",
        )
        vinc = False
        if caso_especial == "restaurante":
            vinc = st.toggle(f"¿Compartes más del {VINCULACION_MAX_CAPITAL:.0%} del capital con otra empresa?", value=False,
                             help="Es la vinculación económica (también cuenta un socio en común que controla las dos). "
                                  "Si existe, SUNAT suma los ingresos de ambas empresas para ver si pasas el tope.")
            v2, v3 = st.columns(2)
            pct_rubro = v2.slider("% de tus ingresos que viene del restaurante u hotel", 0, 100, 92) / 100
            etiqueta_ing = ("Ingresos del año pasado sumando a la empresa vinculada (S/)" if vinc
                            else "Ingresos del año pasado (S/)")
            ingresos = v3.number_input(etiqueta_ing, min_value=0.0, value=624_000.0, step=10_000.0)
            tope = TOPE_TASA_ESPECIAL_UIT * UIT[anio]
            checks = [
                (ingresos <= tope, f"Ingresos hasta {TOPE_TASA_ESPECIAL_UIT:,} UIT ({soles(tope)})"
                 + (" sumando a la vinculada" if vinc else "")),
                (pct_rubro >= MINIMO_INGRESOS_RUBRO, f"Al menos {MINIMO_INGRESOS_RUBRO:.0%} de ingresos del rubro"),
            ]
            st.markdown(" · ".join(f"{':material/check_circle:' if ok else ':material/cancel:'} {t}" for ok, t in checks))
            if all(ok for ok, _ in checks):
                regimen = "restaurante_mype"
                st.caption(f"Aplicas la tasa especial: IGV {tasa(anio, regimen).igv:.1%} + IPM {tasa(anio, regimen).ipm:.1%} "
                           f"= {tasa(anio, regimen).total:.1%} en tus ventas (Ley 31556, modificada por la Ley 32219).")
            else:
                regimen = "general"
                st.markdown(f":material/warning: **No cumples los requisitos de la tasa especial: tus ventas van al "
                            f"{tasa(anio, 'general').total:.0%}.** Si ya declaraste con la tasa especial, habla con tu contador.")
        else:
            regimen = "general"
            st.caption(f"Ventas al {tasa(anio, 'general').total:.0%} (IGV {tasa(anio, 'general').igv:.1%} + IPM "
                       f"{tasa(anio, 'general').ipm:.1%}).")
        st.caption("Otros casos especiales que ValidaSire todavía no calcula: venta de arroz pilado (IVAP), empresas "
                   "en la Amazonía con exoneración, exportadores. Si es tu caso, consulta a tu contador.")

periodo = f"{anio}-{MESES.index(mes) + 1:02d}"
st.write("")

t_rev, t_diag, t_cont = st.tabs([":material/fact_check: Revisión del SIRE", ":material/insights: Diagnóstico del negocio",
                                 ":material/folder_shared: Para tu contador"])

compras = con = res = con_ventas = diag = None

# ================================================================ pestaña 1: revisión
with t_rev:
    i1, i2 = st.columns([3, 2], vertical_alignment="center")
    i1.markdown("El SIRE tiene **dos registros** y los revisamos por separado para que no se mezclen:\n"
                "- **RCE · Compras:** lo que te venden tus proveedores (sus facturas a tu RUC).\n"
                "- **RVIE · Ventas:** lo que tú le vendes a tus clientes (tus boletas y facturas).")
    if i2.button("Usar caso de ejemplo: Cevichería Mar y Sazón (octubre 2026)", icon=":material/science:",
                 width="stretch", key="btn_caso"):
        cargar_caso(rapido)
        st.rerun()

    # ------------------------------------------------ paso 1: compras
    with st.container(border=True):
        st.subheader(":material/shopping_cart: 1 · Compras → RCE")
        st.caption("Sube las facturas y boletas **que te dieron tus proveedores**. Tus propias facturas de venta no van aquí.")
        col_a, col_b = st.columns([3, 2])
        subidos = col_a.file_uploader("Fotos o imágenes de tus comprobantes de compra", type=["png", "jpg", "jpeg"],
                                      accept_multiple_files=True)
        archivo_prop = col_b.file_uploader("Propuesta del RCE descargada del SIRE (CSV o TXT)", type=["csv", "txt"])
        if archivo_prop is not None:
            ss.propuesta_df = leer_csv(archivo_prop)
        if ss.propuesta_df is not None:
            col_b.caption(f":material/check: Propuesta del RCE cargada: {len(ss.propuesta_df)} compras.")
        if subidos:
            nuevos = {f.name: f.getvalue() for f in subidos}
            if set(nuevos) != set(ss.imagenes) or ss.es_caso:
                ss.imagenes, ss.extracciones, ss.es_caso = nuevos, [], False

        if ss.imagenes and not ss.extracciones:
            resultados = []
            with st.status(f"Leyendo {len(ss.imagenes)} comprobantes…", expanded=True) as estado:
                barra = st.progress(0.0)
                for i, (nombre, datos) in enumerate(ss.imagenes.items(), start=1):
                    r = procesar(nombre, datos, ruc_propio, usar_claude)
                    resultados.append(r)
                    barra.progress(i / len(ss.imagenes), text=f"{nombre}: {r['comprobante']['serie_numero'] or '¿?'} "
                                   f"· confianza {r['confianza_ocr']:.0%}")
                estado.update(label=f"{len(resultados)} comprobantes leídos", state="complete", expanded=False)
            ss.extracciones = resultados

        if ss.extracciones:
            filas = []
            for e in ss.extracciones:
                c = e["comprobante"]
                filas.append({
                    "incluir": True, "archivo": e["archivo"], **{k: c[k] for k in (
                        "tipo_comprobante", "serie_numero", "fecha_emision", "ruc_emisor", "razon_social_emisor",
                        "ruc_cliente", "descripcion", "base_gravada", "igv", "total")},
                    "contingencia": c.get("contingencia", False),
                    "confianza": e["confianza_ocr"],
                    "revisar": "; ".join(e["observaciones"] + [f"Claude y reglas difieren en {', '.join(e['discrepancias_motores'])}"]
                                         if e["discrepancias_motores"] else e["observaciones"]),
                })
            tabla = pd.DataFrame(filas)
            n_rev = int((tabla["revisar"] != "").sum())
            st.caption(f"Lectura: {tabla['confianza'].mean():.0%} de confianza media · {n_rev} fila(s) para revisar · "
                       f"motor: {ss.extracciones[0]['motor']}. Puedes corregir cualquier celda o desmarcar una compra "
                       "que no es del negocio.")
            compras = st.data_editor(
                tabla, hide_index=True, key="editor_compras", height=300,
                disabled=["archivo", "confianza", "revisar"],
                column_config={
                    "incluir": st.column_config.CheckboxColumn("Del negocio", width="small"),
                    "archivo": st.column_config.TextColumn("Archivo", width="small"),
                    "tipo_comprobante": st.column_config.SelectboxColumn("Tipo", options=["Factura", "Boleta", "Nota de credito", "Otro"]),
                    "serie_numero": "Serie-número", "fecha_emision": "Fecha", "ruc_emisor": "RUC proveedor",
                    "razon_social_emisor": "Proveedor", "ruc_cliente": "RUC cliente", "descripcion": "Producto",
                    "base_gravada": st.column_config.NumberColumn("Base", format="%.2f"),
                    "igv": st.column_config.NumberColumn("IGV", format="%.2f"),
                    "total": st.column_config.NumberColumn("Total", format="%.2f"),
                    "contingencia": st.column_config.CheckboxColumn("Física", width="small"),
                    "confianza": st.column_config.ProgressColumn("Confianza OCR", min_value=0, max_value=1, format="percent"),
                    "revisar": st.column_config.TextColumn("Revisar", width="medium"),
                },
            )
            with st.expander("Ver una imagen y lo que leyó el OCR", icon=":material/image_search:"):
                elegido = st.selectbox("Comprobante", [e["archivo"] for e in ss.extracciones],
                                       index=next((i for i, e in enumerate(ss.extracciones) if "foto" in e["archivo"]), 0))
                ci, ct = st.columns([1, 1])
                if elegido in ss.imagenes:
                    ci.image(ss.imagenes[elegido], alt=f"Comprobante {elegido}")
                ext = next(e for e in ss.extracciones if e["archivo"] == elegido)
                ct.code(ext["texto_ocr"], language=None)
                ct.json(ext["comprobante"], expanded=False)

    # ------------------------------------------------ paso 2: ventas
    with st.container(border=True):
        st.subheader(":material/point_of_sale: 2 · Ventas → RVIE")
        st.caption("Tus ventas **no se suben como fotos**: ya son electrónicas. Descarga el reporte del mes de tu sistema "
                   "de facturación o POS y la propuesta del RVIE del SIRE.")
        v1, v2 = st.columns(2)
        archivo_ventas = v1.file_uploader("Reporte de ventas de tu sistema (CSV)", type=["csv", "txt"])
        archivo_rvie = v2.file_uploader("Propuesta del RVIE descargada del SIRE (CSV o TXT)", type=["csv", "txt"])
        if archivo_ventas is not None:
            ss.ventas_df = leer_csv(archivo_ventas)
        if archivo_rvie is not None:
            ss.rvie_df = leer_csv(archivo_rvie)
        if ss.ventas_df is not None:
            v1.caption(f":material/check: Reporte de ventas cargado: {len(ss.ventas_df)} filas.")
        if ss.rvie_df is not None:
            v2.caption(f":material/check: Propuesta del RVIE cargada: {len(ss.rvie_df)} filas.")

        base_ventas = 0.0
        try:
            if ss.ventas_df is not None:
                reg = cargar_ventas(ss.ventas_df)
                base_ventas = base_revisada(reg)
                if ss.rvie_df is not None:
                    con_ventas = conciliar_ventas(reg, cargar_ventas(ss.rvie_df))
        except ValueError as err:
            st.error(str(err), icon=":material/error:")
        if ss.ventas_df is None:
            with st.expander("¿No tienes los archivos? Escribe solo el total de ventas del mes", icon=":material/edit:"):
                base_ventas = st.number_input("Base gravada de ventas del mes, sin IGV (S/)", min_value=0.0, step=100.0,
                                              value=float(ss.ventas_base or 0.0), format="%.2f")
                st.caption("Así calculamos el IGV, pero no podemos validar tu RVIE.")
        else:
            st.caption(f"Base de ventas del mes según tu sistema: **{soles(base_ventas)}**.")

    # ------------------------------------------------ paso 3: resultado
    listo = compras is not None and ss.propuesta_df is not None and base_ventas > 0
    with st.container(border=True):
        st.subheader(":material/task_alt: 3 · Resultado")
        if not listo:
            st.info("Sube tus comprobantes de compra, la propuesta del RCE y tus ventas (o usa el caso de ejemplo).",
                    icon=":material/arrow_upward:")
        else:
            try:
                prop = cargar_propuesta(ss.propuesta_df)
            except ValueError as err:
                st.error(str(err), icon=":material/error:")
                st.stop()
            con = conciliar(compras, prop, ruc_propio)
            res = calcular(con, base_ventas, anio, periodo, regimen, regimen_renta=regimen_renta)

            err_rce = con[con["estado"].isin(ESTADOS_ERROR)]
            err_rvie = con_ventas[con_ventas["estado"] != "OK"] if con_ventas is not None else None

            # --- mensaje de la mascota
            if err_rce.empty and err_rvie is not None and err_rvie.empty:
                clave_b, img, titulo, texto = ("banner_ok", "ok.png", "¡Cuy-dado perfecto!",
                                               "Tu RVIE y RCE del SIRE están sincronizados y sin errores.")
            elif not err_rce.empty or (err_rvie is not None and not err_rvie.empty):
                n = len(err_rce) + (len(err_rvie) if err_rvie is not None else 0)
                clave_b, img, titulo, texto = (
                    "banner_alerta", "alerta.png", "¡Ojo con el SIRE!",
                    "Hay facturas que no cuadran con tu registro. Revísalas antes de aceptar la propuesta. "
                    f"({n} por revisar: {len(err_rce)} en compras"
                    + (f", {len(err_rvie)} en ventas)" if err_rvie is not None else "; ventas sin validar)"))
            else:
                clave_b, img, titulo, texto = ("banner_parcial", "portada.png", "Tu RCE está sin errores",
                                               "Sube tu reporte de ventas y la propuesta del RVIE para validar también tus ventas.")
            with st.container(key=clave_b):
                b1, b2 = st.columns([1, 6], vertical_alignment="center")
                b1.image(str(MASCOTA / img), width=110)
                b2.markdown(f"### {titulo}\n{texto}")

            # --- las 4 cifras, explicadas
            st.markdown("#### ¿Cuánto IGV te toca pagar?")
            faltan = con[con["estado"] == "FALTA_EN_PROPUESTA"]
            sobran = con[con["estado"].isin(["SOBRA_EN_PROPUESTA", "DUPLICADO_EN_PROPUESTA"])]
            m1, m2, m3, m4 = st.columns(4)
            with m1:
                st.metric("Si aceptas la propuesta", soles(res.igv_a_pagar_si_acepta_propuesta), border=True)
                st.caption("Lo que pagarías con las compras que SUNAT tiene registradas.")
            with m2:
                st.metric("Lo que te toca pagar", soles(res.igv_a_pagar_revisado), border=True,
                          delta=(f"{'-' if res.diferencia > 0 else '+'}{soles(abs(res.diferencia))} vs. la propuesta"
                                 if res.diferencia else None), delta_color="inverse")
                st.caption("Calculado con tus comprobantes reales, después de corregir la propuesta.")
            with m3:
                st.metric("IGV que SUNAT no tenía", soles(res.credito_recuperado), border=True)
                st.caption(f"{len(faltan)} factura(s) tuya(s) que no estaban en la propuesta"
                           + (f" (p. ej. {faltan.iloc[0]['proveedor'].title()})" if len(faltan) else "")
                           + ". Si no las agregas, pierdes este descuento." if len(faltan)
                           else "Todas tus facturas estaban en la propuesta.")
            with m4:
                st.metric("IGV que no te corresponde", soles(res.credito_indebido_evitado), border=True)
                st.caption(f"Compra(s) en la propuesta sin comprobante subido"
                           + (f" (p. ej. {sobran.iloc[0]['proveedor'].title()})" if len(sobran) else "")
                           + ". Si no son del negocio y las aceptas, SUNAT puede cobrarte ese IGV con multa e intereses."
                           if len(sobran) else "La propuesta no trae compras ajenas.")
            if res.diferencia > 0:
                st.markdown(f":material/lightbulb: **En resumen:** si aceptas la propuesta tal cual, pagas "
                            f"**{soles(res.diferencia)} de más** este mes"
                            + (" y además te descuentas IGV que no te corresponde." if res.credito_indebido_evitado else "."))
            elif res.diferencia < 0:
                st.markdown(f":material/lightbulb: **En resumen:** la propuesta te hace pagar **{soles(-res.diferencia)} de menos**, "
                            "pero con descuentos que no te corresponden: corrígela para evitar multas.")

            # --- hallazgos de compras
            st.markdown("#### Compras (RCE): qué revisar")
            hallazgos = con[con["estado"] != "OK"]
            ver_todo = st.toggle("Ver también las que están bien", value=False)
            vista = (con if ver_todo else hallazgos).copy()
            vista["igv_en_juego"] = vista.apply(
                lambda r: abs(r["igv_comprobante"] - r["igv_propuesta"]) if r["estado"] == "DIFERENCIA_MONTO"
                else max(r["igv_comprobante"], r["igv_propuesta"]), axis=1)
            vista["producto"] = vista.apply(
                lambda r: r["descripcion"] or ("— (SUNAT no informa el detalle; no subiste el comprobante)"
                                               if r["estado"] in ("SOBRA_EN_PROPUESTA", "DUPLICADO_EN_PROPUESTA") else "—"),
                axis=1)
            st.table(
                vista.assign(estado=lambda d: d["estado"].map(ETIQUETA_ESTADO),
                             igv_en_juego=lambda d: d["igv_en_juego"].map(lambda v: f"{v:,.2f}"))
                [["estado", "serie_numero", "proveedor", "producto", "igv_en_juego", "que_hacer"]]
                .rename(columns={"estado": "Estado", "serie_numero": "Comprobante", "proveedor": "Proveedor",
                                 "producto": "Producto", "igv_en_juego": "IGV (S/)", "que_hacer": "Qué hacer"}),
                border="horizontal", hide_index=True, alt="Compras que no coinciden con la propuesta del RCE",
            )

            # --- ventas
            st.markdown("#### Ventas (RVIE)")
            if con_ventas is None:
                st.caption("No cargaste el reporte de ventas y la propuesta del RVIE: tus ventas no se validaron.")
            elif err_rvie.empty:
                st.markdown(f":material/check_circle: Tus {len(con_ventas)} comprobantes y resúmenes diarios de venta "
                            f"coinciden con la propuesta del RVIE (base {soles(base_ventas)}).")
            else:
                st.table(err_rvie.assign(estado=lambda d: d["estado"].map(ETIQUETA_VENTA),
                                         diferencia_base=lambda d: d["diferencia_base"].map(lambda v: f"{v:+,.2f}"))
                         [["estado", "serie_numero", "fecha_emision", "cliente", "diferencia_base", "que_hacer"]]
                         .rename(columns={"estado": "Estado", "serie_numero": "Comprobante", "fecha_emision": "Fecha",
                                          "cliente": "Cliente", "diferencia_base": "Diferencia en base (S/)",
                                          "que_hacer": "Qué hacer"}),
                         border="horizontal", hide_index=True, alt="Ventas que no coinciden con la propuesta del RVIE")

            # --- gráfico
            g = pd.DataFrame([
                {"escenario": "Si aceptas la propuesta", "concepto": "1. IGV que descuentas (crédito)", "soles": res.credito_fiscal_segun_propuesta},
                {"escenario": "Si aceptas la propuesta", "concepto": "2. IGV a pagar", "soles": res.igv_a_pagar_si_acepta_propuesta},
                {"escenario": "Revisado con ValidaSire", "concepto": "1. IGV que descuentas (crédito)", "soles": res.credito_fiscal_revisado},
                {"escenario": "Revisado con ValidaSire", "concepto": "2. IGV a pagar", "soles": res.igv_a_pagar_revisado},
            ])
            grafico = alt.Chart(g).mark_bar(height=28).encode(
                y=alt.Y("escenario:N", title=None, axis=alt.Axis(labelLimit=220)),
                x=alt.X("soles:Q", stack="zero",
                        title=f"IGV de tus ventas = {soles(res.igv_debito_ventas)} ({res.tasa_ventas:.1%}), dividido en lo que descuentas y lo que pagas"),
                color=alt.Color("concepto:N", title=None, legend=alt.Legend(orient="bottom"),
                                scale=alt.Scale(range=["#93C5FD", "#1D4ED8"])),
                order=alt.Order("concepto:N"),
                tooltip=["escenario", "concepto", alt.Tooltip("soles:Q", format=",.2f")],
            ).properties(height=alt.Step(48))
            st.altair_chart(grafico, alt="IGV de ventas dividido en crédito fiscal e IGV a pagar, en los dos escenarios")
            st.caption(f"{'Pago a cuenta de renta (RMT)' if regimen_renta == 'RMT' else 'Cuota de renta (RER)'}: "
                       f"{soles(res.pago_a_cuenta_renta)} ({res.tasa_pago_a_cuenta:.1%} de tus ventas).")

            # --- servicio de pago
            with st.container(key="premium"):
                p1, p2 = st.columns([4, 1], vertical_alignment="center")
                p1.markdown("**:material/workspace_premium: ¿Quieres que lo dejemos listo por ti? · ValidaSire Pro**  \n"
                            "Si decides **complementar o reemplazar** la propuesta, sube tus registros de compras y ventas y "
                            "un contador aliado te prepara el archivo para importarlo al SIRE, revisado y con sustento. "
                            "Tú lo subes con tu Clave SOL: **nunca te la pedimos**. Referencial: S/ 59 por periodo.")
                if p2.button("Me interesa", icon=":material/arrow_forward:", type="primary", width="stretch"):
                    st.toast("¡Gracias! Este es un demo: no se cobra ni se guarda ningún dato.", icon=":material/info:")

# ================================================================ pestaña 2: diagnóstico
with t_diag:
    if res is None:
        st.info("Primero completa la revisión del SIRE (o usa el caso de ejemplo).", icon=":material/arrow_back:")
    else:
        st.markdown("Además de lo que ya revisamos, ¿qué compras haces **sin comprobante** en un mes normal? "
                    "(mercado, terminal, hielo, carbón, bolsas…). Un estimado basta.")
        sin_comp = st.data_editor(
            ss.sin_comprobante, num_rows="dynamic", hide_index=True, key="editor_sin_comprobante",
            column_config={"descripcion": st.column_config.TextColumn("Qué compras", width="large"),
                           "monto": st.column_config.NumberColumn("Monto al mes (S/)", min_value=0, format="%.2f")},
        )
        confianza_min = min(e["confianza_ocr"] for e in ss.extracciones)
        diag = diagnosticar(compras, sin_comp, con, res, anio, regimen_renta, confianza_min, vinc)

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("IGV que podrías recuperar al mes", soles(diag.igv_recuperable_mes), border=True,
                  help="Compras gravadas que hoy haces con boleta o sin comprobante: monto × 18/118, si consigues el mismo precio total con factura.")
        k2.metric("En un año", soles(diag.igv_recuperable_anio), border=True)
        k3.metric("Compras con factura válida", f"{diag.pct_compras_con_credito:.0%}", border=True,
                  help="Parte de tus compras del mes (en soles) que tiene factura válida y está en el registro.")
        k4.metric("Renta extra al mes por compras sin sustento", soles(diag.impuesto_renta_estimado_mes), border=True,
                  help="Solo RMT: costo sin comprobante × 10 % (primer tramo, estimado conservador). En el RER es 0.")

        if not diag.lineas.empty:
            st.markdown("#### Dónde se te escapa más dinero")
            vista_d = diag.lineas.assign(
                monto=lambda d: d["monto"].map(lambda v: f"{v:,.2f}"),
                igv_recuperable=lambda d: d["igv_recuperable"].map(lambda v: f"{v:,.2f}"),
            )[["descripcion", "origen", "tratamiento", "monto", "igv_recuperable", "que_hacer"]].rename(columns={
                "descripcion": "Compra", "origen": "Cómo la compras", "tratamiento": "IGV",
                "monto": "Monto (S/)", "igv_recuperable": "IGV recuperable (S/)", "que_hacer": "Qué hacer"})
            st.table(vista_d, border="horizontal", hide_index=True, alt="Compras sin crédito fiscal y cuánto IGV se podría recuperar")

        st.markdown("#### Recomendaciones")
        cols = st.columns(2)
        for i, rec in enumerate(diag.recomendaciones):
            monto_txt = f" · **{soles(rec['monto'])} al mes**" if rec["monto"] else ""
            with cols[i % 2].container(key=f"rec_t{i % len(TONOS)}_{i}"):
                st.markdown(f"{ICONO_TEMA.get(rec['tema'], ':material/lightbulb:')} **{rec['titulo']}**{monto_txt}")
                st.caption(rec["detalle"])

        st.markdown("#### Aprende")
        c_req, c_tal, c_liq = st.tabs(["¿Qué compras me sirven para descontar IGV?", "¿Mi talonario sirve?",
                                       "Liquidación de compra"])
        c_req.markdown(REQUISITOS_CREDITO)
        c_tal.markdown(TALONARIOS)
        c_liq.markdown(LIQUIDACION)

# ================================================================ pestaña 3: para tu contador
with t_cont:
    if res is None:
        st.info("Primero completa la revisión del SIRE (o usa el caso de ejemplo).", icon=":material/arrow_back:")
    else:
        st.markdown("Todo lo que tu contador necesita para declarar, en un solo lugar. Cada archivo dice qué contiene.")
        buffer = io.BytesIO()
        a_excel(buffer, compras, con, res, diag, con_ventas)
        with st.container(border=True):
            d1, d2 = st.columns([3, 1], vertical_alignment="center")
            d1.markdown("**:material/table_view: Expediente completo (Excel)**  \nUna hoja por tema: resumen del IGV, "
                        "compras contra el RCE, ventas contra el RVIE, comprobantes leídos, diagnóstico, recomendaciones "
                        "y las fórmulas usadas, para que lo pueda auditar.")
            d2.download_button("Descargar Excel", buffer.getvalue(), file_name=f"validasire_{periodo}.xlsx",
                               icon=":material/download:", type="primary", width="stretch",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        with st.container(border=True):
            d1, d2 = st.columns([3, 1], vertical_alignment="center")
            d1.markdown("**:material/playlist_add_check: Compras que sí van con crédito (CSV)**  \nLa lista final de "
                        "facturas para tu RCE: las de la propuesta que están bien más las que SUNAT no tenía. Sirve "
                        "para complementar o reemplazar la propuesta.")
            d2.download_button("Descargar CSV", rce_ajustado(con).to_csv(index=False).encode("utf-8"),
                               file_name=f"rce_ajustado_{periodo}.csv", icon=":material/download:", width="stretch")
        with st.container(border=True):
            d1, d2 = st.columns([3, 1], vertical_alignment="center")
            d1.markdown("**:material/description: Resumen de la revisión (texto)**  \nIGV a pagar con y sin la revisión "
                        "y la lista de hallazgos de compras y ventas, para leer en el celular.")
            d2.download_button("Descargar resumen", reporte_contador(res, con, ruc_propio, con_ventas).encode("utf-8"),
                               file_name=f"resumen_igv_{periodo}.md", icon=":material/download:", width="stretch")
        with st.container(border=True):
            st.markdown("**:material/chat: Mensaje para el dueño (WhatsApp)**  \nSi eres el contador, cópialo y envíalo.")
            st.code(mensaje_dueno(res, con), language=None, wrap_lines=True)

st.divider()
st.caption("Datos de ejemplo 100 % ficticios. ValidaSire es un proyecto independiente: no es un producto de SUNAT ni "
           "está afiliado a ella, y no reemplaza la asesoría de un contador. Mascota Don Cuy © Britney Miranda Gutiérrez.")
