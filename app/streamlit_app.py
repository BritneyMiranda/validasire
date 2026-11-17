"""ValidaSIRE: revisa tu SIRE (compras y ventas) antes de aceptar la propuesta de SUNAT.

Flujo: fotos de comprobantes de compra -> OCR (PP-OCR) -> extracción (reglas + Claude) -> conciliación
contra la propuesta del RCE; reporte de ventas del POS -> conciliación contra la propuesta del RVIE;
IGV a pagar calculado con código determinista; diagnóstico del negocio.
Correr:  streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import io
import json
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

st.set_page_config(page_title="ValidaSIRE · Revisa tu SIRE", page_icon=":material/fact_check:", layout="wide")

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
# Un color por pestaña, para que se vean sobre el fondo azul.
COLOR_PESTANA = ["#1D4ED8", "#0F766E", "#7C3AED", "#D97706"]
css_pestanas = "\n".join(
    f'.st-key-pestanas [data-testid="stTab"]:nth-child({i + 1}) {{ --c: {c}; }}'
    for i, c in enumerate(COLOR_PESTANA))
st.html(f"""
<style>
/* tarjetas blancas sobre el fondo azul */
[class*="st-key-card_"], [class*="st-key-aliado_"] {{
  background: #FFFFFF; border: 1px solid #BFDBFE; border-radius: 14px; padding: 1rem 1.2rem;
  box-shadow: 0 2px 6px rgba(30, 58, 138, .10);
}}
[class*="st-key-aliado_"] {{ border-left: 6px solid #1D4ED8; }}
[data-testid="stMetric"] {{ background: #FFFFFF; border-radius: 12px; }}
[data-testid="stExpander"] details {{ background: #FFFFFF; border-radius: 12px; }}
/* pestañas principales */
.st-key-pestanas [role="tablist"] {{ gap: .6rem; flex-wrap: wrap; padding-bottom: .5rem; }}
.st-key-pestanas [data-testid="stTab"] {{
  background: #FFFFFF; border: 2px solid var(--c); border-radius: 999px; padding: .45rem 1.1rem;
  color: var(--c); box-shadow: 0 2px 6px rgba(30, 58, 138, .12);
}}
.st-key-pestanas [data-testid="stTab"] * {{ font-weight: 700; font-size: 1rem; color: inherit !important; }}
.st-key-pestanas [data-testid="stTab"][aria-selected="true"] {{ background: var(--c); color: #FFFFFF; }}
{css_pestanas}
/* avisos que deben resaltar */
.st-key-ayuda_pro, .st-key-ayuda_diag {{
  background: linear-gradient(135deg, #F59E0B 0%, #D97706 100%); border-radius: 16px; padding: 1.1rem 1.4rem;
  box-shadow: 0 4px 14px rgba(217, 119, 6, .35);
}}
.st-key-ayuda_pro [data-testid="stMarkdownContainer"] :is(p, li, h3, span),
.st-key-ayuda_diag [data-testid="stMarkdownContainer"] :is(p, span) {{ color: #FFFFFF !important; }}
.st-key-ayuda_pro button, .st-key-ayuda_diag button {{ background: #FFFFFF; border: 0; }}
.st-key-ayuda_pro button, .st-key-ayuda_diag button {{ min-height: 3rem; }}
.st-key-ayuda_pro button [data-testid="stMarkdownContainer"] :is(p, span),
.st-key-ayuda_diag button [data-testid="stMarkdownContainer"] :is(p, span),
.st-key-ayuda_pro button span, .st-key-ayuda_diag button span {{ color: #B45309 !important; font-weight: 700; font-size: 1.05rem; }}
[class*="st-key-union_"] {{
  background: linear-gradient(135deg, #EEF2FF, #E0E7FF); border: 2px solid #4F46E5; border-radius: 14px;
  padding: 1rem 1.2rem; box-shadow: 0 2px 8px rgba(79, 70, 229, .18);
}}
[class*="st-key-azul_"] button {{ background: #1D4ED8; border-color: #1D4ED8; }}
[class*="st-key-azul_"] button:hover {{ background: #1E3A8A; border-color: #1E3A8A; }}
[class*="st-key-azul_"] button * {{ color: #FFFFFF !important; }}
.st-key-recuerda {{ background: #FEF3C7; border-left: 5px solid #D97706; border-radius: 10px; padding: .7rem .9rem; }}
[class*="st-key-serv_"] {{
  background: #FFFFFF; border-top: 6px solid #D97706; border-radius: 14px; padding: 1rem 1.1rem;
  box-shadow: 0 2px 8px rgba(30, 58, 138, .12);
}}
.st-key-tu_negocio {{
  background: linear-gradient(135deg, #1E3A8A 0%, #1D4ED8 60%, #2563EB 100%);
  border-radius: 16px; padding: 1.1rem 1.4rem 1.3rem;
}}
.st-key-tu_negocio h3, .st-key-tu_negocio label, .st-key-tu_negocio label p,
.st-key-tu_negocio [data-testid="stMarkdownContainer"] p, .st-key-tu_negocio [data-testid="stCaptionContainer"],
.st-key-tu_negocio [data-testid="stCaptionContainer"] p {{ color: #FFFFFF !important; }}
.st-key-tu_negocio [data-testid="stCaptionContainer"] {{ opacity: .9; }}
.st-key-casos_especiales {{ background: rgba(255,255,255,.10); border-radius: 12px; padding: .8rem 1rem; }}
[class*="st-key-rec_t"] {{ border-radius: 10px; padding: .75rem 1rem; color: #0F172A;
  box-shadow: 0 2px 6px rgba(30, 58, 138, .10); }}
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
    "NC_FALTA_EN_PROPUESTA": "▲ Nota de crédito que SUNAT no tiene",
    "NOTA_SIN_COPIA": "• Nota de crédito sin tu copia",
    "DESCUENTO_SIN_NOTA": "✕ Descuento sin nota de crédito",
}


def igv_en_juego(r) -> float:
    if r["estado"] == "DIFERENCIA_MONTO":
        return abs(r["igv_comprobante"] - r["igv_propuesta"])
    if r["estado"] == "DESCUENTO_SIN_NOTA":
        return r["igv_descuento"]
    return max(abs(r["igv_comprobante"]), abs(r["igv_propuesta"]))


def producto(r) -> str:
    if r["descripcion"]:
        return r["descripcion"]
    if r["estado"] == "NOTA_SIN_COPIA":
        return "— (devolución o descuento informado por el proveedor)"
    if r["estado"] in ("SOBRA_EN_PROPUESTA", "DUPLICADO_EN_PROPUESTA"):
        return "— (SUNAT no informa el detalle; no subiste el comprobante)"
    return "—"


def comprobante(r) -> str:
    sufijo = {"Nota de credito": " (nota de crédito)", "Nota de debito": " (nota de débito)"}.get(r.get("tipo"), "")
    return f"{r['serie_numero']}{sufijo}"
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


# ---------------------------------------------------------------- modelo de negocio (demo: no hay cuentas ni pagos)
ALIADOS = json.loads((RAIZ / "data" / "demo" / "aliados.json").read_text(encoding="utf-8"))
FOTOS_ALIADOS = RAIZ / "assets" / "aliados"
AVISO_DEMO = "Demo: no hay registro ni pagos todavía, y no se guarda ningún dato."


def mostrar_planes() -> None:
    st.markdown("**Prueba gratis:** un mes completo revisado para tu RUC. Solo vale si tus comprobantes están a "
                "nombre de ese RUC, y es una vez por RUC y por correo o celular.")
    st.markdown(
        "| Plan | Para quién | Precio |\n|---|---|---|\n"
        "| **Dueño** | Tu negocio (1 RUC) | **S/ 19 al mes** o S/ 190 al año |\n"
        "| **Contador** | Contador independiente, con colegiatura | **S/ 49 al mes** con 3 RUC + S/ 8 por RUC adicional (máximo S/ 249) |\n"
        "| **Estudio** | Estudio contable (varios usuarios) | **Desde S/ 299 al mes**, con tu propia marca |")
    st.caption("Al registrarte eliges: ¿eres dueño o contador? Al dueño le pedimos el RUC del negocio; al contador, "
               "su colegiatura y DNI, que se verifican en el padrón del Colegio de Contadores. " + AVISO_DEMO)


PESTANAS = [":material/fact_check: Revisión del SIRE", ":material/insights: Diagnóstico del negocio",
            ":material/folder_shared: Para tu contador", ":material/workspace_premium: Servicios PRO"]


def ir_a_pro() -> None:
    st.session_state["pestanas"] = PESTANAS[3]


def mostrar_union(clave: str) -> None:
    st.markdown("**¿Eres contador? Únete a nuestros aliados**\n\n"
                "- **Qué ganas:** clientes MYPE que ya revisaron su SIRE con ValidaSIRE y necesitan ayuda: recuperar "
                "crédito, cierre anual, consejería o esquelas de SUNAT.\n"
                "- **Requisitos:** colegiatura vigente y DNI, que se verifican en el padrón del Colegio de Contadores.\n"
                "- **Cómo cobramos:** el cliente paga el servicio por la app; tú recibes el **80 %** y ValidaSIRE se "
                "queda con el **20 %** como comisión. Sin pago mensual por aparecer en la lista.")
    if st.button("Postular (demo)", icon=":material/how_to_reg:", key=f"postular_{clave}", type="primary"):
        st.toast("¡Gracias por tu interés! " + AVISO_DEMO, icon=":material/info:")


def avatar(aliado: dict) -> None:
    foto = FOTOS_ALIADOS / aliado.get("foto", "")
    if aliado.get("foto") and foto.exists():
        st.image(str(foto), width=110)
    else:  # sin foto: iniciales
        iniciales = "".join(p[0] for p in aliado["nombre"].replace("CPC ", "").split()[:2])
        st.html(f'<div style="width:96px;height:96px;border-radius:50%;background:#1D4ED8;color:#fff;'
                f'display:flex;align-items:center;justify-content:center;font-size:2rem;font-weight:700">{iniciales}</div>')


# ---------------------------------------------------------------- estado
ss = st.session_state
ss.setdefault("imagenes", {})        # nombre -> bytes
ss.setdefault("extracciones", [])    # lista de dicts
ss.setdefault("propuesta_df", None)  # propuesta RCE (compras)
ss.setdefault("ventas_df", None)     # reporte de ventas del POS
ss.setdefault("rvie_df", None)       # propuesta RVIE (ventas)
ss.setdefault("ventas_base", None)
ss.setdefault("es_caso", False)
ss.setdefault("pagos", {})           # serie-número -> lo que de verdad se pagó
ss.setdefault("sin_comprobante", SIN_COMPROBANTE_VACIO)


def cargar_caso(rapido: bool, con_notas: bool = False) -> None:
    """Caso base o, con con_notas, el mismo caso más devoluciones y descuentos (notas de crédito)."""
    ss.sin_comprobante = SIN_COMPROBANTE_CASO
    for k in ("editor_sin_comprobante", "editor_compras"):
        ss.pop(k, None)
    archivos = caso.archivos_demo() + (caso.archivos_notas() if con_notas else [])
    ss.imagenes = {p.name: p.read_bytes() for p in archivos}
    ss.propuesta_df = pd.read_csv(caso.DIR_NOTAS / "propuesta_rce_con_notas.csv" if con_notas
                                  else caso.DIR / "propuesta_rce.csv", dtype=str)
    ss.ventas_df = pd.read_csv(caso.DIR_NOTAS / "ventas_registro_con_notas.csv" if con_notas
                               else caso.DIR / "ventas_registro.csv", dtype=str)
    ss.rvie_df = pd.read_csv(caso.DIR / "propuesta_rvie.csv", dtype=str)
    ss.pagos = caso.pagos_notas() if con_notas else {}
    ss.ventas_base = caso.base_ventas()
    ss.es_caso = True
    ss.extracciones = caso.cache() if rapido else []


# ---------------------------------------------------------------- barra lateral
with st.sidebar:
    with st.container(key="recuerda"):
        st.markdown("**:material/campaign: Recuerda:** si tu negocio es una **MYPE** del Régimen MYPE Tributario o del "
                    "RER, **cada mes** tienes que generar en el **SIRE** de SUNAT tu registro de ventas (RVIE) y tu "
                    "registro de compras (RCE), según el cronograma de tu último dígito de RUC. SUNAT te da una "
                    "propuesta; ValidaSIRE te ayuda a revisarla antes de aceptarla.")
    st.write("")
    st.subheader(":material/gavel: Normativa")
    dias = dias_desde_revision()
    st.caption(f"Información tributaria actualizada al **{ACTUALIZADO_AL:%d/%m/%Y}**.")
    if dias > 45:
        st.warning(f"Han pasado {dias} días desde la última revisión de normas: verifica las tasas antes de declarar.",
                   icon=":material/schedule:")
    with st.container(key="azul_fuentes"), st.popover("Fuentes y pendientes", icon=":material/menu_book:",
                                                       width="stretch"):
        for f in FUENTES:
            st.markdown(f"- **{f['norma']}**: {f['tema']}")
        if PENDIENTES:
            st.markdown("**Por confirmar:**\n" + "\n".join(f"- {p}" for p in PENDIENTES))
        st.caption("Cada lunes un monitor revisa las nuevas resoluciones de SUNAT y abre un aviso para revisarlas "
                   "(`scripts/monitor_normativa.py`). Ningún cambio de tasa se aplica sin revisión humana.")

    st.divider()
    st.subheader(":material/sell: Planes")
    with st.container(key="azul_planes"):
        with st.popover("Ver planes y prueba gratis", icon=":material/workspace_premium:", width="stretch"):
            mostrar_planes()
        with st.popover("¿Eres contador? Únete a nuestros aliados", icon=":material/group_add:", width="stretch"):
            mostrar_union(clave="sidebar")

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
    st.title("ValidaSIRE")
    st.markdown("**Para MYPE: revisa tu SIRE antes de aceptar la propuesta de SUNAT.** Sube tus facturas de compra y tus ventas: "
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
        st.caption("Otros casos especiales que ValidaSIRE todavía no calcula: venta de arroz pilado (IVAP), empresas "
                   "en la Amazonía con exoneración, exportadores. Si es tu caso, consulta a tu contador.")

periodo = f"{anio}-{MESES.index(mes) + 1:02d}"
st.write("")

t_rev, t_diag, t_cont, t_ali = st.tabs(PESTANAS, key="pestanas", on_change="rerun")

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
    if i2.button("Mismo caso, con devoluciones y descuentos", icon=":material/assignment_return:",
                 width="stretch", key="btn_caso_notas",
                 help="Agrega notas de crédito (una devolución de pulpo, un descuento de bebidas y una venta con descuento) "
                      "y un pago menor a la factura sin nota de crédito."):
        cargar_caso(rapido, con_notas=True)
        st.rerun()

    # ------------------------------------------------ paso 1: compras
    with st.container(key="card_paso1"):
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

        leidos = {e["archivo"] for e in ss.extracciones}
        pendientes = [n for n in ss.imagenes if n not in leidos]
        if pendientes:
            resultados = []
            with st.status(f"Leyendo {len(pendientes)} comprobantes…", expanded=True) as estado:
                barra = st.progress(0.0)
                for i, nombre in enumerate(pendientes, start=1):
                    r = procesar(nombre, ss.imagenes[nombre], ruc_propio, usar_claude)
                    resultados.append(r)
                    barra.progress(i / len(pendientes), text=f"{nombre}: {r['comprobante']['serie_numero'] or '¿?'} "
                                   f"· confianza {r['confianza_ocr']:.0%}")
                estado.update(label=f"{len(resultados)} comprobantes leídos", state="complete", expanded=False)
            ss.extracciones = ss.extracciones + resultados

        if ss.extracciones:
            filas = []
            for e in ss.extracciones:
                c = e["comprobante"]
                filas.append({
                    "incluir": True, "archivo": e["archivo"], **{k: c[k] for k in (
                        "tipo_comprobante", "serie_numero", "fecha_emision", "ruc_emisor", "razon_social_emisor",
                        "ruc_cliente", "descripcion", "base_gravada", "igv", "total")},
                    "doc_referencia": c.get("doc_referencia"),
                    "pagado": ss.pagos.get(c["serie_numero"]),
                    "contingencia": c.get("contingencia", False),
                    "confianza": e["confianza_ocr"],
                    "revisar": "; ".join(e["observaciones"] + [f"Claude y reglas difieren en {', '.join(e['discrepancias_motores'])}"]
                                         if e["discrepancias_motores"] else e["observaciones"]),
                })
            tabla = pd.DataFrame(filas)
            n_rev = int((tabla["revisar"] != "").sum())
            st.caption(f"Lectura: {tabla['confianza'].mean():.0%} de confianza media · {n_rev} fila(s) para revisar · "
                       f"motor: {ss.extracciones[0]['motor']}. Puedes corregir cualquier celda o desmarcar una compra "
                       "que no es del negocio. Si pagaste menos que el total (descuento, devolución), escríbelo en "
                       "**Pagado**: revisamos si hay nota de crédito.")
            compras = st.data_editor(
                tabla, hide_index=True, key="editor_compras", height=300,
                disabled=["archivo", "confianza", "revisar"],
                column_config={
                    "incluir": st.column_config.CheckboxColumn("Del negocio", width="small"),
                    "archivo": st.column_config.TextColumn("Archivo", width="small"),
                    "tipo_comprobante": st.column_config.SelectboxColumn(
                        "Tipo", options=["Factura", "Boleta", "Nota de credito", "Nota de debito", "Otro"]),
                    "doc_referencia": st.column_config.TextColumn(
                        "Modifica a", help="Solo en notas de crédito o débito: el comprobante que corrigen."),
                    "pagado": st.column_config.NumberColumn(
                        "Pagado (S/)", format="%.2f", help="Lo que de verdad pagaste. Déjalo vacío si pagaste el total."),
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
    with st.container(key="card_paso2"):
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

        base_ventas, base_rvie = 0.0, None
        try:
            if ss.ventas_df is not None:
                reg = cargar_ventas(ss.ventas_df)
                base_ventas = base_revisada(reg)
                if ss.rvie_df is not None:
                    rvie = cargar_ventas(ss.rvie_df)
                    base_rvie = base_revisada(rvie)
                    con_ventas = conciliar_ventas(reg, rvie)
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
    with st.container(key="card_paso3"):
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
            res = calcular(con, base_ventas, anio, periodo, regimen, regimen_renta=regimen_renta,
                           base_ventas_propuesta=base_rvie)

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
                st.caption("Lo que pagarías con las compras y ventas que SUNAT tiene registradas.")
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
            if res.ajuste_notas_credito or res.igv_descuentos_sin_nota:
                partes = []
                if res.ajuste_notas_credito:
                    partes.append(f"tus devoluciones y descuentos con nota de crédito bajan tu crédito en "
                                  f"**{soles(-res.ajuste_notas_credito)}** (ya está descontado)")
                if res.igv_descuentos_sin_nota:
                    partes.append(f"hay **{soles(res.igv_descuentos_sin_nota)}** de IGV en descuentos que te hicieron "
                                  "sin nota de crédito: pídela al proveedor")
                st.markdown(":material/assignment_return: **Devoluciones y descuentos:** " + "; ".join(partes) + ".")
            if base_rvie is not None and abs(base_rvie - base_ventas) > 0.01:
                st.markdown(f":material/point_of_sale: **Ventas:** tu sistema dice {soles(base_ventas)} y la propuesta "
                            f"del RVIE {soles(base_rvie)}. Lo que te toca pagar usa tus ventas reales.")

            # --- hallazgos de compras
            st.markdown("#### Compras (RCE): qué revisar")
            # Las notas de crédito/débito se muestran siempre: dicen cuánto queda neto de cada factura.
            hallazgos = con[(con["estado"] != "OK") | con["tipo"].isin(["Nota de credito", "Nota de debito"])]
            ver_todo = st.toggle("Ver también las que están bien", value=False)
            vista = (con if ver_todo else hallazgos).copy()
            vista["igv_en_juego"] = vista.apply(igv_en_juego, axis=1)
            vista["producto"] = vista.apply(producto, axis=1)
            vista["serie_numero"] = vista.apply(comprobante, axis=1)
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
                                         serie_numero=lambda d: d.apply(comprobante, axis=1),
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
                {"escenario": "Revisado con ValidaSIRE", "concepto": "1. IGV que descuentas (crédito)", "soles": res.credito_fiscal_revisado},
                {"escenario": "Revisado con ValidaSIRE", "concepto": "2. IGV a pagar", "soles": res.igv_a_pagar_revisado},
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

    # ------------------------------------------------ puente a Servicios PRO (debe resaltar)
    if res is not None:
        st.write("")
        with st.container(key="ayuda_pro"):
            p1, p2 = st.columns([5, 2], vertical_alignment="center")
            gancho = (f"Este mes encontramos **{soles(res.credito_recuperado)}** de IGV que SUNAT no tenía. "
                      "¿Y en los 11 meses anteriores? El crédito se puede anotar hasta 12 meses después.\n\n"
                      if res.credito_recuperado else "")
            p1.markdown(
                "### :material/workspace_premium: ¿Necesitas más ayuda?\n" + gancho +
                "Con **Servicios PRO** lo hacemos contigo, **en coordinación con contadores aliados colegiados**, "
                "para que cada corrección sea segura:\n"
                "- **Recupera tu crédito:** revisamos tus últimos 12 meses. Pagas solo si recuperas.\n"
                "- **Cierre anual:** tu declaración anual con los 12 meses ya revisados.\n"
                "- **Consejería:** resuelve tus dudas, también si te llegó una esquela de SUNAT.")
            p2.button("Ver Servicios PRO", icon=":material/arrow_forward:", type="secondary", width="stretch",
                      key="btn_ir_pro", on_click=ir_a_pro)

# ================================================================ pestaña 2: diagnóstico
with t_diag:
    if res is None:
        st.info("Primero completa la revisión del SIRE (o usa el caso de ejemplo).", icon=":material/arrow_back:")
    else:
        with st.container(key="card_sin_comp"):
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
            vista_d = diag.lineas.assign(
                monto=lambda d: d["monto"].map(lambda v: f"{v:,.2f}"),
                igv_recuperable=lambda d: d["igv_recuperable"].map(lambda v: f"{v:,.2f}"),
            )[["descripcion", "origen", "tratamiento", "monto", "igv_recuperable", "que_hacer"]].rename(columns={
                "descripcion": "Compra", "origen": "Cómo la compras", "tratamiento": "IGV",
                "monto": "Monto (S/)", "igv_recuperable": "IGV recuperable (S/)", "que_hacer": "Qué hacer"})
            with st.container(key="card_escapa"):
                st.markdown("#### Dónde se te escapa más dinero")
                st.table(vista_d, border="horizontal", hide_index=True,
                         alt="Compras sin crédito fiscal y cuánto IGV se podría recuperar")

        st.markdown("#### Recomendaciones")
        cols = st.columns(2)
        for i, rec in enumerate(diag.recomendaciones):
            monto_txt = f" · **{soles(rec['monto'])} al mes**" if rec["monto"] else ""
            with cols[i % 2].container(key=f"rec_t{i % len(TONOS)}_{i}"):
                st.markdown(f"{ICONO_TEMA.get(rec['tema'], ':material/lightbulb:')} **{rec['titulo']}**{monto_txt}")
                st.caption(rec["detalle"])

        with st.container(key="card_dudas"):
            st.markdown("#### :material/help: Dudas comunes, explicadas en simple")
            c_req, c_tal, c_liq = st.tabs(["¿Qué compras me sirven para descontar IGV?", "¿Mi talonario sirve?",
                                           "Liquidación de compra"])
            c_req.markdown(REQUISITOS_CREDITO)
            c_tal.markdown(TALONARIOS)
            c_liq.markdown(LIQUIDACION)

        st.write("")
        with st.container(key="ayuda_diag"):
            a1, a2 = st.columns([5, 2], vertical_alignment="center")
            a1.markdown("**:material/support_agent: ¿Necesitas ayuda con algo más específico?** Contáctate con "
                        "nuestros contadores aliados: encuentras sus perfiles y servicios en la pestaña "
                        "**Servicios PRO**.")
            a2.button("Ver Servicios PRO", icon=":material/arrow_forward:", width="stretch", key="btn_ir_pro_diag",
                      on_click=ir_a_pro)

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

# ================================================================ pestaña 4: servicios PRO
with t_ali:
    st.markdown("### :material/workspace_premium: Servicios PRO\n"
                "ValidaSIRE ofrece estos servicios **en coordinación con contadores aliados colegiados**, para mayor "
                "seguridad: la app hace la revisión y el contador firma y presenta las correcciones.")
    SERVICIOS = [
        (":material/savings:", "Recupera tu crédito",
         "Revisamos tus últimos 12 meses (el crédito se puede anotar hasta 12 meses después) y encontramos facturas y "
         "notas de crédito que faltaron. El contador hace las correcciones.",
         "**15 % de lo que recuperes.** Si no recuperas nada, no pagas."),
        (":material/event_available:","Cierre anual",
         "Juntamos tus 12 meses ya revisados, estimamos la renta del año, revisamos pagos a cuenta y saldo a favor, y "
         "el contador prepara tu declaración anual (marzo-abril).",
         "**S/ 290 por año** (pago único)."),
        (":material/support_agent:", "Consejería (incluye esquelas de SUNAT)",
         "Videollamada con un contador: ¿RER o RMT?, ¿mi talonario sirve?, ¿qué hago con esta carta de SUNAT? Si te "
         "llega una esquela, la subes y te la explicamos en simple antes de la llamada.",
         "**S/ 49 por consulta** de 30 min, o **S/ 79 al mes** con 2 consultas + ayuda con esquelas."),
    ]
    cols = st.columns(3)
    for i, (icono, titulo, texto, precio) in enumerate(SERVICIOS):
        with cols[i].container(key=f"serv_{i}", height="stretch"):
            st.markdown(f"{icono} **{titulo}**")
            st.caption(texto)
            st.markdown(precio)
    st.caption("Precios referenciales de la propuesta. Siguiente paso: que el contador aliado emita cada mes tus "
               "liquidaciones de compra a pescadores y productores sin RUC. " + AVISO_DEMO)

    st.write("")
    st.markdown(f"#### :material/groups: Nuestros contadores aliados ({len(ALIADOS['aliados'])})")
    st.warning(ALIADOS["aviso"], icon=":material/info:")
    for a in ALIADOS["aliados"]:
        with st.container(key=f"aliado_{a['id']}"):
            f1, f2, f3 = st.columns([1, 4, 1.4], vertical_alignment="center")
            with f1:
                avatar(a)
            f2.markdown(f"**{a['nombre']}** · :blue-badge[Perfil de ejemplo]  \n"
                        f"{a['experiencia']}  \n"
                        f":material/school: {a['universidad']} · Matrícula {a['matricula']}  \n"
                        f":material/location_on: {a['region']}")
            f2.markdown(" ".join(f":blue-badge[{e}]" for e in a["especialidades"]))
            if f3.button("Contactar (demo)", key=f"contacto_{a['id']}", icon=":material/call:",
                         width="stretch", type="primary"):
                st.toast(f"En la versión real, {a['nombre']} te escribiría por la app. " + AVISO_DEMO,
                         icon=":material/info:")

    st.write("")
    with st.container(key="union_pestana"):
        mostrar_union(clave="pestana")

st.divider()
st.caption("Datos de ejemplo 100 % ficticios. ValidaSIRE es un proyecto independiente: no es un producto de SUNAT ni "
           "está afiliado a ella, y no reemplaza la asesoría de un contador. Mascota Don Cuy © Britney Miranda Gutiérrez.")
