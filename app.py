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
# DETECCIÓN DE TOTALES GENERALES (EXACTO Y SEGURO)
# ============================================================
# ============================================================
# DETECCIÓN DE TOTALES GENERALES (AJUSTADO Y PRECISO)
# ============================================================

def detectar_total_contado(texto_pagina):
    if not texto_pagina:
        return None

    texto = re.sub(r"\s+", " ", texto_pagina).strip()

    patron = re.compile(
        r"Total\s+Venta\s+de\s+Contado\s+CO\s+([\d.,]+)",
        re.IGNORECASE,
    )

    coincidencia = patron.search(texto)

    if coincidencia:
        return dinero_a_numero(coincidencia.group(1))

    return None


def detectar_total_credito(texto_pagina):
    if not texto_pagina:
        return None

    texto = re.sub(r"\s+", " ", texto_pagina).strip()

    # Patrón estricto para capturar el total de crédito formal de la ruta sin confundirlo con códigos o facturas
    patron = re.compile(
        r"Total\s+Vta\s+Cr[ée]dito\s*Formal\s+CO\s+([\d.,]+)",
        re.IGNORECASE,
    )

    coincidencia = patron.search(texto)

    if coincidencia:
        valor = dinero_a_numero(coincidencia.group(1))
        # Filtro de seguridad: un crédito formal por ruta no debe superar los 50 millones
        if valor < 50000000:
            return valor

    return 0.0


def rutas_de_resumen_en_pagina(texto):
    rutas_encontradas = []
    for r in ["ML3E51", "ML3E52", "ML3E53"]:
        if r in texto.upper():
            rutas_encontradas.append(r)
    return list(dict.fromkeys(rutas_encontradas))


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

            # Detectar ruta activa en la página (estrictamente oficiales)
            for r_cand in ["ML3E51", "ML3E52", "ML3E53"]:
                if r_cand in texto.upper():
                    ruta_actual = r_cand
                    rutas_detectadas.add(ruta_actual)

           # Detectar totales generales
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

            # Extracción complementaria por tablas nativas
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

    # Asegurar que solo queden rutas oficiales válidas
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
                    html.escape(str(devolucion.get("Producto", "")), quote=False),
                    estilos["TextoPequeno"],
                ),
                Paragraph(
                    html.escape(str(devolucion.get("Cliente", "")), quote=False),
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
                "Estado": "No se pudo calcular proporcionalmente",
            }
        )
        continue

    total_valor_devuelto += subtotal

    resumen_devoluciones.append(
        {
            "Código": codigo_ingresado,
            "Producto": origen["Producto"],
            "Cliente": origen["Cliente"],
            "Cajas Dev.": cajas_dev,
            "Botellas Dev.": botellas_dev,
            "Precio Unitario (Botella)": precio_unitario,
            "Subtotal Devolución": subtotal,
            "Estado": "Calculado correctamente",
        }
    )


# ============================================================
# MOSTRAR RESULTADOS Y DESCARGA PDF
# ============================================================

st.divider()
st.subheader("📋 Resumen financiero de devoluciones")

if resumen_devoluciones:
    df_resumen = pd.DataFrame(resumen_devoluciones)
    
    st.dataframe(
        df_resumen.style.format({
            "Precio Unitario (Botella)": "${:,.0f}",
            "Subtotal Devolución": "${:,.0f}",
        }),
        use_container_width=True,
    )
else:
    st.info("Aún no se han registrado devoluciones válidas.")

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

fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M")

if st.button("📄 Crear PDF para Imprimir"):
    if total_libro_ruta is None:
        st.error("No se puede generar el PDF porque falta el total de contado de la ruta.")
    else:
        pdf_bytes = generar_comprobante_pdf(
            ruta=ruta_elegida,
            fecha=fecha_actual,
            total_contado=total_libro_ruta,
            total_credito=total_credito_ruta,
            total_devoluciones=total_valor_devuelto,
            neto_liquidar=neto_a_liquidar,
            devoluciones=resumen_devoluciones,
        )

        st.download_button(
            label="📥 Descargar Comprobante PDF",
            data=pdf_bytes,
            file_name=f"Comprobante_Devolucion_{ruta_elegida}.pdf",
            mime="application/pdf",
        )
        st.success("¡Comprobante generado con éxito listo para descargar!")
