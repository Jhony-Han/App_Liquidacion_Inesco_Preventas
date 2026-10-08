import re
import io
import pandas as pd
import pdfplumber
import streamlit as st


# ============================================================
# CONFIGURACIÓN DE STREAMLIT
# ============================================================

st.set_page_config(
    page_title="InescoRoute - Liquidación y Devoluciones",
    page_icon="🚚",
    layout="wide",
)


# ============================================================
# ENCABEZADO
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
    Convierte valores del PDF:

        32.500
        1.250.000
        $32.500

    a números.

    En el PDF el punto representa separador de miles.
    """

    if valor is None:
        return 0.0

    texto = str(valor).strip()

    if texto == "":
        return 0.0

    texto = texto.replace("$", "")
    texto = texto.replace(" ", "")

    # Formato colombiano:
    # 32.500 -> 32500
    if "." in texto and "," not in texto:
        texto = texto.replace(".", "")

    # Ejemplo:
    # 32.500,50 -> 32500.50
    elif "." in texto and "," in texto:
        texto = texto.replace(".", "")
        texto = texto.replace(",", ".")

    elif "," in texto:
        texto = texto.replace(",", ".")

    try:
        return float(texto)

    except ValueError:
        return 0.0


def normalizar_codigo(codigo):
    """
    Mantiene los ceros iniciales.

    Ejemplo:

        056841

    NO se convierte en:

        56841
    """

    if codigo is None:
        return ""

    return str(codigo).strip()


def convertir_cantidad(valor):
    """
    Convierte una cantidad a número.
    """

    if valor is None:
        return 0.0

    texto = str(valor).strip()

    if texto == "":
        return 0.0

    try:
        return float(texto.replace(",", "."))

    except ValueError:
        return 0.0


# ============================================================
# DETECTAR ENCABEZADO DE CLIENTE / RUTA
# ============================================================

def detectar_encabezado_cliente(linea):
    """
    Detecta líneas reales del PDF como:

    0001       1224896397       Ruta: ML3E52
    Fecha de Entrega: 06.10.2026

    o:

    0025       1210674829       Ruta: ML3E51
    """

    if not linea:
        return None

    linea = str(linea).strip()

    # --------------------------------------------------------
    # Buscar Ruta en cualquier posición de la línea.
    # --------------------------------------------------------

    match_ruta = re.search(
        r"Ruta\s*:\s*(ML3E\d+)",
        linea,
        re.IGNORECASE
    )

    if not match_ruta:
        return None

    ruta = match_ruta.group(1).upper()

    # --------------------------------------------------------
    # Número de cliente al principio.
    #
    # Ejemplo:
    #
    # 0001       1224896397       Ruta: ML3E52
    #
    # --------------------------------------------------------

    match_cliente = re.match(
        r"^\s*(\d{4})\b",
        linea
    )

    if match_cliente:

        numero_cliente = match_cliente.group(1)

    else:

        numero_cliente = ""

    return {
        "NumeroCliente": numero_cliente,
        "Ruta": ruta,
    }


# ============================================================
# OBTENER NOMBRE DEL CLIENTE
# ============================================================

def obtener_nombre_cliente(lineas, indice_encabezado):
    """
    Después del encabezado del cliente normalmente aparece:

        NOMBRE DEL NEGOCIO
        DIRECCIÓN
        CONTACTO
        TELÉFONO

    Por eso tomamos la primera línea útil posterior
    al encabezado.
    """

    indice = indice_encabezado + 1

    while indice < len(lineas):

        linea = lineas[indice].strip()

        if not linea:
            indice += 1
            continue

        # Si aparece otro cliente antes del nombre,
        # detenemos la búsqueda.
        if detectar_encabezado_cliente(linea):

            break

        # Evitar encabezados internos.
        if "PEDIDO" in linea.upper():

            indice += 1
            continue

        if "VENTA DE CONTADO" in linea.upper():

            indice += 1
            continue

        if "VENTA DE CRÉDITO" in linea.upper():

            indice += 1
            continue

        if "RUTA:" in linea.upper():

            indice += 1
            continue

        if "FECHA DE ENTREGA:" in linea.upper():

            indice += 1
            continue

        return linea

    return "CLIENTE SIN NOMBRE"


# ============================================================
# ANALIZAR LÍNEA DE PRODUCTO
# ============================================================

def analizar_linea_producto(linea):
    """
    Detecta líneas como:

    4057271111 402537712 056841 FLASHUVA 1 32.500 32.500

    4057501120 402537712 056706 QTC400
    0 / 6 25.100 12.550

    4057636900 402537712 160318 CC 400ML
    1 30.000 30.000
    """

    if not linea:

        return None

    linea = linea.strip()

    # --------------------------------------------------------
    # Estructura:
    #
    # PEDIDO
    # TRANSP.
    # CODIGO
    # DESCRIPCIÓN
    # CAJAS
    # / BOTELLAS
    # PRECIO
    # IMPORTE
    #
    # La descripción puede contener espacios.
    # --------------------------------------------------------

    patron = re.compile(
        r"^\s*"
        r"(\d+)\s+"                    # PEDIDO
        r"(\d+)\s+"                    # TRANSPORTE
        r"(\d{4,8})\s+"                # CODIGO
        r"(.+?)\s+"                    # DESCRIPCIÓN
        r"(\d+(?:\.\d+)?)"             # CAJAS
        r"(?:\s*/\s*(\d+(?:\.\d+)?))?" # BOTELLAS
        r"\s+"
        r"([\d.,]+)\s+"                # PRECIO
        r"([\d.,]+)"                   # IMPORTE
        r"(?:\s+.*)?$"
    )

    match = patron.match(linea)

    if not match:

        return None

    pedido = match.group(1)

    transporte = match.group(2)

    codigo = match.group(3)

    producto = match.group(4).strip()

    cajas = convertir_cantidad(
        match.group(5)
    )

    botellas = 0.0

    if match.group(6):

        botellas = convertir_cantidad(
            match.group(6)
        )

    precio = limpiar_numero_monetario(
        match.group(7)
    )

    importe = limpiar_numero_monetario(
        match.group(8)
    )

    if not codigo:

        return None

    if not producto:

        return None

    return {

        "Pedido": pedido,

        "Transporte": transporte,

        "Código": normalizar_codigo(
            codigo
        ),

        "Producto": producto,

        "Cajas": cajas,

        "Botellas": botellas,

        "Precio_Unitario_Caja": precio,

        "Importe_Total": importe,
    }


# ============================================================
# EXTRAER TODO EL PDF
# ============================================================

@st.cache_data
def extraer_datos_completos(bytes_pdf):

    registros = []

    rutas_detectadas = set()

    with pdfplumber.open(
        io.BytesIO(bytes_pdf)
    ) as pdf:

        # ----------------------------------------------------
        # Variables que conservan el estado actual
        # ----------------------------------------------------

        ruta_actual = None

        cliente_actual = None

        numero_cliente_actual = None

        # ----------------------------------------------------
        # Recorrer páginas
        # ----------------------------------------------------

        for numero_pagina, pagina in enumerate(
            pdf.pages,
            start=1
        ):

            texto = pagina.extract_text()

            if not texto:

                continue

            lineas = texto.split("\n")

            # ------------------------------------------------
            # Recorrer líneas
            # ------------------------------------------------

            for indice, linea in enumerate(
                lineas
            ):

                linea_limpia = linea.strip()

                if not linea_limpia:

                    continue

                # ==================================================
                # DETECTAR NUEVO CLIENTE
                # ==================================================

                encabezado = detectar_encabezado_cliente(
                    linea_limpia
                )

                if encabezado:

                    ruta_actual = encabezado[
                        "Ruta"
                    ]

                    numero_cliente_actual = encabezado[
                        "NumeroCliente"
                    ]

                    rutas_detectadas.add(
                        ruta_actual
                    )

                    cliente_actual = obtener_nombre_cliente(
                        lineas,
                        indice
                    )

                    continue

                # ==================================================
                # DETECTAR PRODUCTO
                # ==================================================

                producto = analizar_linea_producto(
                    linea_limpia
                )

                if producto is not None:

                    producto["Ruta"] = ruta_actual

                    producto["NumeroCliente"] = (
                        numero_cliente_actual
                    )

                    producto["Cliente"] = (
                        cliente_actual
                    )

                    producto["Pagina"] = (
                        numero_pagina
                    )

                    registros.append(
                        producto
                    )

    # ============================================================
    # CREAR DATAFRAME
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

        df = pd.DataFrame(
            registros
        )

        # Asegurar columnas

        for columna in columnas:

            if columna not in df.columns:

                df[columna] = ""

        df = df[columnas]

        # Eliminar duplicados exactos

        df = df.drop_duplicates()

    else:

        df = pd.DataFrame(
            columns=columnas
        )

    # ============================================================
    # TOTALES POR RUTA
    # ============================================================

    totales_por_ruta = {}

    if not df.empty:

        totales = (
            df.groupby("Ruta")[
                "Importe_Total"
            ]
            .sum()
            .to_dict()
        )

        for ruta, total in totales.items():

            totales_por_ruta[
                ruta
            ] = float(total)

    return (
        df,
        totales_por_ruta,
        sorted(rutas_detectadas)
    )


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header(
        "📂 Archivo Nocturno"
    )

    pdf_subido = st.file_uploader(
        "Sube el PDF de la planilla de rutas",
        type=["pdf"]
    )

    st.markdown("---")

    st.markdown(
        "### Pasos"
    )

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
# SI NO HAY PDF
# ============================================================

if pdf_subido is None:

    st.info(
        "👋 Sube el archivo PDF de la planilla "
        "en el panel izquierdo para comenzar."
    )

    st.stop()


# ============================================================
# LEER PDF
# ============================================================

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


# ============================================================
# MENSAJE DE RESULTADO
# ============================================================

st.success(
    f"✅ PDF analizado. "
    f"Se encontraron "
    f"{len(df_entregas)} líneas de productos."
)


# ============================================================
# DIAGNÓSTICO
# ============================================================

with st.expander(
    "🔍 Diagnóstico de lectura del PDF"
):

    col1, col2, col3 = st.columns(3)

    # --------------------------------------------------------
    # Líneas
    # --------------------------------------------------------

    with col1:

        st.metric(
            "Líneas de productos",
            len(df_entregas)
        )

    # --------------------------------------------------------
    # Clientes
    # --------------------------------------------------------

    with col2:

        if not df_entregas.empty:

            cantidad_clientes = (
                df_entregas[
                    "NumeroCliente"
                ]
                .replace("", pd.NA)
                .dropna()
                .nunique()
            )

        else:

            cantidad_clientes = 0

        st.metric(
            "Clientes detectados",
            cantidad_clientes
        )

    # --------------------------------------------------------
    # Rutas
    # --------------------------------------------------------

    with col3:

        st.metric(
            "Rutas detectadas",
            len(rutas_disponibles)
        )

    # --------------------------------------------------------
    # Mostrar rutas
    # --------------------------------------------------------

    if rutas_disponibles:

        st.write(
            "**Rutas encontradas:**"
        )

        st.write(
            ", ".join(
                rutas_disponibles
            )
        )

    else:

        st.warning(
            "⚠️ No se detectaron rutas."
        )

    # --------------------------------------------------------
    # Mostrar primeras filas
    # --------------------------------------------------------

    if not df_entregas.empty:

        st.write(
            "### Primeras líneas detectadas"
        )

        st.dataframe(
            df_entregas.head(20),
            use_container_width=True
        )


# ============================================================
# SI NO SE ENCONTRARON RUTAS
# ============================================================

if not rutas_disponibles:

    st.error(
        "❌ No se encontraron rutas en el PDF."
    )

    st.stop()


# ============================================================
# SELECCIONAR RUTA
# ============================================================

st.markdown("---")

st.subheader(
    "🎯 Selección de Ruta"
)

ruta_elegida = st.selectbox(
    "Selecciona la Ruta a Liquidar:",
    rutas_disponibles
)


# ============================================================
# FILTRAR RUTA
# ============================================================

df_ruta_actual = df_entregas[
    df_entregas["Ruta"]
    == ruta_elegida
].copy()


# ============================================================
# TOTAL DE LA RUTA
# ============================================================

total_libro_ruta = dict_totales.get(
    ruta_elegida,
    0.0
)


# ============================================================
# INFORMACIÓN GENERAL
# ============================================================

st.markdown("---")

m1, m2, m3 = st.columns(3)

with m1:

    st.metric(
        "🚚 Ruta Activa",
        ruta_elegida
    )

with m2:

    st.metric(
        "💰 Total Ruta",
        f"${total_libro_ruta:,.2f}"
    )

with m3:

    st.metric(
        "📦 Líneas de Productos",
        len(df_ruta_actual)
    )


# ============================================================
# TABLA DE PRODUCTOS DE LA RUTA
# ============================================================

with st.expander(
    "📦 Ver productos de la ruta"
):

    if df_ruta_actual.empty:

        st.warning(
            "No se encontraron productos "
            "para esta ruta."
        )

    else:

        tabla_ruta = df_ruta_actual[
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
        ].copy()

        st.dataframe(
            tabla_ruta.style.format(
                {
                    "Precio_Unitario_Caja":
                        "${:,.2f}",

                    "Importe_Total":
                        "${:,.2f}",
                }
            ),
            use_container_width=True
        )


# ============================================================
# DEVOLUCIONES
# ============================================================

st.markdown("---")

st.subheader(
    f"🔄 Registro de Devoluciones - {ruta_elegida}"
)

st.write(
    """
    Ingresa el código del producto y la cantidad
    devuelta.
    """
)


# ============================================================
# EDITOR DE DEVOLUCIONES
# ============================================================

df_devoluciones_base = pd.DataFrame(
    [
        {
            "Código": "",
            "Cajas_Devueltas": 0.0,
            "Botellas_Devueltas": 0.0,
        }
    ]
)


devoluciones_ingresadas = st.data_editor(
    df_devoluciones_base,

    num_rows="dynamic",

    use_container_width=True,

    column_config={

        "Código":
            st.column_config.TextColumn(
                "Código del producto"
            ),

        "Cajas_Devueltas":
            st.column_config.NumberColumn(
                "Cajas devueltas",
                min_value=0.0,
                step=1.0
            ),

        "Botellas_Devueltas":
            st.column_config.NumberColumn(
                "Botellas devueltas",
                min_value=0.0,
                step=1.0
            ),
    }
)


# ============================================================
# PROCESAR DEVOLUCIONES
# ============================================================

resumen_devoluciones = []

total_valor_devuelto = 0.0


for _, row in devoluciones_ingresadas.iterrows():

    codigo_raw = row["Código"]

    # --------------------------------------------------------
    # Ignorar fila vacía
    # --------------------------------------------------------

    if codigo_raw is None:

        continue

    codigo_texto = str(
        codigo_raw
    ).strip()

    if codigo_texto == "":

        continue

    if codigo_texto.lower() == "none":

        continue

    # --------------------------------------------------------
    # Código
    # --------------------------------------------------------

    codigo = normalizar_codigo(
        codigo_texto
    )

    # --------------------------------------------------------
    # Cantidades
    # --------------------------------------------------------

    cajas_dev = convertir_cantidad(
        row["Cajas_Devueltas"]
    )

    botellas_dev = convertir_cantidad(
        row["Botellas_Devueltas"]
    )

    if cajas_dev == 0 and botellas_dev == 0:

        continue

    # ========================================================
    # BUSCAR CÓDIGO EN LA RUTA
    # ========================================================

    coincidencias = df_ruta_actual[
        df_ruta_actual["Código"].astype(str)
        == codigo
    ]

    # ========================================================
    # NO ENCONTRADO
    # ========================================================

    if coincidencias.empty:

        resumen_devoluciones.append(
            {
                "Código": codigo,

                "Producto":
                    "❌ NO ENCONTRADO EN LA RUTA",

                "Cajas Dev.":
                    cajas_dev,

                "Botellas Dev.":
                    botellas_dev,

                "Valor Devolución":
                    0.0,

                "Estado":
                    "Código no encontrado",
            }
        )

        continue

    # ========================================================
    # NOMBRE DEL PRODUCTO
    # ========================================================

    producto_nombre = str(
        coincidencias.iloc[0][
            "Producto"
        ]
    )


    # ========================================================
    # CÁLCULO DEL VALOR DEVUELTO
    # ========================================================

    valor_cajas = 0.0

    valor_botellas = 0.0


    # --------------------------------------------------------
    # Valores encontrados en PDF
    # --------------------------------------------------------

    valores_por_caja = []

    valores_por_botella = []


    for _, producto_original in coincidencias.iterrows():

        cajas_originales = float(
            producto_original[
                "Cajas"
            ]
        )

        botellas_originales = float(
            producto_original[
                "Botellas"
            ]
        )

        importe_original = float(
            producto_original[
                "Importe_Total"
            ]
        )

        # ----------------------------------------------------
        # Si el registro corresponde a cajas
        # ----------------------------------------------------

        if cajas_originales > 0:

            valor_por_caja = (
                importe_original
                / cajas_originales
            )

            valores_por_caja.append(
                valor_por_caja
            )

        # ----------------------------------------------------
        # Si el registro corresponde a botellas
        # ----------------------------------------------------

        if botellas_originales > 0:

            valor_por_botella = (
                importe_original
                / botellas_originales
            )

            valores_por_botella.append(
                valor_por_botella
            )


    # ========================================================
    # VALOR DE CAJAS
    # ========================================================

    if cajas_dev > 0:

        if valores_por_caja:

            valor_promedio_caja = (
                sum(valores_por_caja)
                / len(valores_por_caja)
            )

            valor_cajas = (
                cajas_dev
                * valor_promedio_caja
            )


    # ========================================================
    # VALOR DE BOTELLAS
    # ========================================================

    if botellas_dev > 0:

        if valores_por_botella:

            valor_promedio_botella = (
                sum(valores_por_botella)
                / len(valores_por_botella)
            )

            valor_botellas = (
                botellas_dev
                * valor_promedio_botella
            )


    # ========================================================
    # TOTAL DEVOLUCIÓN
    # ========================================================

    valor_devolucion = (
        valor_cajas
        + valor_botellas
    )


    total_valor_devuelto += (
        valor_devolucion
    )


    # ========================================================
    # ESTADO
    # ========================================================

    estado = "✅ Calculado"


    if (
        botellas_dev > 0
        and not valores_por_botella
    ):

        estado = (
            "⚠️ Revisar: "
            "no hay presentación por botella "
            "identificada en el PDF"
        )


    # ========================================================
    # GUARDAR RESULTADO
    # ========================================================

    resumen_devoluciones.append(
        {
            "Código":
                codigo,

            "Producto":
                producto_nombre,

            "Cajas Dev.":
                cajas_dev,

            "Botellas Dev.":
                botellas_dev,

            "Valor Devolución":
                valor_devolucion,

            "Estado":
                estado,
        }
    )


# ============================================================
# RESUMEN DE DEVOLUCIONES
# ============================================================

st.markdown("---")

st.subheader(
    "📋 Resumen de Devoluciones"
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
        "Ingresa al menos un código y una "
        "cantidad para calcular la devolución."
    )


# ============================================================
# LIQUIDACIÓN
# ============================================================

neto_a_liquidar = (
    total_libro_ruta
    - total_valor_devuelto
)


st.markdown("---")

st.subheader(
    "💰 Liquidación"
)


col_liq1, col_liq2, col_liq3 = st.columns(3)


with col_liq1:

    st.metric(
        "Total Libro",
        f"${total_libro_ruta:,.2f}"
    )


with col_liq2:

    st.metric(
        "Devoluciones",
        f"- ${total_valor_devuelto:,.2f}"
    )


with col_liq3:

    st.metric(
        "Neto a Liquidar",
        f"${neto_a_liquidar:,.2f}"
    )


# ============================================================
# TRAZABILIDAD
# ============================================================

st.markdown("---")

st.subheader(
    "🏪 Clientes que recibieron los productos devueltos"
)


if resumen_devoluciones:

    for devolucion in resumen_devoluciones:

        codigo_dev = str(
            devolucion["Código"]
        )

        producto_dev = str(
            devolucion["Producto"]
        )

        # ----------------------------------------------------
        # Buscar todas las apariciones
        # ----------------------------------------------------

        tiendas_afectadas = df_ruta_actual[
            df_ruta_actual["Código"].astype(str)
            == codigo_dev
        ]

        with st.expander(
            f"📦 {producto_dev} "
            f"— Código {codigo_dev}"
        ):

            if tiendas_afectadas.empty:

                st.warning(
                    "No se encontraron clientes "
                    "para este código."
                )

            else:

                st.write(
                    "### Clientes que recibieron este producto"
                )

                columnas_cliente = [

                    "NumeroCliente",

                    "Cliente",

                    "Código",

                    "Producto",

                    "Cajas",

                    "Botellas",

                    "Precio_Unitario_Caja",

                    "Importe_Total",
                ]

                tabla_clientes = (
                    tiendas_afectadas[
                        columnas_cliente
                    ]
                    .copy()
                )

                st.dataframe(
                    tabla_clientes.style.format(
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
        "Cuando registres una devolución, "
        "aquí aparecerán automáticamente "
        "los clientes que recibieron ese producto."
    )


# ============================================================
# BUSCADOR MANUAL DE PRODUCTOS
# ============================================================

st.markdown("---")

st.subheader(
    "🔎 Buscar un producto en la ruta"
)

codigo_busqueda = st.text_input(
    "Escribe un código de producto:",
    placeholder="Ejemplo: 160318"
)


if codigo_busqueda.strip():

    codigo_busqueda = normalizar_codigo(
        codigo_busqueda
    )

    resultados_busqueda = df_ruta_actual[
        df_ruta_actual["Código"].astype(str)
        == codigo_busqueda
    ]

    if resultados_busqueda.empty:

        st.warning(
            f"❌ El código {codigo_busqueda} "
            f"no aparece en la ruta {ruta_elegida}."
        )

    else:

        st.success(
            f"✅ El código {codigo_busqueda} "
            f"aparece {len(resultados_busqueda)} vez/veces "
            f"en la ruta {ruta_elegida}."
        )

        columnas_busqueda = [

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
            resultados_busqueda[
                columnas_busqueda
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


# ============================================================
# REPORTE PARA IMPRESIÓN
# ============================================================

st.markdown("---")

st.subheader(
    "🖨️ Reporte de Liquidación"
)


if st.button(
    "📄 Generar Vista de Impresión"
):

    filas_html = ""

    for devolucion in resumen_devoluciones:

        filas_html += f"""
        <tr>
            <td>{devolucion['Código']}</td>

            <td>{devolucion['Producto']}</td>

            <td>{devolucion['Cajas Dev.']}</td>

            <td>{devolucion['Botellas Dev.']}</td>

            <td>
                ${devolucion['Valor Devolución']:,.2f}
            </td>

            <td>{devolucion['Estado']}</td>
        </tr>
        """

    st.markdown(
        f"""
        <div style="
            background:white;
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
                <b>Total Libro:</b>
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
                    <th>Estado</th>
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
        "💡 Utiliza Ctrl + P para imprimir "
        "o guardar el reporte como PDF."
    )
