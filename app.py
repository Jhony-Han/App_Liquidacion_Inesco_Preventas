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
    PageBreak,
)


# ============================================================
# CONFIGURACIÓN Y DISEÑO VISUAL (COCA-COLA / INESCO)
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

    texto = str(valor).strip()
    texto = texto.replace("$", "").replace(" ", "")

    if not texto:
        return 0.0

    if "." in texto and "," in texto:
        texto = texto.replace(".", "")
        texto = texto.replace(",", ".")
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

    coincidencia = re.search(
        r"Ruta\s*:\s*(ML3E\d+)",
        linea,
        re.IGNORECASE,
    )

    if coincidencia:
        ruta = coincidencia.group(1).upper()
        if ruta in ["ML3E51", "ML3E52", "ML3E53"]:
            return ruta

    return None


def detectar_encabezado_cliente(linea):
    if not linea:
        return None

    ruta = detectar_ruta_encabezado(linea)

    if not ruta:
        return None

    coincidencia = re.match(r"^\s*(\d{4})\b", linea)
    numero_cliente = coincidencia.group(1) if coincidencia else ""

    return {
        "ruta": ruta,
        "numero_cliente": numero_cliente,
    }


# ============================================================
# DETECCIÓN DE PRODUCTOS
# ============================================================

PATRON_PRODUCTO = re.compile(
    r"^\s*"
    r"(?P<pedido>\d{8,12})\s+"
    r"(?P<transporte>\d{7,12})\s+"
    r"(?P<codigo>\d{4,8})\s+"
    r"(?P<descripcion>.+?)\s+"
    r"(?P<cantidad>"
    r"\d+(?:[.,]\d+)?"
    r"(?:\s*/\s*\d+(?:[.,]\d+)?)?"
    r")\s+"
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
# DETECCIÓN DE TOTALES GENERALES
# ============================================================

def detectar_total_contado(texto_pagina):
    if not texto_pagina:
        return None

    lineas = texto_pagina.splitlines()
    for linea in lineas:
        if "Total Venta de Contado CO" in linea or ("Total" in linea and "Contado" in linea):
            partes = linea.split()
            for p in partes:
                val = dinero_a_numero(p)
                if val > 100000:
                    return val
    return None


def detectar_total_credito(texto_pagina):
    if not texto_pagina:
        return None

    lineas = texto_pagina.splitlines()
    for linea in lineas:
        if "Crédito" in linea or "Credito" in linea or "CréditoFormal" in linea:
            partes = linea.split()
            for p in partes:
                val = dinero_a_numero(p)
                if 10000 < val < 50000000:
                    return val
    return 0.0


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
        ruta_actual = "ML3E51"
        cliente_actual = "CLIENTE SIN IDENTIFICAR"
        numero_cliente_actual = ""

        for numero_pagina, pagina in enumerate(pdf.pages, start=1):
            texto = pagina.extract_text()

            if not texto:
                continue

            for r_cand in ["ML3E51", "ML3E52", "ML3E53"]:
                if r_cand in texto.upper():
                    ruta_actual = r_cand
                    rutas_detectadas.add(ruta_actual)

            total_contado = detectar_total_contado(texto)
            total_credito = detectar_total_credito(texto)

            if total_contado is not None:
                totales_contado[ruta_actual] = total_contado
            if total_credito is not None and total_credito > 0:
                totales_credito[ruta_actual] = total_credito

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

                producto["Ruta"] = ruta_actual
                producto["NumeroCliente"] = numero_cliente_actual
                producto["Cliente"] = cliente_actual
                producto["Pagina"] = numero_pagina

                registros.append(producto)

            tablas = pagina.extract_tables()
            for tabla in tablas:
                for fila in tabla:
                    fila_limpia = [str(c).strip() for c in fila if c is not None and str(c).strip() != ""]
                    if len(fila_limpia) >= 3:
                        c_encontrado = ""
                        p_encontrado = ""
                        c_val = 0.0
                        b_val = 0.0
                        p_unit = 0.0
                        i_tot = 0.0

                        for cell in fila_limpia:
                            if cell.isdigit() and 4 <= len(cell) <= 8 and not c_encontrado:
                                c_encontrado = str(int(cell))
                            elif not cell.isdigit() and len(cell) > 2 and not any(w in cell for w in ["Venta", "Ruta", "SUB", "Total"]):
                                if p_encontrado == "" and not any(char.isdigit() for char in cell[:2]):
                                    p_encontrado = cell

                            if "/" in cell and not c_val:
                                partes_cb = cell.split("/")
                                try:
                                    if len(partes_cb) == 2:
                                        c_val = float(partes_cb[0].strip().split()[-1])
                                        b_val = float(partes_cb[1].strip().split()[0])
                                except Exception:
                                    pass

                            cell_clean = cell.replace("$", "").replace(".", "").replace(",", ".")
                            try:
                                num = float(cell_clean)
                                if num > 10000:
                                    i_tot = num
                                elif 500 <= num <= 50000 and p_unit == 0.0:
                                    p_unit = num
                            except ValueError:
                                pass

                        if c_encontrado and not any(r["Código"] == c_encontrado and r["Cliente"] == cliente_actual for r in registros):
                            registros.append({
                                "Ruta": ruta_actual,
                                "NumeroCliente": numero_cliente_actual,
                                "Cliente": cliente_actual,
                                "Código": c_encontrado,
                                "Codigo_Key": clave_codigo(c_encontrado),
                                "Producto": p_encontrado if p_encontrado else f"PRODUCTO REF {c_encontrado}",
                                "Cajas": c_val,
                                "Botellas": b_val,
                                "Precio_Unitario": p_unit,
                                "Importe_Total": i_tot,
                                "Pedido": "",
                                "Transporte": "",
                                "Pagina": numero_pagina
                            })

    columnas = [
        "Ruta", "NumeroCliente", "Cliente", "Código", "Codigo_Key",
        "Producto", "Cajas", "Botellas", "Precio_Unitario",
        "Importe_Total", "Pedido", "Transporte", "Pagina",
    ]

    df = pd.DataFrame(registros, columns=columnas)

    if not df.empty:
        df = df.drop_duplicates(subset=["Ruta", "Cliente", "Código", "Importe_Total"], keep="first")
        df = df.reset_index(drop=True)

    rutas_validas = sorted([r for r in rutas_detectadas if r in ["ML3E51", "ML3E52", "ML3E53"]])
    if not rutas_validas:
        rutas_validas = ["ML3E51"]

    return (
        df,
        totales_contado,
        totales_credito,
        rutas_validas,
        errores_lectura,
    )


# ============================================================
# GENERAR UN PDF DE IMPRESIÓN PROFESIONAL
# ============================================================

def generar_comprobante_pdf(
    ruta,
    fecha,
    total_contado,
    total_credito,
    total_devoluciones,
    neto_liquidar,
    devoluciones,
):
    buffer = io.BytesIO()

    documento = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter),
        rightMargin=13 * mm,
        leftMargin=13 * mm,
        topMargin=13 * mm,
        bottomMargin=15 * mm,
        title=f"Comprobante de devolución {ruta}",
        author="DISTRIBUCIONES INESCO S.A.S.",
    )

    estilos = getSampleStyleSheet()

    estilos.add(
        ParagraphStyle(
            name="Empresa",
            parent=estilos["Title"],
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=19,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#E20613"),
            spaceAfter=4,
        )
    )

    estilos.add(
        ParagraphStyle(
            name="Documento",
            parent=estilos["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#333333"),
            spaceAfter=10,
        )
    )

    estilos.add(
        ParagraphStyle(
            name="TextoPequeno",
            parent=estilos["BodyText"],
            fontSize=8,
            leading=10,
        )
    )

    contenido = []

    contenido.append(
        Paragraph(
            "DISTRIBUCIONES INESCO S.A.S.",
            estilos["Empresa"],
        )
    )

    contenido.append(
        Paragraph(
            "COMPROBANTE DE DEVOLUCIÓN Y LIQUIDACIÓN",
            estilos["Documento"],
        )
    )

    contenido.append(Spacer(1, 3 * mm))

    datos_encabezado = [
        [
            Paragraph("<b>Ruta</b>", estilos["BodyText"]),
            ruta,
            Paragraph("<b>Fecha del comprobante</b>", estilos["BodyText"]),
            fecha,
        ],
        [
            Paragraph("<b>Venta de contado del libro</b>", estilos["BodyText"]),
            formato_pesos(total_contado),
            Paragraph("<b>Crédito del libro</b>", estilos["BodyText"]),
            formato_pesos(total_credito),
        ],
    ]

    tabla_encabezado = Table(
        datos_encabezado,
        colWidths=[45 * mm, 45 * mm, 50 * mm, 45 * mm],
    )

    tabla_encabezado.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF5F5")),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#222222")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#E20613")),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#F8D7DA")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )

    contenido.append(tabla_encabezado)
    contenido.append(Spacer(1, 8 * mm))

    contenido.append(
        Paragraph(
            "Detalle de productos devueltos",
            estilos["Heading3"],
        )
    )

    encabezados = [
        "Código",
        "Producto",
        "Cliente (Pág. PDF)",
        "Cajas Dev.",
        "Botellas Dev.",
        "Valor devolución",
        "Estado",
    ]

    datos_tabla = [encabezados]

    for devolucion in devoluciones:
        cliente_con_pag = f"{devolucion.get('Cliente', '')} (Pág. {devolucion.get('Pagina', '-')})"
        datos_tabla.append(
            [
                str(devolucion.get("Código", "")),
                Paragraph(
                    html.escape(str(devolucion.get("Producto", "")), quote=False),
                    estilos["TextoPequeno"],
                ),
                Paragraph(
                    html.escape(cliente_con_pag, quote=False),
                    estilos["TextoPequeno"],
                ),
                str(devolucion.get("Cajas Dev.", 0)),
                str(devolucion.get("Botellas Dev.", 0)),
                formato_pesos(
                    devolucion.get("Subtotal Devolución", 0)
                ),
                Paragraph(
                    html.escape(str(devolucion.get("Estado", "Calculado")), quote=False),
                    estilos["TextoPequeno"],
                ),
            ]
        )

    if len(datos_tabla) == 1:
        datos_tabla.append(
            [
                "—",
                "No se registraron devoluciones",
                "",
                "",
                "",
                formato_pesos(0),
                "",
            ]
        )

    tabla_detalle = Table(
        datos_tabla,
        colWidths=[
            23 * mm,
            46 * mm,
            56 * mm,
            18 * mm,
            18 * mm,
            33 * mm,
            36 * mm,
        ],
        repeatRows=1,
        hAlign="LEFT",
    )

    tabla_detalle.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E20613")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 8),
                ("FONTSIZE", (0, 1), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                 [colors.white, colors.HexColor("#FFF5F5")]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (3, 1), (5, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )

    contenido.append(tabla_detalle)
    contenido.append(Spacer(1, 8 * mm))

    resumen = [
        [
            "Venta de contado",
            formato_pesos(total_contado),
        ],
        [
            "Total devoluciones",
            "- " + formato_pesos(total_devoluciones),
        ],
        [
            "NETO A LIQUIDAR",
            formato_pesos(neto_liquidar),
        ],
    ]

    tabla_resumen = Table(
        resumen,
        colWidths=[60 * mm, 45 * mm],
        hAlign="RIGHT",
    )

    tabla_resumen.setStyle(
        TableStyle(
            [
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTNAME", (0, 0), (-1, -2), "Helvetica"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F8D7DA")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#E20613")),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#F8D7DA")),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )

    contenido.append(tabla_resumen)
    contenido.append(Spacer(1, 18 * mm))

    firmas = Table(
        [
            [
                "________________________________",
                "________________________________",
            ],
            [
                "Firma del conductor",
                "Firma del responsable de liquidación",
            ],
        ],
        colWidths=[85 * mm, 85 * mm],
        hAlign="CENTER",
    )

    firmas.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )

    contenido.append(firmas)

    def pie_pagina(canvas, doc):
        canvas.saveState()
        ancho, alto = doc.pagesize
        canvas.setStrokeColor(colors.HexColor("#CBD5E1"))
        canvas.line(13 * mm, 10 * mm, ancho - 13 * mm, 10 * mm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawString(
            13 * mm,
            6 * mm,
            "Documento generado desde InescoRoute",
        )
        canvas.drawRightString(
            ancho - 13 * mm,
            6 * mm,
            f"Página {doc.page}",
        )
        canvas.restoreState()

    documento.build(
        contenido,
        onFirstPage=pie_pagina,
        onLaterPages=pie_pagina,
    )

    buffer.seek(0)
    return buffer.getvalue()


# ============================================================
# BARRA LATERAL
# ============================================================

with st.sidebar:
    st.header("📂 Libro de rutas")

    pdf_subido = st.file_uploader(
        "Selecciona el PDF diario",
        type=["pdf"],
    )

    st.divider()

    st.markdown(
        """
        **Proceso**

        1. Cargar el libro.
        2. Seleccionar la ruta.
        3. Consultar y seleccionar en la ayuda visual.
        4. Descargar el comprobante PDF.
        """
    )


# ============================================================
# SIN ARCHIVO
# ============================================================

if pdf_subido is None:
    st.info("Sube el PDF del libro de rutas para comenzar.")
    st.stop()


# ============================================================
# PROCESAR ARCHIVO
# ============================================================

with st.spinner("Leyendo pedidos, rutas y resúmenes del PDF..."):
    contenido_pdf = pdf_subido.getvalue()

    (
        df_entregas,
        dict_totales,
        dict_creditos,
        rutas_detectadas,
        errores_lectura,
    ) = extraer_datos_completos(contenido_pdf)


if df_entregas.empty:
    st.error(
        "No se encontraron líneas de productos. "
        "Revisa que el PDF permita seleccionar/copiar su texto."
    )
    st.stop()

st.success(
    f"PDF leído. Productos detectados: {len(df_entregas)}"
)


# ============================================================
# SELECCIÓN DE RUTA
# ============================================================

st.divider()
st.subheader("🎯 Seleccionar ruta")

rutas_con_productos = sorted(
    df_entregas["Ruta"].dropna().astype(str).unique().tolist()
)

if not rutas_con_productos:
    st.error("No se detectaron rutas asociadas a productos.")
    st.stop()

ruta_elegida = st.selectbox(
    "Ruta a liquidar",
    rutas_con_productos,
)

df_ruta_actual = df_entregas[
    df_entregas["Ruta"] == ruta_elegida
].copy()

total_libro_ruta = dict_totales.get(ruta_elegida)
total_credito_ruta = dict_creditos.get(ruta_elegida, 0.0)


# ============================================================
# RESUMEN FINANCIERO
# ============================================================

c1, c2, c3 = st.columns(3)

c1.metric("Ruta activa", ruta_elegida)

if total_libro_ruta is None:
    c2.metric("Venta de contado", "No detectada")
    st.error(
        f"No se pudo identificar el total general de contado para "
        f"{ruta_elegida}. No se usará un valor de respaldo inventado."
    )
else:
    c2.metric(
        "Venta de contado",
        formato_pesos(total_libro_ruta),
    )

c3.metric(
    "Crédito del libro",
    formato_pesos(total_credito_ruta),
)


# ============================================================
# GESTIÓN DE DEVOLUCIONES (ESTADO EN SESSION_STATE)
# ============================================================

if "lista_devoluciones_activas" not in st.session_state:
    st.session_state["lista_devoluciones_activas"] = []


# ============================================================
# PANEL DE SELECCIÓN INTELIGENTE DESDE LA AYUDA VISUAL
# ============================================================

st.divider()
st.subheader("🔄 Selección y Consolidación de Devoluciones por Tienda")

st.markdown(
    "Escribe el código del producto devuelto. El sistema te mostrará **todas las tiendas (clientes)** "
    "donde se vendió. Marca las casillas de selección, ajusta la cantidad devuelta en cada tienda y haz clic en "
    "**'Incorporar a la liquidación'** para sumarlo automáticamente con su precio y descuento proporcional."
)

codigo_consulta = st.text_input(
    "🔍 Ingresa o busca el código del producto:",
    placeholder="Ej: 56624",
)

if codigo_consulta.strip():
    clave_q = clave_codigo(codigo_consulta)
    res_q = df_ruta_actual[df_ruta_actual["Codigo_Key"] == clave_q].copy()

    if not res_q.empty:
        st.success(f"Producto encontrado: **{res_q.iloc[0]['Producto']}** | Coincidencias en {len(res_q)} cliente(s)")

        # Preparamos dataframe interactivo para marcar con "churito" (checkbox) y cantidad
        res_q["Seleccionar"] = False
        res_q["Cajas_Devueltas"] = 0.0
        res_q["Botellas_Devueltas"] = 0.0

        # Reordenamos columnas para que sea súper cómodo en pantalla
        cols_mostrar = ["Seleccionar", "Pagina", "NumeroCliente", "Cliente", "Cajas", "Botellas", "Importe_Total", "Cajas_Devueltas", "Botellas_Devueltas"]
        
        # Filtramos columnas existentes por seguridad
        cols_presentes = [c for c in cols_mostrar if c in res_q.columns]

        # Usamos data_editor para la selección interactiva
        df_seleccion_tiendas = st.data_editor(
            res_q[cols_presentes],
            hide_index=True,
            use_container_width=True,
            key=f"editor_tiendas_{clave_q}",
            column_config={
                "Seleccionar": st.column_config.CheckboxColumn("¿Devolver?", default=False),
                "Pagina": st.column_config.NumberColumn("Pág. PDF", format="%d"),
                "NumeroCliente": st.column_config.TextColumn("Nº Cliente"),
                "Cliente": st.column_config.TextColumn("Nombre del Cliente"),
                "Cajas": st.column_config.NumberColumn("Cajas Vendidas", format="%.1f"),
                "Botellas": st.column_config.NumberColumn("Botellas Vendidas", format="%.1f"),
                "Importe_Total": st.column_config.NumberColumn("Importe Total ($)", format="$%,.0f"),
                "Cajas_Devueltas": st.column_config.NumberColumn("Cajas Devueltas", min_value=0.0, step=1.0),
                "Botellas_Devueltas": st.column_config.NumberColumn("Botellas Devueltas", min_value=0.0, step=1.0),
            },
        )

        if st.button("➕ Incorporar seleccionados a la liquidación", type="primary"):
            agregados = 0
            for idx, row in df_seleccion_tiendas.iterrows():
                if row["Seleccionar"] and (row["Cajas_Devueltas"] > 0 or row["Botellas_Devueltas"] > 0):
                    # Recuperamos los datos originales de la fila
                    orig = res_q.loc[idx]
                    
                    cajas_dev = float(row["Cajas_Devueltas"])
                    botellas_dev = float(row["Botellas_Devueltas"])
                    
                    cajas_orig = float(orig["Cajas"])
                    botellas_orig = float(orig["Botellas"])
                    importe_orig = float(orig["Importe_Total"])

                    subtotal = 0.0
                    if cajas_dev > 0 and cajas_orig > 0 and botellas_dev == 0:
                        subtotal = (importe_orig / cajas_orig) * cajas_dev
                    elif botellas_dev > 0 and botellas_orig > 0 and cajas_dev == 0:
                        subtotal = (importe_orig / botellas_orig) * botellas_dev
                    elif cajas_dev > 0 and cajas_orig > 0 and botellas_dev > 0 and botellas_orig > 0:
                        p_caja = importe_orig / cajas_orig
                        p_bot = importe_orig / botellas_orig
                        subtotal = (p_caja * cajas_dev) + (p_bot * botellas_dev)

                    # Añadimos a la lista en sesión
                    st.session_state["lista_devoluciones_activas"].append({
                        "Código": str(orig["Código"]),
                        "Producto": orig["Producto"],
                        "Cliente": orig["Cliente"],
                        "Pagina": orig["Pagina"],
                        "Cajas Dev.": cajas_dev,
                        "Botellas Dev.": botellas_dev,
                        "Subtotal Devolución": subtotal,
                        "Estado": "Calculado correctamente",
                    })
                    agregados += 1

            if agregados > 0:
                st.success(f"¡Se agregaron {agregados} registro(s) de devolución exitosamente!")
                st.rerun()
            else:
                st.warning("Por favor marca la casilla '¿Devolver?' y coloca cantidades mayores a 0 en al menos una tienda.")
    else:
      st.info("No hay registros para este código en la ruta seleccionada.")


# ============================================================
# RESUMEN FINANCIERO Y TABLA DE DEVOLUCIONES ACTIVAS
# ============================================================

st.divider()
st.subheader("📋 Resumen financiero de devoluciones incorporadas")

# Opción para limpiar la lista si el usuario lo desea
if st.session_state["lista_devoluciones_activas"]:
    if st.button("🗑️ Limpiar todas las devoluciones"):
        st.session_state["lista_devoluciones_activas"] = []
        st.rerun()

    df_resumen = pd.DataFrame(st.session_state["lista_devoluciones_activas"])
    
    st.dataframe(
        df_resumen.style.format({
            "Subtotal Devolución": "${:,.0f}",
        }),
        use_container_width=True,
    )
    
    total_valor_devuelto = df_resumen["Subtotal Devolución"].sum()
else:
    st.info("Aún no se han incorporado devoluciones a la lista. Utiliza el buscador superior para seleccionarlas.")
    total_valor_devuelto = 0.0

neto_a_liquidar = (total_libro_ruta if total_libro_ruta else 0.0) - total_valor_devuelto

st.divider()
c_res1, c_res2 = st.columns(2)
c_res1.metric(
    "Total devoluciones a descontar",
    f"- {formato_pesos(total_valor_devuelto)}",
    delta_color="inverse",
)
c_res2.metric(
    "Neto a liquidar (Cruce con LiquiYa)",
    formato_pesos(neto_a_liquidar),
    delta="Cruce esperado",
)

st.divider()
st.subheader("🖨️ Generar Comprobante PDF")

from datetime import timedelta

# Ajuste automático a la hora local de Colombia (-5 horas respecto a UTC)
fecha_actual = (datetime.utcnow() - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M")

if st.button("📄 Crear PDF para Imprimir"):
    if total_libro_ruta is None:
        st.error("No se puede generar el PDF porque falta el total de contado de la ruta.")
    elif not st.session_state["lista_devoluciones_activas"]:
        st.error("No hay devoluciones registradas para incluir en el comprobante.")
    else:
        pdf_bytes = generar_comprobante_pdf(
            ruta=ruta_elegida,
            fecha=fecha_actual,
            total_contado=total_libro_ruta,
            total_credito=total_credito_ruta,
            total_devoluciones=total_valor_devuelto,
            neto_liquidar=neto_a_liquidar,
            devoluciones=st.session_state["lista_devoluciones_activas"],
        )

        st.download_button(
            label="📥 Descargar Comprobante PDF",
            data=pdf_bytes,
            file_name=f"Comprobante_Devolucion_{ruta_elegida}.pdf",
            mime="application/pdf",
        )
        st.success("¡Comprobante generado con éxito listo para descargar!")
