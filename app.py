import io
import re
import html
from datetime import datetime

import pandas as pd
import pdfplumber
import streamlit as st

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


# ============================================================
# CONFIGURACIÓN Y ESTILOS VISUALES (TEMA COCA-COLA / INESCO)
# ============================================================

st.set_page_config(
    page_title="InescoRoute | Liquidación y Devoluciones",
    page_icon="🚚",
    layout="wide",
)

st.markdown(
    """
    <style>
    .main-header {
        background: linear-gradient(90deg, #E20613 0%, #8B0000 100%);
        padding: 24px;
        border-radius: 12px;
        color: white;
        text-align: center;
        margin-bottom: 24px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    .main-title {
        font-size: 32px;
        font-weight: 800;
        margin: 0;
        letter-spacing: 1px;
    }
    .subtitle {
        font-size: 16px;
        color: #F8D7DA;
        margin-top: 6px;
        font-weight: 400;
    }
    </style>
    <div class="main-header">
        <div class="main-title">DISTRIBUCIONES INESCO S.A.S.</div>
        <div class="subtitle">🚚 Módulo Profesional de Liquidación de Rutas y Cruce con LiquiYa</div>
    </div>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# FUNCIONES DE CONVERSIÓN
# ============================================================

def dinero_a_numero(valor):
    if valor is None:
        return 0.0
    texto = str(valor).strip().replace("$", "").replace(" ", "")
    if not texto:
        return 0.0

    if "." in texto and "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif texto.count(".") > 1:
        texto = texto.replace(".", "")
    elif "." in texto:
        texto = texto.replace(".", "")
    elif "," in texto:
        texto = texto.replace(",", ".")

    try:
        return float(texto)
    except ValueError:
        return 0.0


def cantidad_a_numero(valor):
    if valor is None:
        return 0.0
    texto = str(valor).strip().replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return 0.0


def normalizar_codigo(codigo):
    if codigo is None:
        return ""
    codigo = str(codigo).strip()
    if codigo.endswith(".0"):
        codigo = codigo[:-2]
    return codigo


def clave_codigo(codigo):
    codigo = normalizar_codigo(codigo)
    solo_digitos = re.sub(r"\D", "", codigo)
    if not solo_digitos:
        return ""
    return str(int(solo_digitos))


def formato_pesos(valor):
    return f"${float(valor):,.0f}".replace(",", ".")


# ============================================================
# DETECCIÓN DE RUTA Y CLIENTE
# ============================================================

def detectar_ruta_encabezado(linea):
    if not linea:
        return None
    coincidencia = re.search(r"Ruta\s*:\s*(ML3E\d+)", linea, re.IGNORECASE)
    if coincidencia:
        return coincidencia.group(1).upper()
    return None


def detectar_encabezado_cliente(linea):
    if not linea:
        return None
    ruta = detectar_ruta_encabezado(linea)
    if not ruta:
        return None
    coincidencia = re.match(r"^\s*(\d{4})\b", linea)
    numero_cliente = coincidencia.group(1) if coincidencia else ""
    return {"ruta": ruta, "numero_cliente": numero_cliente}


PATRON_PRODUCTO = re.compile(
    r"^\s*"
    r"(?P<pedido>\d{8,12})\s+"
    r"(?P<transporte>\d{7,12})\s+"
    r"(?P<codigo>\d{4,8})\s+"
    r"(?P<descripcion>.+?)\s+"
    r"(?P<cantidad>\d+(?:[.,]\d+)?(?:\s*/\s*\d+(?:[.,]\d+)?)?)\s+"
    r"(?P<precio>\d[\d.,]*)\s+"
    r"(?P<importe>\d[\d.,]*)"
    r"(?:\s+.*)?$"
)


def analizar_producto(linea):
    coincidencia = PATRON_PRODUCTO.match(linea.strip())
    if not coincidencia:
        return None
    datos = coincidencia.groupdict()
    cantidad = datos["cantidad"].strip()

    cajas = 0.0
    botellas = 0.0
    if "/" in cantidad:
        partes = re.split(r"\s*/\s*", cantidad)
        cajas = cantidad_a_numero(partes[0])
        botellas = cantidad_a_numero(partes[1])
    else:
        cajas = cantidad_a_numero(cantidad)

    codigo = normalizar_codigo(datos["codigo"])
    return {
        "Pedido": datos["pedido"],
        "Transporte": datos["transporte"],
        "Código": codigo,
        "Codigo_Key": clave_codigo(codigo),
        "Producto": datos["descripcion"].strip(),
        "Cajas": cajas,
        "Botellas": botellas,
        "Precio_Unitario": dinero_a_numero(datos["precio"]),
        "Importe_Total": dinero_a_numero(datos["importe"]),
    }


# ============================================================
# DETECCIÓN DE TOTALES GENERALES (AJUSTADO A LA IMAGEN)
# ============================================================

def detectar_total_contado(texto_pagina):
    if not texto_pagina:
        return None
    texto = re.sub(r"\s+", " ", texto_pagina).strip()
    patron = re.compile(
        r"Total\s+Venta\s+de\s+Contado\s+CO\s+([\d.]+)",
        re.IGNORECASE,
    )
    coincidencia = patron.search(texto)
    if not coincidencia:
        return None
    return dinero_a_numero(coincidencia.group(1))


def detectar_total_credito(texto_pagina):
    if not texto_pagina:
        return None
    texto = re.sub(r"\s+", " ", texto_pagina).strip()
    patron = re.compile(
        r"Total\s+Vta\s+Cr[ée]dito\s*Formal\s+CO\s+([\d.]+)",
        re.IGNORECASE,
    )
    coincidencia = patron.search(texto)
    if not coincidencia:
        return None
    return dinero_a_numero(coincidencia.group(1))


def rutas_de_resumen_en_pagina(texto):
    rutas_explicitas = re.findall(r"Ruta\s*:\s*(ML3E\d+)", texto, re.IGNORECASE)
    rutas_explicitas = [r.upper() for r in rutas_explicitas]
    if rutas_explicitas:
        return list(dict.fromkeys(rutas_explicitas))
    rutas_impresas = re.findall(r"\bML3E\d+\b", texto.upper())
    return list(dict.fromkeys(rutas_impresas))


# ============================================================
# LECTOR PRINCIPAL DEL PDF
# ============================================================

@st.cache_data(show_spinner=False)
def extraer_datos_completos(contenido_pdf):
    registros = []
    totales_contado = {}
    totales_credito = {}
    rutas_detectadas = set()
    errores_lectura = []

    with pdfplumber.open(io.BytesIO(contenido_pdf)) as pdf:
        ruta_actual = None
        cliente_actual = "CLIENTE SIN IDENTIFICAR"
        numero_cliente_actual = ""

        for numero_pagina, pagina in enumerate(pdf.pages, start=1):
            texto = pagina.extract_text()
            if not texto:
                continue

            total_contado = detectar_total_contado(texto)
            total_credito = detectar_total_credito(texto)

            if total_contado is not None or total_credito is not None:
                rutas_resumen = rutas_de_resumen_en_pagina(texto)
                if len(rutas_resumen) == 1:
                    ruta_resumen = rutas_resumen[0]
                    rutas_detectadas.add(ruta_resumen)
                    if total_contado is not None:
                        totales_contado[ruta_resumen] = total_contado
                    if total_credito is not None:
                        totales_credito[ruta_resumen] = total_credito

            lineas = texto.splitlines()
            for indice, linea in enumerate(lineas):
                linea_limpia = linea.strip()
                if not linea_limpia:
                    continue

                encabezado = detectar_encabezado_cliente(linea_limpia)
                if encabezado:
                    ruta_actual = encabezado["ruta"]
                    numero_cliente_actual = encabezado["numero_cliente"]
                    rutas_detectadas.add(ruta_actual)
                    cliente_actual = "CLIENTE SIN IDENTIFICAR"

                    for siguiente in lineas[indice + 1:indice + 3]:
                        candidato = siguiente.strip()
                        if not candidato or detectar_encabezado_cliente(candidato):
                            continue
                        if any(k in candidato for k in ["Ruta:", "PEDIDO", "Venta", "Crédito"]):
                            continue
                        partes = re.split(r"\s{2,}", candidato)
                        if partes and partes[0].strip():
                            cliente_actual = partes[0].strip()
                            break
                    continue

                producto = analizar_producto(linea_limpia)
                if producto is None:
                    continue

                if not ruta_actual:
                    continue

                producto["Ruta"] = ruta_actual
                producto["NumeroCliente"] = numero_cliente_actual
                producto["Cliente"] = cliente_actual
                producto["Pagina"] = numero_pagina
                registros.append(producto)

    columnas = [
        "Ruta", "NumeroCliente", "Cliente", "Código", "Codigo_Key",
        "Producto", "Cajas", "Botellas", "Precio_Unitario",
        "Importe_Total", "Pedido", "Transporte", "Pagina",
    ]
    df = pd.DataFrame(registros, columns=columnas)
    if not df.empty:
        df = df.reset_index(drop=True)

    return df, totales_contado, totales_credito, sorted(rutas_detectadas), errores_lectura


# ============================================================
# GENERADOR DE PDF (REPORTLAB)
# ============================================================

def generar_comprobante_pdf(
    ruta, fecha, total_contado, total_credito,
    total_devoluciones, neto_liquidar, devoluciones,
):
    buffer = io.BytesIO()
    documento = SimpleDocTemplate(
        buffer, pagesize=landscape(letter),
        rightMargin=13 * mm, leftMargin=13 * mm,
        topMargin=13 * mm, bottomMargin=15 * mm,
    )
    estilos = getSampleStyleSheet()
    contenido = []

    estilos.add(ParagraphStyle(
        name="Empresa", parent=estilos["Title"],
        fontName="Helvetica-Bold", fontSize=16, leading=19,
        alignment=TA_CENTER, textColor=colors.HexColor("#E20613"), spaceAfter=4,
    ))
    estilos.add(ParagraphStyle(
        name="Documento", parent=estilos["Heading2"],
        fontName="Helvetica-Bold", fontSize=12, leading=15,
        alignment=TA_CENTER, textColor=colors.HexColor("#333333"), spaceAfter=10,
    ))
    estilos.add(ParagraphStyle(name="TextoPequeno", parent=estilos["BodyText"], fontSize=8, leading=10))

    contenido.append(Paragraph("DISTRIBUCIONES INESCO S.A.S.", estilos["Empresa"]))
    contenido.append(Paragraph("COMPROBANTE DE DEVOLUCIÓN Y LIQUIDACIÓN", estilos["Documento"]))
    contenido.append(Spacer(1, 3 * mm))

    datos_encabezado = [
        [Paragraph("<b>Ruta</b>", estilos["BodyText"]), ruta, Paragraph("<b>Fecha</b>", estilos["BodyText"]), fecha],
        [Paragraph("<b>Venta de contado</b>", estilos["BodyText"]), formato_pesos(total_contado), Paragraph("<b>Crédito</b>", estilos["BodyText"]), formato_pesos(total_credito)],
    ]
    tabla_encabezado = Table(datos_encabezado, colWidths=[45 * mm, 45 * mm, 50 * mm, 45 * mm])
    tabla_encabezado.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF5F5")),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#E20613")),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#F8D7DA")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    contenido.append(tabla_encabezado)
    contenido.append(Spacer(1, 8 * mm))

    # Detalle y Resumen...
    documento.build(contenido)
    buffer.seek(0)
    return buffer.getvalue()


# ============================================================
# INTERFAZ PRINCIPAL STREAMLIT
# ============================================================

with st.sidebar:
    st.header("📂 Libro de rutas")
    pdf_subido = st.file_uploader("Selecciona el PDF diario", type=["pdf"])
    st.divider()
    st.markdown("**Proceso:**\n1. Cargar libro\n2. Seleccionar ruta\n3. Registrar devoluciones\n4. Descargar PDF")

if pdf_subido is None:
    st.info("Sube el PDF del libro de rutas para comenzar.")
    st.stop()

with st.spinner("Leyendo pedidos, rutas y resúmenes del PDF..."):
    df_entregas, dict_totales, dict_creditos, rutas_detectadas, errores_lectura = extraer_datos_completos(pdf_subido.getvalue())

if df_entregas.empty:
    st.error("No se encontraron líneas de productos en el PDF.")
    st.stop()

st.success(f"PDF leído correctamente. Productos detectados: {len(df_entregas)}")

rutas_con_productos = sorted(df_entregas["Ruta"].dropna().astype(str).unique().tolist())
if not rutas_con_productos:
    st.error("No se detectaron rutas asociadas a productos.")
    st.stop()

st.divider()
st.subheader("🎯 Seleccionar ruta")
ruta_elegida = st.selectbox("Ruta a liquidar", rutas_con_productos)

df_ruta_actual = df_entregas[df_entregas["Ruta"] == ruta_elegida].copy()
total_libro_ruta = dict_totales.get(ruta_elegida)
total_credito_ruta = dict_creditos.get(ruta_elegida, 0.0)

c1, c2, c3 = st.columns(3)
c1.metric("Ruta activa", ruta_elegida)
if total_libro_ruta is None:
    c2.metric("Venta de contado", "No detectada")
    st.error(f"No se pudo identificar el total general de contado para {ruta_elegida}.")
else:
    c2.metric("Venta de contado", formato_pesos(total_libro_ruta))
c3.metric("Crédito del libro", formato_pesos(total_credito_ruta))
