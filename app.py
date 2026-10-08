import re
import io
import pandas as pd
import pdfplumber
import streamlit as st


# ============================================================
# CONFIGURACIÓN
# ============================================================

st.set_page_config(
    page_title="InescoRoute - Liquidación y Devoluciones",
    page_icon="🚚",
    layout="wide",
)


# ============================================================
# ESTILO / ENCABEZADO
# ============================================================

st.markdown(
    """
    <div style="text-align:center;">
        <h1>🚚 DISTRIBUCIONES INESCO S.A.S.</h1>
        <h3>Módulo de Liquidación de Rutas y Cruce con LiquiYa</h3>
    </div>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================

def limpiar_numero_monetario(valor):
    """
    Convierte valores del PDF como:

        32.500
        1.250.000
        $32.500
        32,500

    a número float.

    En este PDF el punto representa separación de miles.
    """

    if valor is None:
        return 0.0

    texto = str(valor).strip()

    if not texto:
        return 0.0

    texto = (
        texto
        .replace("$", "")
        .replace(" ", "")
    )

    # Formato habitual del PDF colombiano:
    # 32.500 -> 32500
    if "." in texto and "," not in texto:
        texto = texto.replace(".", "")

    # Por seguridad, si aparece coma:
    elif "." in texto and "," in texto:
        texto = texto.replace(".", "").replace(",", ".")

    elif "," in texto:
        texto = texto.replace(",", ".")

    try:
        return float(texto)
    except ValueError:
        return 0.0


def normalizar_codigo(codigo):
    """
    Mantiene códigos como 056841 exactamente como aparecen.
    """

    if codigo is None:
        return ""

    codigo = str(codigo).strip()

    if not codigo:
        return ""

    # El código de producto puede tener ceros iniciales.
    # NO convertirlo a int.
    return codigo


def convertir_cantidad(valor):
    """
    Convierte una cantidad a float.
    """

    if valor is None:
        return 0.0

    texto = str(valor).strip()

    if not texto:
        return 0.0

    try:
        return float(texto.replace(",", "."))
    except ValueError:
        return 0.0


# ============================================================
# DETECCIÓN DE LÍNEAS DE PRODUCTOS
# ============================================================

def analizar_linea_producto(linea):
    """
    Intenta interpretar una línea de producto del PDF.

    Ejemplos reales del PDF:

    4057271111 402537712 056841 FLASHUVA 1 32.500 32.500

    4057640671 402537713 135718 CCSO8OZX3 0 / 15 36.000 18.000

    4057640671 402537713 160318 CC 400ML 3 30.000 82.782

    Devuelve un diccionario si la línea corresponde a un producto.
    """

    if not linea:
        return None

    linea = linea.strip()

    # --------------------------------------------------------
    # Patrón:
    #
    # PEDIDO TRANSP. CODIGO DESCRIPCION CANTIDAD PRECIO IMPORTE
    #
    # La descripción puede tener espacios.
    # --------------------------------------------------------

    patron = re.compile(
        r"^\s*"
        r"(\d+)\s+"                    # PEDIDO
        r"(\d+)\s+"                    # TRANSP.
        r"(\d{4,8})\s+"                # CODIGO
        r"(.+?)\s+"                    # DESCRIPCION
        r"(\d+(?:\.\d+)?)"             # CAJAS
        r"(?:\s*/\s*(\d+(?:\.\d+)?))?" # BOTELLAS opcionales
        r"\s+"
        r"([\d.,]+)\s+"                # PRECIO
        r"([\d.,]+)"                   # IMPORTE
        r"(?:\s+.*)?$"
    )

    coincidencia = patron.match(linea)

    if not coincidencia:
        return None

    pedido = coincidencia.group(1)
    transporte = coincidencia.group(2)
    codigo = coincidencia.group(3)
    producto = coincidencia.group(4).strip()

    cajas = convertir_cantidad(coincidencia.group(5))

    botellas = 0.0

    if coincidencia.group(6):
        botellas = convertir_cantidad(coincidencia.group(6))

    precio = limpiar_numero_monetario(coincidencia.group(7))
    importe = limpiar_numero_monetario(coincidencia.group(8))

    # Evitar falsos positivos.
    if not codigo:
        return None

    if not producto:
        return None

    return {
        "Pedido": pedido,
        "Transporte": transporte,
        "Código": normalizar_codigo(codigo),
        "Producto": producto,
        "Cajas": cajas,
        "Botellas": botellas,
        "Precio_Unitario_Caja": precio,
        "Importe_Total": importe,
    }


# ============================================================
# DETECTAR CLIENTE
# ============================================================

def detectar_encabezado_cliente(linea):
    """
    Detecta líneas como:

    0005 1224872905 Ruta: ML3E52 Fecha de Entrega: 06.10.2026

    Devuelve:
        número cliente
        ruta
    """

    patron = re.compile(
        r"^\s*"
        r"(\d{4})\s+"
        r"(\d+)\s+"
        r"Ruta:\s*"
        r"(ML3E\d+)"
        r".*Fecha de Entrega:",
        re.IGNORECASE
    )

    match = patron.match(linea)

    if match:
        return {
            "NumeroCliente": match.group(1),
            "Ruta": match.group(3).upper(),
        }

    return None


# ============================================================
# EXTRAER CLIENTE DEL BLOQUE
# ============================================================

def obtener_nombre_cliente(lineas, indice_encabezado):
    """
    En el PDF la estructura normalmente es:

    0005 1224872905 Ruta: ML3E52 ...
    URUS GYM
    CL 50 N 45 50 ...
    Giraldo
    3214285859
    ML3E62 Venta de Contado CO

    Por eso el nombre del establecimiento está
    inmediatamente después del encabezado.
    """

    indice = indice_encabezado + 1

    while indice < len(lineas):

        linea = lineas[indice].strip()

        if not linea:
            indice += 1
            continue

        # Si aparece otro cliente, no hay nombre válido.
        if detectar_encabezado_cliente(linea):
            break

        # No tomar líneas de sistema.
        if (
            "PEDIDO" in linea
            or "Venta de Contado" in linea
            or "Venta de Crédito" in linea
            or "Ruta:" in linea
            or "Fecha de Entrega:" in linea
        ):
            indice += 1
            continue

        # La primera línea no vacía después del encabezado
        # normalmente es el nombre del establecimiento.
        return linea

    return "CLIENTE SIN NOMBRE"


# ============================================================
# EXTRACCIÓN PRINCIPAL
# ============================================================

@st.cache_data
def extraer_datos_completos(bytes_pdf):

    registros = []

    rutas_detectadas = set()

    advertencias = []

    # --------------------------------------------
    # Abrir PDF desde bytes
    # --------------------------------------------

    with pdfplumber.open(io.BytesIO(bytes_pdf)) as pdf:

        ruta_actual = None
        cliente_actual = None
        numero_cliente_actual = None

        for numero_pagina, pagina in enumerate(pdf.pages, start=1):

            texto = pagina.extract_text()

            if not texto:
                continue

            lineas = texto.split("\n")

            # ==================================================
            # RECORRER LÍNEA POR LÍNEA
            # ==================================================

            for indice, linea in enumerate(lineas):

                linea_limpia = linea.strip()

                if not linea_limpia:
                    continue

                # ------------------------------------------------
                # 1. DETECTAR NUEVO CLIENTE / RUTA
                # ------------------------------------------------

                encabezado_cliente = detectar_encabezado_cliente(
                    linea_limpia
                )

                if encabezado_cliente:

                    ruta_actual = encabezado_cliente["Ruta"]

                    numero_cliente_actual = (
                        encabezado_cliente["NumeroCliente"]
                    )

                    rutas_detectadas.add(ruta_actual)

                    cliente_actual = obtener_nombre_cliente(
                        lineas,
                        indice
                    )

                    continue

                # ------------------------------------------------
                # 2. DETECTAR PRODUCTO
                # ------------------------------------------------

                producto = analizar_linea_producto(linea_limpia)

                if producto is not None:

                    producto["Ruta"] = ruta_actual
                    producto["Cliente"] = cliente_actual
                    producto["NumeroCliente"] = numero_cliente_actual
                    producto["Pagina"] = numero_pagina

                    registros.append(producto)

    # ============================================================
    # DATAFRAME
    # ============================================================

    columnas = [
        "Ruta",
        "NumeroCliente",
        "Cliente",
        "Código",
        "Producto",
        "Cajas",
        "Botellas",
        "Precio_Unitario_Caja",
        "Importe_Total",
        "Pedido",
        "Transporte",
        "Pagina",
    ]

    if registros:

        df = pd.DataFrame(registros)

        # Asegurar columnas
        for columna in columnas:
            if columna not in df.columns:
                df[columna] = ""

        df = df[columnas]

        # Eliminar duplicados reales
        df = df.drop_duplicates()

    else:

        df = pd.DataFrame(columns=columnas)

    # ============================================================
    # TOTALES POR RUTA
    # ============================================================

    totales_por_ruta = {}

    if not df.empty:

        totales = (
            df.groupby("Ruta")["Importe_Total"]
            .sum()
            .to_dict()
        )

        for ruta, total in totales.items():
            totales_por_ruta[ruta] = float(total)

    return df, totales_por_ruta, sorted(rutas_detectadas)


# ============================================================
# INTERFAZ
# ============================================================

with st.sidebar:

    st.header("📂 Archivo Nocturno")

    pdf_subido = st.file_uploader(
        "Sube el PDF de la planilla de rutas",
        type=["pdf"]
    )

    st.markdown("---")

    st.markdown("### Pasos")

    st.markdown(
        """
        1. Sube el PDF diario.
        2. Selecciona la ruta.
        3. Digita los productos devueltos.
        4. Consulta los clientes que recibieron cada producto.
        5. Cruza el valor con LiquiYa.
        """
    )


# ============================================================
# SI HAY PDF
# ============================================================

if pdf_subido is not None:

    with st.spinner(
        "🔎 Analizando PDF y estructurando pedidos..."
    ):

        contenido_pdf = pdf_subido.getvalue()

        (
            df_entregas,
            dict_totales,
            rutas_disponibles
        ) = extraer_datos_completos(
            contenido_pdf
        )

    st.success(
        f"✅ PDF analizado. "
        f"Se encontraron {len(df_entregas)} líneas de productos."
    )

    # ========================================================
    # INFORMACIÓN DE DIAGNÓSTICO
    # ========================================================

    with st.expander("🔍 Diagnóstico de lectura del PDF"):

        col1, col2, col3 = st.columns(3)

        col1.metric(
            "Líneas de productos",
            len(df_entregas)
        )

        col2.metric(
            "Clientes detectados",
            df_entregas["NumeroCliente"].nunique()
            if not df_entregas.empty
            else 0
        )

        col3.metric(
            "Rutas detectadas",
            len(rutas_disponibles)
        )

        if rutas_disponibles:

            st.write(
                "**Rutas encontradas:**",
                ", ".join(rutas_disponibles)
            )

        if not df_entregas.empty:

            st.dataframe(
                df_entregas.head(20),
                use_container_width=True
            )

    # ========================================================
    # SELECCIÓN DE RUTA
    # ========================================================

    if not rutas_disponibles:

        st.error(
            "❌ No se encontraron rutas en el PDF."
        )

        st.stop()

    st.markdown("---")

    col_s1, col_s2 = st.columns([1, 2])

    with col_s1:

        ruta_elegida = st.selectbox(
            "🎯 Selecciona la Ruta a Liquidar:",
            rutas_disponibles
        )

    # ========================================================
    # FILTRAR RUTA
    # ========================================================

    df_ruta_actual = df_entregas[
        df_entregas["Ruta"] == ruta_elegida
    ].copy()

    # ========================================================
    # TOTAL DE RUTA
    # ========================================================

    total_libro_ruta = dict_totales.get(
        ruta_elegida,
        0.0
    )

    st.markdown("---")

    m1, m2, m3 = st.columns(3)

    m1.metric(
        "🚚 Ruta Activa",
        ruta_elegida
    )

    m2.metric(
        f"💰 Total Ruta",
        f"${total_libro_ruta:,.2f}"
    )

    m3.metric(
        "🔄 Plataforma",
        "LiquiYa"
    )

    # ========================================================
    # DEVOLUCIONES
    # ========================================================

    st.subheader(
        f"🔄 Registro de Devoluciones - Ruta {ruta_elegida}"
    )

    st.write(
        """
        Digita el **código del producto** y la cantidad
        devuelta en **cajas** y/o **botellas**.
        """
    )

    df_base_dev = pd.DataFrame(
        [
            {
                "Código": "",
                "Cajas_Devueltas": 0.0,
                "Botellas_Devueltas": 0.0,
            }
        ]
    )

    devoluciones_ingresadas = st.data_editor(
        df_base_dev,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "Código": st.column_config.TextColumn(
                "Código del producto"
            ),
            "Cajas_Devueltas": st.column_config.NumberColumn(
                "Cajas devueltas",
                min_value=0.0,
                step=1.0
            ),
            "Botellas_Devueltas": st.column_config.NumberColumn(
                "Botellas devueltas",
                min_value=0.0,
                step=1.0
            ),
        }
    )

    # ========================================================
    # PROCESAR DEVOLUCIONES
    # ========================================================

    resumen_devoluciones = []

    total_valor_devuelto = 0.0

    for _, row in devoluciones_ingresadas.iterrows():

        codigo_raw = row["Código"]

        if (
            codigo_raw is None
            or str(codigo_raw).strip() == ""
            or str(codigo_raw).lower() == "none"
        ):
            continue

        codigo = normalizar_codigo(codigo_raw)

        cajas_dev = convertir_cantidad(
            row["Cajas_Devueltas"]
        )

        botellas_dev = convertir_cantidad(
            row["Botellas_Devueltas"]
        )

        if cajas_dev == 0 and botellas_dev == 0:
            continue

        # ----------------------------------------------------
        # Buscar producto en la ruta
        # ----------------------------------------------------

        coincidencias = df_ruta_actual[
            df_ruta_actual["Código"].astype(str)
            == codigo
        ]

        # ----------------------------------------------------
        # Producto no encontrado
        # ----------------------------------------------------

        if coincidencias.empty:

            resumen_devoluciones.append(
                {
                    "Código": codigo,
                    "Producto": "❌ NO ENCONTRADO EN LA RUTA",
                    "Cajas Dev.": cajas_dev,
                    "Botellas Dev.": botellas_dev,
                    "Valor Devolución": 0.0,
                    "Estado": "Código no encontrado",
                }
            )

            continue

        # ----------------------------------------------------
        # PRODUCTO ENCONTRADO
        # ----------------------------------------------------

        producto_nombre = coincidencias.iloc[0][
            "Producto"
        ]

        # ----------------------------------------------------
        # Calcular valor de devolución
        #
        # IMPORTANTE:
        #
        # No utilizamos un factor fijo de 30.
        #
        # El PDF nos da la relación entre:
        #
        # cajas / botellas
        #
        # y el IMPORTE real.
        #
        # Para cada línea buscamos un valor proporcional.
        # ----------------------------------------------------

        valor_estimado_devolucion = 0.0

        detalles_calculo = []

        for _, producto_original in coincidencias.iterrows():

            cajas_originales = float(
                producto_original["Cajas"]
            )

            botellas_originales = float(
                producto_original["Botellas"]
            )

            importe_original = float(
                producto_original["Importe_Total"]
            )

            precio_caja = float(
                producto_original["Precio_Unitario_Caja"]
            )

            # ----------------------------------------------
            # Si la línea tiene cajas
            # ----------------------------------------------

            if cajas_originales > 0:

                valor_por_caja = (
                    importe_original
                    / cajas_originales
                )

                detalles_calculo.append(
                    {
                        "tipo": "caja",
                        "valor": valor_por_caja
                    }
                )

            # ----------------------------------------------
            # Si la línea tiene botellas
            #
            # Para líneas tipo:
            #
            # 0 / 6
            #
            # el importe corresponde a esas unidades
            # indicadas en el documento.
            # ----------------------------------------------

            if botellas_originales > 0:

                valor_por_botella = (
                    importe_original
                    / botellas_originales
                )

                detalles_calculo.append(
                    {
                        "tipo": "botella",
                        "valor": valor_por_botella
                    }
                )

        # ----------------------------------------------------
        # Obtener valores disponibles
        # ----------------------------------------------------

        valores_caja = [
            x["valor"]
            for x in detalles_calculo
            if x["tipo"] == "caja"
        ]

        valores_botella = [
            x["valor"]
            for x in detalles_calculo
            if x["tipo"] == "botella"
        ]

        # ----------------------------------------------------
        # Valor para cajas devueltas
        # ----------------------------------------------------

        valor_cajas = 0.0

        if cajas_dev > 0:

            if valores_caja:

                valor_por_caja = sum(
                    valores_caja
                ) / len(valores_caja)

                valor_cajas = (
                    cajas_dev
                    * valor_por_caja
                )

            else:

                valor_cajas = 0.0

        # ----------------------------------------------------
        # Valor para botellas devueltas
        # ----------------------------------------------------

        valor_botellas = 0.0

        if botellas_dev > 0:

            if valores_botella:

                valor_por_botella = sum(
                    valores_botella
                ) / len(valores_botella)

                valor_botellas = (
                    botellas_dev
                    * valor_por_botella
                )

            else:

                # ------------------------------------------------
                # Si el PDF solo muestra cajas para ese producto,
                # todavía no inventamos un valor.
                # ------------------------------------------------

                valor_botellas = 0.0

        valor_estimado_devolucion = (
            valor_cajas
            + valor_botellas
        )

        total_valor_devuelto += (
            valor_estimado_devolucion
        )

        estado = "Calculado"

        if (
            botellas_dev > 0
            and not valores_botella
        ):
            estado = (
                "⚠️ Revisar: el PDF no muestra "
                "botellas para calcular esta devolución"
            )

        resumen_devoluciones.append(
            {
                "Código": codigo,
                "Producto": producto_nombre,
                "Cajas Dev.": cajas_dev,
                "Botellas Dev.": botellas_dev,
                "Valor Devolución":
                    valor_estimado_devolucion,
                "Estado": estado,
            }
        )

    # ========================================================
    # RESUMEN FINANCIERO
    # ========================================================

    st.markdown("---")

    st.subheader(
        "📋 Resumen Financiero de Devoluciones"
    )

    if resumen_devoluciones:

        df_resumen = pd.DataFrame(
            resumen_devoluciones
        )

        st.dataframe(
            df_resumen.style.format(
                {
                    "Valor Devolución":
                        "${:,.2f}"
                }
            ),
            use_container_width=True
        )

    else:

        st.info(
            "Digita un código y cantidades devueltas "
            "para realizar el cálculo."
        )

    # ========================================================
    # NETO
    # ========================================================

    neto_a_liquidar = (
        total_libro_ruta
        - total_valor_devuelto
    )

    c_res1, c_res2 = st.columns(2)

    c_res1.metric(
        "💸 Total Devoluciones a Descontar",
        f"- ${total_valor_devuelto:,.2f}",
    )

    c_res2.metric(
        "💰 Neto a Liquidar",
        f"${neto_a_liquidar:,.2f}",
    )

    # ========================================================
    # TRAZABILIDAD
    # ========================================================

    st.markdown("---")

    st.subheader(
        "🏪 Trazabilidad por Cliente y Tienda"
    )

    if resumen_devoluciones:

        for devolucion in resumen_devoluciones:

            codigo_dev = str(
                devolucion["Código"]
            )

            tiendas_afectadas = df_ruta_actual[
                df_ruta_actual["Código"].astype(str)
                == codigo_dev
            ]

            with st.expander(
                f"📦 {devolucion['Producto']} "
                f"— Código {codigo_dev}"
            ):

                if not tiendas_afectadas.empty:

                    st.write(
                        "### Clientes que recibieron este producto"
                    )

                    columnas_mostrar = [
                        "NumeroCliente",
                        "Cliente",
                        "Código",
                        "Producto",
                        "Cajas",
                        "Botellas",
                        "Precio_Unitario_Caja",
                        "Importe_Total",
                    ]

                    st.dataframe(
                        tiendas_afectadas[
                            columnas_mostrar
                        ].style.format(
                            {
                                "Precio_Unitario_Caja":
                                    "${:,.2f}",
                                "Importe_Total":
                                    "${:,.2f}",
                            }
                        ),
                        use_container_width=True
                    )

                else:

                    st.warning(
                        f"No se encontraron clientes "
                        f"para el código {codigo_dev}."
                    )

    else:

        st.info(
            "Cuando registres una devolución "
            "aquí aparecerán los clientes que "
            "recibieron ese producto."
        )

    # ========================================================
    # CATÁLOGO COMPLETO
    # ========================================================

    st.markdown("---")

    with st.expander(
        "🔍 Ver todos los productos extraídos"
    ):

        if not df_ruta_actual.empty:

            st.dataframe(
                df_ruta_actual[
                    [
                        "NumeroCliente",
                        "Cliente",
                        "Código",
                        "Producto",
                        "Cajas",
                        "Botellas",
                        "Precio_Unitario_Caja",
                        "Importe_Total",
                    ]
                ].style.format(
                    {
                        "Precio_Unitario_Caja":
                            "${:,.2f}",
                        "Importe_Total":
                            "${:,.2f}",
                    }
                ),
                use_container_width=True
            )

        else:

            st.info(
                "No se encontraron productos "
                "en esta ruta."
            )

    # ========================================================
    # REPORTE PARA IMPRIMIR
    # ========================================================

    st.markdown("---")

    st.subheader(
        "🖨️ Reporte Listo para Imprimir"
    )

    if st.button(
        "📄 Generar Vista de Impresión"
    ):

        filas_html = ""

        for r in resumen_devoluciones:

            filas_html += f"""
            <tr>
                <td>{r['Código']}</td>
                <td>{r['Producto']}</td>
                <td>{r['Cajas Dev.']}</td>
                <td>{r['Botellas Dev.']}</td>
                <td>${r['Valor Devolución']:,.2f}</td>
            </tr>
            """

        st.markdown(
            f"""
            <div style="
                background-color:white;
                color:black;
                padding:30px;
                border:2px solid #333;
                border-radius:10px;
            ">

                <h2 style="text-align:center;">
                    DISTRIBUCIONES INESCO S.A.S.
                </h2>

                <h3 style="text-align:center;">
                    REPORTE DE LIQUIDACIÓN
                    Y DEVOLUCIONES
                </h3>

                <hr>

                <p>
                    <b>Ruta:</b>
                    {ruta_elegida}
                </p>

                <p>
                    <b>Total de la Ruta:</b>
                    ${total_libro_ruta:,.2f}
                </p>

                <p>
                    <b>Total Devoluciones:</b>
                    - ${total_valor_devuelto:,.2f}
                </p>

                <h3>
                    NETO A LIQUIDAR:
                    ${neto_a_liquidar:,.2f}
                </h3>

                <hr>

                <h3>
                    Detalle de Devoluciones
                </h3>

                <table style="
                    width:100%;
                    border-collapse:collapse;
                ">

                    <tr>
                        <th>Código</th>
                        <th>Producto</th>
                        <th>Cajas</th>
                        <th>Botellas</th>
                        <th>Valor</th>
                    </tr>

                    {filas_html}

                </table>

                <br><br>

                <p>
                    ______________________________
                </p>

                <p>
                    Firma del Conductor / Liquidador
                </p>

            </div>
            """,
            unsafe_allow_html=True
        )

        st.info(
            "💡 Puedes utilizar Ctrl + P para "
            "imprimir o guardar como PDF."
        )

else:

    st.info(
        "👋 Sube el archivo PDF de la planilla "
        "en el panel izquierdo para comenzar."
    )
