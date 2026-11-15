"""Cuadra: revisa tu IGV del mes antes de aceptar la propuesta del SIRE.

Flujo: fotos de comprobantes -> OCR (PP-OCR) -> extracción (reglas + Claude) -> conciliación
contra la propuesta del RCE -> IGV a pagar calculado con código determinista.
Correr:  streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import caso  # noqa: E402
from backend.conciliacion import (  # noqa: E402
    base_ventas_desde_csv, calcular, cargar_propuesta, conciliar, rce_ajustado,
)
from backend.extraccion import cliente_claude, extraer, modelo_claude  # noqa: E402
from backend.ocr import leer  # noqa: E402
from backend.diagnostico import LIQUIDACION, REQUISITOS_CREDITO, TALONARIOS, a_excel, diagnosticar  # noqa: E402
from backend.parametros import (  # noqa: E402
    ACTUALIZADO_AL, FUENTES, MINIMO_INGRESOS_RUBRO, PENDIENTES, TASAS, TOPE_TASA_ESPECIAL_UIT, UIT,
    VINCULACION_MAX_CAPITAL, dias_desde_revision, tasa,
)
from backend.reporte import mensaje_dueno, reporte_contador  # noqa: E402

import io  # noqa: E402

SIN_COMPROBANTE_CASO = pd.DataFrame([
    {"descripcion": "Pescado fresco del terminal pesquero", "monto": 6500.0},
    {"descripcion": "Limón, cebolla y culantro del mercado", "monto": 2800.0},
    {"descripcion": "Hielo en bloque", "monto": 450.0},
    {"descripcion": "Bolsas y táperes del mercado", "monto": 300.0},
])
SIN_COMPROBANTE_VACIO = pd.DataFrame([{"descripcion": "", "monto": 0.0}])

st.set_page_config(page_title="Cuadra · IGV sin sorpresas", page_icon=":material/receipt_long:", layout="wide")

# Secretos: en local, archivo .env; en Streamlit Cloud, Settings > Secrets. Nunca en el repo.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass
try:
    if "ANTHROPIC_API_KEY" in st.secrets and not os.getenv("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = st.secrets["ANTHROPIC_API_KEY"]
except Exception:
    pass

ETIQUETA_ESTADO = {
    "OK": "✓ Coincide",
    "FALTA_EN_PROPUESTA": "▲ Falta en la propuesta",
    "SOBRA_EN_PROPUESTA": "▼ Sobra en la propuesta",
    "DIFERENCIA_MONTO": "≠ Monto distinto",
    "DUPLICADO_EN_PROPUESTA": "▼ Duplicado",
    "RUC_CLIENTE_ERRADO": "✕ A nombre de otro RUC",
    "SIN_CREDITO": "• Boleta sin crédito",
}

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


# ---------------------------------------------------------------- estado
ss = st.session_state
ss.setdefault("imagenes", {})       # nombre -> bytes
ss.setdefault("extracciones", [])   # lista de dicts
ss.setdefault("propuesta_df", None)
ss.setdefault("ventas_base", None)
ss.setdefault("es_caso", False)
ss.setdefault("sin_comprobante", SIN_COMPROBANTE_VACIO)


def cargar_caso(rapido: bool) -> None:
    ss.sin_comprobante = SIN_COMPROBANTE_CASO
    ss.pop("editor_sin_comprobante", None)
    ss.imagenes = {p.name: p.read_bytes() for p in caso.archivos_demo()}
    ss.propuesta_df = pd.read_csv(caso.DIR / "propuesta_rce.csv", dtype=str)
    ss.ventas_base = caso.base_ventas()
    ss.es_caso = True
    ss.extracciones = caso.cache() if rapido else []


# ---------------------------------------------------------------- barra lateral
with st.sidebar:
    st.subheader(":material/storefront: Tu negocio")
    ruc_propio = st.text_input("RUC del negocio", value=caso.RUC_PROPIO, max_chars=11)
    c1, c2 = st.columns(2)
    anio = c1.selectbox("Año", sorted(TASAS), index=0)
    mes = c2.selectbox("Mes", MESES, index=9)
    regimen = st.segmented_control(
        "Tasa de tus ventas", ["restaurante_mype", "general"], default="restaurante_mype", required=True,
        format_func=lambda r: f"Restaurante MYPE {tasa(anio, 'restaurante_mype').total:.1%}"
        if r == "restaurante_mype" else f"General {tasa(anio, 'general').total:.0%}",
    )
    st.caption(f"{anio}: IGV {tasa(anio, regimen).igv:.1%} + IPM {tasa(anio, regimen).ipm:.1%}. "
               "Las tasas viven en `data/normativa/parametros.json`, no en el LLM.")
    regimen_renta = st.segmented_control("Régimen de renta", ["RMT", "RER"], default="RMT", required=True,
                                         help="RMT: Régimen MYPE Tributario. RER: Régimen Especial de Renta.")

    st.divider()
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
    st.subheader(":material/smart_toy: Lectura")
    hay_key = _claude() is not None
    usar_claude = st.toggle("Verificar con Claude", value=hay_key, disabled=not hay_key,
                            help="Claude lee el texto del OCR y devuelve un JSON validado. Si no coincide con las reglas, la fila se marca para revisar.")
    if not hay_key:
        st.caption("Sin API key: se usa OCR + reglas (gratis, sin internet).")
    else:
        st.caption(f"Modelo: `{modelo_claude()}`")
    rapido = st.toggle("Modo rápido (resultados guardados)", value=False,
                       help="Usa la lectura ya calculada del caso de ejemplo, por si el servidor está lento.")

periodo = f"{anio}-{MESES.index(mes) + 1:02d}"

# ---------------------------------------------------------------- encabezado
st.title(":material/receipt_long: Cuadra")
st.markdown("**Revisa tu IGV del mes en 5 minutos, antes de aceptar la propuesta del SIRE.** "
            "Sube las fotos de tus facturas de compra y la propuesta del RCE; Cuadra te dice qué falta, qué sobra, cuánto pagar "
            "y dónde puedes ahorrar.")
st.caption(f":material/verified: Con información tributaria actualizada al {ACTUALIZADO_AL:%d/%m/%Y}.")

if st.query_params.get("demo") == "1" and not ss.extracciones and not ss.es_caso:
    cargar_caso(rapido=True)

# ---------------------------------------------------------------- paso 1
with st.container(border=True):
    st.subheader("1 · Tus comprobantes de compra")
    col_a, col_b = st.columns([3, 2], vertical_alignment="bottom")
    subidos = col_a.file_uploader("Fotos o imágenes de facturas y boletas", type=["png", "jpg", "jpeg"],
                                  accept_multiple_files=True)
    if col_b.button("Usar caso de ejemplo: Cevichería Mar y Sazón (octubre 2026)", icon=":material/science:",
                    width="stretch", key="btn_caso"):
        cargar_caso(rapido)
        st.rerun()
    if subidos:
        nuevos = {f.name: f.getvalue() for f in subidos}
        if set(nuevos) != set(ss.imagenes) or ss.es_caso:
            ss.imagenes, ss.extracciones, ss.es_caso = nuevos, [], False

    if ss.imagenes and not ss.extracciones:
        resultados = []
        with st.status(f"Leyendo {len(ss.imagenes)} comprobantes con OCR…", expanded=True) as estado:
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
        st.caption(f"Lectura: {tabla['confianza'].mean():.0%} de confianza media del OCR · "
                   f"{n_rev} fila(s) para revisar · motor: {ss.extracciones[0]['motor']}. "
                   "Puedes corregir cualquier celda o desmarcar una compra que no es del negocio.")
        compras = st.data_editor(
            tabla, hide_index=True, key="editor_compras", height=300,
            disabled=["archivo", "confianza", "revisar"],
            column_config={
                "incluir": st.column_config.CheckboxColumn("Del negocio", width="small"),
                "archivo": st.column_config.TextColumn("Archivo", width="small"),
                "tipo_comprobante": st.column_config.SelectboxColumn("Tipo", options=["Factura", "Boleta", "Nota de credito", "Otro"]),
                "serie_numero": "Serie-número", "fecha_emision": "Fecha", "ruc_emisor": "RUC proveedor",
                "razon_social_emisor": "Proveedor", "ruc_cliente": "RUC cliente", "descripcion": "Descripción",
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

# ---------------------------------------------------------------- paso 2
with st.container(border=True):
    st.subheader("2 · La propuesta de SUNAT y tus ventas")
    c1, c2 = st.columns(2)
    archivo_prop = c1.file_uploader("Propuesta del RCE exportada del SIRE (CSV)", type=["csv", "txt"])
    if archivo_prop is not None:
        ss.propuesta_df = pd.read_csv(archivo_prop, dtype=str, sep=None, engine="python")
    archivo_ventas = c2.file_uploader("Resumen de ventas del mes (CSV, opcional)", type=["csv"])
    if archivo_ventas is not None:
        ss.ventas_base = base_ventas_desde_csv(pd.read_csv(archivo_ventas))
    base_ventas = c2.number_input("Base gravada de ventas del mes (S/)", min_value=0.0, step=100.0,
                                  value=float(ss.ventas_base or 0.0), format="%.2f")
    if ss.propuesta_df is not None:
        c1.caption(f"Propuesta cargada: {len(ss.propuesta_df)} comprobantes de compra.")

# ---------------------------------------------------------------- paso 3
listo = bool(ss.extracciones) and ss.propuesta_df is not None and base_ventas > 0
with st.container(border=True):
    st.subheader("3 · Resultado de la revisión")
    if not listo:
        st.info("Sube tus comprobantes, la propuesta del RCE y tus ventas (o usa el caso de ejemplo).",
                icon=":material/arrow_upward:")
        st.stop()

    try:
        prop = cargar_propuesta(ss.propuesta_df)
    except ValueError as err:
        st.error(str(err), icon=":material/error:")
        st.stop()

    con0 = conciliar(compras, prop, ruc_propio)
    sobrantes = con0[con0["estado"] == "SOBRA_EN_PROPUESTA"]
    validados = set()
    if not sobrantes.empty:
        etiquetas = {r["clave"]: f"{r['serie_numero']} · {r['proveedor']} · IGV {soles(r['igv_propuesta'])}"
                     for _, r in sobrantes.iterrows()}
        validados = set(st.pills("¿Alguna compra de la propuesta que no subiste sí es del negocio? Márcala:",
                                 list(etiquetas), format_func=etiquetas.get, selection_mode="multi"))
    con = conciliar(compras, prop, ruc_propio, validados_por_usuario=validados)
    res = calcular(con, base_ventas, anio, periodo, regimen, regimen_renta=regimen_renta)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("IGV a pagar si aceptas la propuesta", soles(res.igv_a_pagar_si_acepta_propuesta), border=True)
    m2.metric("IGV a pagar revisado", soles(res.igv_a_pagar_revisado),
              delta=f"{-res.diferencia:,.2f}" if res.diferencia else None, delta_color="inverse", border=True)
    m3.metric("Crédito que SUNAT no tenía", soles(res.credito_recuperado), border=True)
    m4.metric("Crédito indebido que evitas", soles(res.credito_indebido_evitado), border=True)

    hallazgos = con[con["estado"] != "OK"]
    st.markdown(f"**{len(hallazgos)} hallazgo(s)** · débito de ventas {soles(res.igv_debito_ventas)} "
                f"({res.tasa_ventas:.1%}) · {'pago a cuenta de renta RMT' if regimen_renta == 'RMT' else 'cuota de renta RER'} "
                f"{soles(res.pago_a_cuenta_renta)} "
                f"({res.tasa_pago_a_cuenta:.1%})")
    ver_todo = st.toggle("Ver también los que coinciden", value=False)
    vista = (con if ver_todo else hallazgos).assign(estado=lambda d: d["estado"].map(ETIQUETA_ESTADO))
    st.table(
        vista.assign(efecto_en_credito=lambda d: d["efecto_en_credito"].map(lambda v: f"{v:+,.2f}"))
        [["estado", "serie_numero", "proveedor", "efecto_en_credito", "que_hacer"]]
        .rename(columns={"estado": "Estado", "serie_numero": "Serie-número", "proveedor": "Proveedor",
                         "efecto_en_credito": "Efecto en tu crédito (S/)", "que_hacer": "Qué hacer"}),
        border="horizontal", hide_index=True, alt="Resultado de la conciliación contra la propuesta del RCE",
    )

    g = pd.DataFrame([
        {"escenario": "Aceptar la propuesta", "concepto": "1. Crédito fiscal", "soles": res.credito_fiscal_segun_propuesta},
        {"escenario": "Aceptar la propuesta", "concepto": "2. IGV a pagar", "soles": res.igv_a_pagar_si_acepta_propuesta},
        {"escenario": "Revisado con Cuadra", "concepto": "1. Crédito fiscal", "soles": res.credito_fiscal_revisado},
        {"escenario": "Revisado con Cuadra", "concepto": "2. IGV a pagar", "soles": res.igv_a_pagar_revisado},
    ])
    grafico = alt.Chart(g).mark_bar(height=28).encode(
        y=alt.Y("escenario:N", title=None, axis=alt.Axis(labelLimit=220)),
        x=alt.X("soles:Q", stack="zero", title=f"Débito de ventas = {soles(res.igv_debito_ventas)}, dividido en crédito e IGV a pagar"),
        color=alt.Color("concepto:N", title=None, legend=alt.Legend(orient="bottom"),
                        scale=alt.Scale(range=["#99C9C2", "#0F766E"])),
        order=alt.Order("concepto:N"),
        tooltip=["escenario", "concepto", alt.Tooltip("soles:Q", format=",.2f")],
    ).properties(height=alt.Step(48))
    st.altair_chart(grafico, alt="Débito de ventas dividido en crédito fiscal e IGV a pagar, en los dos escenarios")

    d1, d2, d3 = st.columns(3)
    d1.download_button("RCE ajustado (CSV)", rce_ajustado(con).to_csv(index=False).encode("utf-8"),
                       file_name=f"rce_ajustado_{periodo}.csv", icon=":material/download:", width="stretch")
    d2.download_button("Reporte para el contador", reporte_contador(res, con, ruc_propio).encode("utf-8"),
                       file_name=f"reporte_igv_{periodo}.md", icon=":material/description:", width="stretch")
    with d3.popover("Mensaje para el dueño", icon=":material/chat:", width="stretch"):
        st.code(mensaje_dueno(res, con), language=None, wrap_lines=True)

# ---------------------------------------------------------------- paso 4: diagnóstico
ICONO_TEMA = {"Dinero": ":material/savings:", "Riesgo": ":material/warning:", "Renta": ":material/account_balance:",
              "Orden": ":material/folder_open:", "Futuro": ":material/event_upcoming:"}

with st.container(border=True):
    st.subheader("4 · Diagnóstico del negocio: dónde puedes ahorrar")
    st.markdown("Además de lo que ya revisamos, ¿qué compras haces **sin comprobante** en un mes normal? "
                "(mercado, terminal, hielo, carbón, bolsas…). Un estimado basta.")
    sin_comp = st.data_editor(
        ss.sin_comprobante, num_rows="dynamic", hide_index=True, key="editor_sin_comprobante",
        column_config={"descripcion": st.column_config.TextColumn("Qué compras", width="large"),
                       "monto": st.column_config.NumberColumn("Monto al mes (S/)", min_value=0, format="%.2f")},
    )
    recs_box = st.container()

    c_req, c_tal, c_liq, c_tasa = st.tabs(["¿Qué compras me sirven?", "¿Mi talonario sirve?",
                                            "Liquidación de compra", "Tasa especial"])
    c_req.markdown(REQUISITOS_CREDITO)
    c_tal.markdown(TALONARIOS)
    c_liq.markdown(LIQUIDACION)
    with c_tasa:
        e1, e2, e3 = st.columns(3)
        ingresos = e1.number_input("Ingresos del año anterior (S/)", min_value=0.0, value=round(base_ventas * 12, 2), step=10_000.0)
        pct_rubro = e2.slider("% de ingresos por restaurante", 0, 100, 92) / 100
        vinc = e3.toggle("¿Compartes más de 30 % del capital con otra empresa del rubro?", value=False)
        tope = TOPE_TASA_ESPECIAL_UIT * UIT[anio]
        checks = [
            (ingresos <= tope, f"Ingresos de hasta {TOPE_TASA_ESPECIAL_UIT:,} UIT ({soles(tope)})"),
            (pct_rubro >= MINIMO_INGRESOS_RUBRO, f"Al menos {MINIMO_INGRESOS_RUBRO:.0%} de ingresos del rubro"),
            (not vinc, f"Sin vinculación económica (más de {VINCULACION_MAX_CAPITAL:.0%} del capital) que sume ingresos sobre el tope"),
        ]
        for ok, texto in checks:
            st.markdown(f"{':material/check_circle:' if ok else ':material/cancel:'} {texto}")
        if anio + 1 in TASAS:
            st.caption("Referencial. SUNAT está enviando esquelas por vinculación económica: perder el beneficio implica "
                       f"reliquidar a 18 % con intereses y multa. En {anio + 1} la tasa especial pasa a "
                       f"{tasa(anio + 1, 'restaurante_mype').total:.0%}, salvo que se apruebe otra prórroga.")

    confianza_min = min(e["confianza_ocr"] for e in ss.extracciones)
    diag = diagnosticar(compras, sin_comp, con, res, anio, regimen_renta, confianza_min, vinc)

    with recs_box:
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("IGV recuperable al mes", soles(diag.igv_recuperable_mes), border=True,
                  help="Compras gravadas que hoy haces con boleta o sin comprobante: monto × 18/118, si consigues el mismo precio total con factura.")
        k2.metric("En un año", soles(diag.igv_recuperable_anio), border=True)
        k3.metric("Compras con crédito fiscal", f"{diag.pct_compras_con_credito:.0%}", border=True,
                  help="Parte de tus compras del mes (en soles) que tiene factura válida y está en el registro.")
        k4.metric("Renta extra al mes (costos sin sustento)", soles(diag.impuesto_renta_estimado_mes), border=True,
                  help="Solo RMT: costo sin comprobante × 10 % (primer tramo, estimado conservador). En el RER es 0.")

        if not diag.lineas.empty:
            st.markdown("**Dónde se te escapa más dinero**")
            vista_d = diag.lineas.assign(
                monto=lambda d: d["monto"].map(lambda v: f"{v:,.2f}"),
                igv_recuperable=lambda d: d["igv_recuperable"].map(lambda v: f"{v:,.2f}"),
            )[["descripcion", "origen", "tratamiento", "monto", "igv_recuperable", "que_hacer"]].rename(columns={
                "descripcion": "Compra", "origen": "Cómo la compras", "tratamiento": "IGV",
                "monto": "Monto (S/)", "igv_recuperable": "IGV recuperable (S/)", "que_hacer": "Qué hacer"})
            st.table(vista_d, border="horizontal", hide_index=True, alt="Compras sin crédito fiscal y cuánto IGV se podría recuperar")

        st.markdown("**Recomendaciones**")
        for rec in diag.recomendaciones:
            monto_txt = f" · **{soles(rec['monto'])} al mes**" if rec["monto"] else ""
            with st.container(border=True):
                st.markdown(f"{ICONO_TEMA.get(rec['tema'], ':material/lightbulb:')} **{rec['titulo']}**{monto_txt}")
                st.caption(rec["detalle"])

    buffer = io.BytesIO()
    a_excel(buffer, compras, con, res, diag)
    st.download_button("Descargar todo en Excel (para el contador)", buffer.getvalue(),
                       file_name=f"cuadra_{periodo}.xlsx", icon=":material/table_view:", type="primary",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

st.caption("Datos de ejemplo 100 % ficticios. Cuadra es una herramienta de revisión, no reemplaza la asesoría de un contador.")
