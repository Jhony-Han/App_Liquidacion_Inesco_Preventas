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
# CONFIGURACIÓN
# ============================================================

st.set_page_config(
    page_title="InescoRoute | Devoluciones",
    page_icon="🚚",
    layout="wide",
)

st.markdown(
    """
    <style>
    .main-title {
        font-size: 30px;
        font-weight: 700;
        text-align: center;
        margin-bottom: 0;
    }
    .subtitle {
        text-align: center;
        color: #777;
        margin-top: 4px;
        margin-bottom: 24px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="main-title">DISTRIBUCIONES INESCO S.A.S.</div>'
    '<div class="subtitle">Liquidación de rutas y control de devoluciones</div>',
    unsafe_allow_html=True,
)


# ============================================================
# FUNCIONES DE CONVERSIÓN
# ============================================================

def dinero_a_numero(valor):
    """
    Convierte números del PDF en formato colombiano:
    7.110.218 -> 7110218
    32.500    -> 32500
    1.250,50  -> 1250.50
    """
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
        # En este libro los puntos suelen separar miles.
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
    """Conserva los ceros iniciales para mostrar el código."""
    if codigo is None:
        return ""

    codigo = str(codigo).strip()

    if codigo.endswith(".0"):
        codigo = codigo[:-2]

    return codigo


def clave_codigo(codigo):
    """Permite buscar 056706 usando también 56706."""
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
    """
    Busca el campo explícito Ruta: ML3E53.
    No confunde los códigos internos ML3E63, ML3W10, etc.
    """
    if not linea:
        return None

    coincidencia = re.search(
        r"Ruta\s*:\s*(ML3E\d+)",
        linea,
        re.IGNORECASE,
    )

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

    numero_cliente = (
        coincidencia.group(1)
        if coincidencia
        else ""
    )

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
    """
    Lee una línea de producto del libro.

    Ejemplos:
    4057637803 402537714 160318 CC 400ML 2 30.000 60.000
    4057624469 402537714 056705 QTC350 0 / 15 62.500 31.250

    El IMPORTE se lee del PDF, no se recalcula con el precio.
    """
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
    """
    Busca exclusivamente el resumen general:
    Total Venta de Contado CO 7.110.218

    No toma los 'Total a Cobrar' de cada cliente.
    """
    if not texto_pagina:
        return None

    texto = re.sub(r"\s+", " ", texto_pagina).strip()

    patron = re.compile(
        r"\bTotal\s+Venta\s+de\s+Contado\s+CO\s+"
        r"([\d.]+(?:,\d+)?)",
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
        r"\bTotal\s+Vta\s+CréditoFormal\s+CO\s+"
        r"([\d.]+(?:,\d+)?)",
        re.IGNORECASE,
    )

    coincidencia = patron.search(texto)

    if not coincidencia:
        return None

    return dinero_a_numero(coincidencia.group(1))


def rutas_de_resumen_en_pagina(texto):
    """
    Obtiene la ruta asociada a un resumen.

    Primero utiliza el campo explícito 'Ruta:'.
    Si no existe, usa el código de ruta impreso en el
    encabezado o pie de página.
    """
    rutas_explicitas = re.findall(
        r"Ruta\s*:\s*(ML3E\d+)",
        texto,
        re.IGNORECASE,
    )

    rutas_explicitas = [
        r.upper() for r in rutas_explicitas
    ]

    if rutas_explicitas:
        return list(dict.fromkeys(rutas_explicitas))

    rutas_impresas = re.findall(
        r"\bML3E\d+\b",
        texto.upper(),
    )

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

            lineas = texto.splitlines()

            # ------------------------------------------------
            # A. Buscar resumen general en toda la página
            # ------------------------------------------------
            total_contado = detectar_total_contado(texto)
            total_credito = detectar_total_credito(texto)

            if (
                total_contado is not None
                or total_credito is not None
            ):
                rutas_resumen = rutas_de_resumen_en_pagina(texto)

                # Guardar un total solamente si la página
                # identifica una ruta de manera inequívoca.
                if len(rutas_resumen) == 1:
                    ruta_resumen = rutas_resumen[0]
                    rutas_detectadas.add(ruta_resumen)

                    if total_contado is not None:
                        totales_contado[ruta_resumen] = total_contado

                    if total_credito is not None:
                        totales_credito[ruta_resumen] = total_credito

                elif len(rutas_resumen) > 1:
                    errores_lectura.append(
                        f"Página {numero_pagina}: el resumen contiene "
                        "más de una ruta; no se asignó automáticamente."
                    )

            # ------------------------------------------------
            # B. Leer clientes y productos
            # ------------------------------------------------
            for indice, linea in enumerate(lineas):
                linea_limpia = linea.strip()

                if not linea_limpia:
                    continue

                encabezado = detectar_encabezado_cliente(linea_limpia)

                if encabezado:
                    ruta_actual = encabezado["ruta"]
                    numero_cliente_actual = encabezado["numero_cliente"]
                    rutas_detectadas.add(ruta_actual)

                    # El nombre del establecimiento suele estar
                    # en la línea siguiente al encabezado.
                    cliente_actual = "CLIENTE SIN IDENTIFICAR"

                    for siguiente in lineas[indice + 1:indice + 3]:
                        candidato = siguiente.strip()

                        if not candidato:
                            continue

                        if detectar_encabezado_cliente(candidato):
                            break

                        if (
                            "Ruta:" in candidato
                            or "PEDIDO" in candidato.upper()
                            or "Venta de Contado" in candidato
                            or "Vta CréditoFormal" in candidato
                        ):
                            continue

                        partes = re.split(r"\s{2,}", candidato)

                        if partes and partes[0].strip():
                            cliente_actual = partes[0].strip()
                            break

                    continue

                producto = analizar_producto(linea_limpia)

                if producto is None:
                    continue

                # Nunca asignar un producto a una ruta inventada.
                if not ruta_actual:
                    errores_lectura.append(
                        f"Página {numero_pagina}: se encontró una línea "
                        "de producto sin ruta identificada."
                    )
                    continue

                producto["Ruta"] = ruta_actual
                producto["NumeroCliente"] = numero_cliente_actual
                producto["Cliente"] = cliente_actual
                producto["Pagina"] = numero_pagina

                registros.append(producto)

    columnas = [
        "Ruta",
        "NumeroCliente",
        "Cliente",
        "Código",
        "Codigo_Key",
        "Producto",
        "Cajas",
        "Botellas",
        "Precio_Unitario",
        "Importe_Total",
        "Pedido",
        "Transporte",
        "Pagina",
    ]

    df = pd.DataFrame(registros, columns=columnas)

    if not df.empty:
        # No se eliminan filas solo porque tengan mismo producto:
        # pueden ser pedidos/promociones diferentes.
        df = df.reset_index(drop=True)

    return (
        df,
        totales_contado,
        totales_credito,
        sorted(rutas_detectadas),
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
    """
    Crea un PDF real tamaño carta.
    Se descarga desde Streamlit y se imprime normalmente.
    """
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
            textColor=colors.HexColor("#183153"),
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

    estilos.add(
        ParagraphStyle(
            name="ValorDerecha",
            parent=estilos["BodyText"],
            fontName="Helvetica-Bold",
            alignment=TA_RIGHT,
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
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F3F6FA")),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#222222")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#CBD5E1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
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
        "Cliente",
        "Cajas",
        "Botellas",
        "Valor devolución",
        "Estado",
    ]

    datos_tabla = [encabezados]

    for devolucion in devoluciones:
        datos_tabla.append(
            [
                str(devolucion.get("Código", "")),
                Paragraph(
                    html.escape(str(devolucion.get("Producto", "")),
                                quote=False),
                    estilos["TextoPequeno"],
                ),
                Paragraph(
                    html.escape(str(devolucion.get("Cliente", "")),
                                quote=False),
                    estilos["TextoPequeno"],
                ),
                str(devolucion.get("Cajas Dev.", 0)),
                str(devolucion.get("Botellas Dev.", 0)),
                formato_pesos(
                    devolucion.get("Subtotal Devolución", 0)
                ),
                Paragraph(
                    html.escape(str(devolucion.get("Estado", "Calculado")),
                                quote=False),
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
            48 * mm,
            54 * mm,
            18 * mm,
            20 * mm,
            33 * mm,
            37 * mm,
        ],
        repeatRows=1,
        hAlign="LEFT",
    )

    tabla_detalle.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#183153")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 8),
                ("FONTSIZE", (0, 1), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                 [colors.white, colors.HexColor("#F7F9FC")]),
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
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#E8EEF7")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#CBD5E1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
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
        3. Registrar devoluciones.
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
# DIAGNÓSTICO
# ============================================================

with st.expander("🔍 Diagnóstico de lectura del PDF"):
    st.write("**Rutas detectadas:**", ", ".join(rutas_detectadas))

    filas_diagnostico = []

    for ruta in rutas_detectadas:
        filas_diagnostico.append(
            {
                "Ruta": ruta,
                "Venta de contado": dict_totales.get(ruta),
                "Crédito": dict_creditos.get(ruta),
                "Líneas de producto": int(
                    (df_entregas["Ruta"] == ruta).sum()
                ),
            }
        )

    st.dataframe(
        pd.DataFrame(filas_diagnostico),
        use_container_width=True,
    )

    if errores_lectura:
        st.warning("Advertencias de lectura:")
        for error in errores_lectura[:30]:
            st.write(f"- {error}")

    else:
        st.caption("No se detectaron advertencias estructurales.")


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
# BUSCAR PRODUCTOS
# ============================================================

st.divider()
st.subheader("🔎 Buscar producto y consultar clientes")

codigo_busqueda = st.text_input(
    "Código del producto",
    placeholder="Ejemplo: 160318 o 056706",
)

if codigo_busqueda.strip():
    clave = clave_codigo(codigo_busqueda)

    encontrados = df_ruta_actual[
        df_ruta_actual["Codigo_Key"] == clave
    ].copy()

    if encontrados.empty:
        st.warning(
            f"No se encontró el código {codigo_busqueda} en {ruta_elegida}."
        )
    else:
        st.success(
            f"Producto: {encontrados.iloc[0]['Producto']} "
            f"| Coincidencias: {len(encontrados)}"
        )

        st.dataframe(
            encontrados[
                [
                    "NumeroCliente",
                    "Cliente",
                    "Código",
                    "Producto",
                    "Cajas",
                    "Botellas",
                    "Precio_Unitario",
                    "Importe_Total",
                ]
            ].style.format(
                {
                    "Precio_Unitario": "${:,.0f}",
                    "Importe_Total": "${:,.0f}",
                }
            ),
            use_container_width=True,
        )


# ============================================================
# REGISTRO DE DEVOLUCIONES
# ============================================================

st.divider()
st.subheader("🔄 Registrar devoluciones")

st.caption(
    "Introduce el código y la cantidad devuelta. "
    "Si el código aparece con diferentes precios o promociones, "
    "elige el cliente de origen."
)

base_devoluciones = pd.DataFrame(
    [
        {
            "Código": "",
            "Cliente": "",
            "Cajas Dev.": 0.0,
            "Botellas Dev.": 0.0,
        }
    ]
)

devoluciones_ingresadas = st.data_editor(
    base_devoluciones,
    num_rows="dynamic",
    use_container_width=True,
    key="editor_devoluciones",
    column_config={
        "Código": st.column_config.TextColumn(
            "Código",
            help="Puedes escribirlo con o sin ceros iniciales.",
        ),
        "Cliente": st.column_config.TextColumn(
            "Cliente de origen (opcional)",
        ),
        "Cajas Dev.": st.column_config.NumberColumn(
            "Cajas devueltas",
            min_value=0.0,
            step=1.0,
        ),
        "Botellas Dev.": st.column_config.NumberColumn(
            "Botellas devueltas",
            min_value=0.0,
            step=1.0,
        ),
    },
)


# ============================================================
# CALCULAR DEVOLUCIONES
# ============================================================

resumen_devoluciones = []
total_valor_devuelto = 0.0

for _, fila in devoluciones_ingresadas.iterrows():
    codigo_ingresado = str(fila.get("Código", "")).strip()

    if not codigo_ingresado or codigo_ingresado.lower() == "none":
        continue

    cajas_dev = cantidad_a_numero(fila.get("Cajas Dev.", 0))
    botellas_dev = cantidad_a_numero(fila.get("Botellas Dev.", 0))
    cliente_elegido = str(fila.get("Cliente", "")).strip()

    if cajas_dev <= 0 and botellas_dev <= 0:
        continue

    clave = clave_codigo(codigo_ingresado)

    coincidencias = df_ruta_actual[
        df_ruta_actual["Codigo_Key"] == clave
    ].copy()

    if coincidencias.empty:
        resumen_devoluciones.append(
            {
                "Código": codigo_ingresado,
                "Producto": "NO ENCONTRADO",
                "Cliente": cliente_elegido,
                "Cajas Dev.": cajas_dev,
                "Botellas Dev.": botellas_dev,
                "Precio Unitario (Botella)": 0.0,
                "Subtotal Devolución": 0.0,
                "Estado": "Código no encontrado",
            }
        )
        continue

    # Si se especifica cliente, intentar seleccionar su registro.
    if cliente_elegido:
        filtradas = coincidencias[
            coincidencias["Cliente"].astype(str).str.contains(
                re.escape(cliente_elegido),
                case=False,
                na=False,
            )
        ]

        if filtradas.empty:
            resumen_devoluciones.append(
                {
                    "Código": codigo_ingresado,
                    "Producto": coincidencias.iloc[0]["Producto"],
                    "Cliente": cliente_elegido,
                    "Cajas Dev.": cajas_dev,
                    "Botellas Dev.": botellas_dev,
                    "Precio Unitario (Botella)": 0.0,
                    "Subtotal Devolución": 0.0,
                    "Estado": "No coincide el cliente",
                }
            )
            continue

        coincidencias = filtradas

    # Para evitar inventar precios, seleccionar solo si existe
    # una única fila de origen compatible.
    if len(coincidencias) != 1:
        resumen_devoluciones.append(
            {
                "Código": codigo_ingresado,
                "Producto": coincidencias.iloc[0]["Producto"],
                "Cliente": (
                    cliente_elegido
                    if cliente_elegido
                    else "SELECCIONAR CLIENTE"
                ),
                "Cajas Dev.": cajas_dev,
                "Botellas Dev.": botellas_dev,
                "Precio Unitario (Botella)": 0.0,
                "Subtotal Devolución": 0.0,
                "Estado": "Varias líneas de origen; especifica el cliente",
            }
        )
        continue

    origen = coincidencias.iloc[0]

    cajas_originales = float(origen["Cajas"])
    botellas_originales = float(origen["Botellas"])
    importe_original = float(origen["Importe_Total"])

    # Se calcula proporcionalmente a la cantidad de la línea.
    # No se asume un tamaño universal de caja.
    if cajas_dev > 0 and cajas_originales > 0 and botellas_dev == 0:
        precio_por_caja = importe_original / cajas_originales
        subtotal = precio_por_caja * cajas_dev
        precio_unitario = precio_por_caja

    elif botellas_dev > 0 and botellas_originales > 0 and cajas_dev == 0:
        precio_unitario = importe_original / botellas_originales
        subtotal = precio_unitario * botellas_dev

    elif cajas_dev > 0 and cajas_originales > 0 and botellas_dev > 0 and botellas_originales > 0:
        precio_por_caja = importe_original / cajas_originales
        precio_por_botella = importe_original / botellas_originales
        subtotal = (
            precio_por_caja * cajas_dev
            + precio_por_botella * botellas_dev
        )
        precio_unitario = precio_por_botella

    else:
        resumen_devoluciones.append(
            {
                "Código": codigo_ingresado,
                "Producto": origen["Producto"],
                "Cliente": origen["Cliente"],
                "Cajas Dev.": cajas_dev,
                "Botellas Dev.": botellas_dev,
                "Precio Unitario (Botella)": 0.0,
                "Subtotal Devolución": 0.0,
                "Estado": "La cantidad no coincide con la presentación leída",
            }
        )
        continue

    if subtotal <= 0:
        estado = "Valor no calculable"
    else:
        estado = "Calculado"
        total_valor_devuelto += subtotal

    resumen_devoluciones.append(
        {
            "Código": origen["Código"],
            "Producto": origen["Producto"],
            "Cliente": origen["Cliente"],
            "Cajas Dev.": cajas_dev,
            "Botellas Dev.": botellas_dev,
            "Precio Unitario (Botella)": precio_unitario,
            "Subtotal Devolución": subtotal,
            "Estado": estado,
        }
    )


# ============================================================
# MOSTRAR RESUMEN DE DEVOLUCIONES
# ============================================================

st.divider()
st.subheader("📋 Resumen de devoluciones")

if resumen_devoluciones:
    df_resumen = pd.DataFrame(resumen_devoluciones)

    st.dataframe(
        df_resumen.style.format(
            {
                "Precio Unitario (Botella)": "${:,.0f}",
                "Subtotal Devolución": "${:,.0f}",
            }
        ),
        use_container_width=True,
    )
else:
    df_resumen = pd.DataFrame(
        columns=[
            "Código",
            "Producto",
            "Cliente",
            "Cajas Dev.",
            "Botellas Dev.",
            "Precio Unitario (Botella)",
            "Subtotal Devolución",
            "Estado",
        ]
    )

    st.info("Registra una devolución para ver el resumen.")


# ============================================================
# LIQUIDACIÓN
# ============================================================

if total_libro_ruta is not None:
    neto_a_liquidar = total_libro_ruta - total_valor_devuelto
else:
    neto_a_liquidar = None

st.divider()
st.subheader("💰 Liquidación de la ruta")

c1, c2, c3 = st.columns(3)

if total_libro_ruta is None:
    c1.metric("Venta de contado", "No detectada")
else:
    c1.metric("Venta de contado", formato_pesos(total_libro_ruta))

c2.metric(
    "Devoluciones calculadas",
    "- " + formato_pesos(total_valor_devuelto),
)

if neto_a_liquidar is None:
    c3.metric("Neto a liquidar", "Pendiente")
else:
    c3.metric("Neto a liquidar", formato_pesos(neto_a_liquidar))


# ============================================================
# TRAZABILIDAD
# ============================================================

st.divider()
st.subheader("🏪 Clientes que recibieron los productos")

codigos_registrados = {
    clave_codigo(item["Código"])
    for item in resumen_devoluciones
    if item.get("Código")
}

for codigo_key in sorted(codigos_registrados):
    clientes_producto = df_ruta_actual[
        df_ruta_actual["Codigo_Key"] == codigo_key
    ]

    if clientes_producto.empty:
        continue

    nombre = clientes_producto.iloc[0]["Producto"]

    with st.expander(f"{nombre} — código normalizado {codigo_key}"):
        st.dataframe(
            clientes_producto[
                [
                    "NumeroCliente",
                    "Cliente",
                    "Código",
                    "Producto",
                    "Cajas",
                    "Botellas",
                    "Precio_Unitario",
                    "Importe_Total",
                ]
            ].style.format(
                {
                    "Precio_Unitario": "${:,.0f}",
                    "Importe_Total": "${:,.0f}",
                }
            ),
            use_container_width=True,
        )


# ============================================================
# DESCARGA DE COMPROBANTE PDF
# ============================================================

st.divider()
st.subheader("🧾 Comprobante profesional para imprimir")

st.write(
    "Genera un PDF independiente, tamaño carta horizontal, "
    "con el resumen financiero, el detalle de devoluciones "
    "y los espacios de firmas."
)

if total_libro_ruta is None:
    st.warning(
        "No se puede generar una liquidación definitiva hasta "
        "que el valor de contado se lea correctamente."
    )
elif st.button("🧾 Generar comprobante PDF", type="primary"):
    fecha_comprobante = datetime.now().strftime("%d/%m/%Y %H:%M")

    pdf_comprobante = generar_comprobante_pdf(
        ruta=ruta_elegida,
        fecha=fecha_comprobante,
        total_contado=total_libro_ruta,
        total_credito=total_credito_ruta,
        total_devoluciones=total_valor_devuelto,
        neto_liquidar=neto_a_liquidar,
        devoluciones=resumen_devoluciones,
    )

    st.download_button(
        label="⬇️ Descargar comprobante para imprimir",
        data=pdf_comprobante,
        file_name=f"Comprobante_Devolucion_{ruta_elegida}.pdf",
        mime="application/pdf",
        use_container_width=True,
    )

    st.success(
        "Comprobante generado. Descárgalo y ábrelo para imprimirlo "
        "o guardarlo."
    )


# ============================================================
# CATÁLOGO DE RUTA
# ============================================================

with st.expander("📦 Ver catálogo completo de esta ruta"):
    st.dataframe(
        df_ruta_actual,
        use_container_width=True,
    )
