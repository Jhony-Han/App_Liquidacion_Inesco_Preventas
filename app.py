import io
import re
from datetime import datetime

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
# ESTILOS
# ============================================================

st.markdown(
    """
    <style>

    .titulo-principal {
        text-align: center;
        margin-bottom: 0;
    }

    .subtitulo {
        text-align: center;
        color: #888;
        margin-top: 0;
    }

    .tarjeta {
        padding: 18px;
        border-radius: 12px;
        border: 1px solid #333;
        margin-bottom: 15px;
    }

    .total-grande {
        font-size: 28px;
        font-weight: bold;
    }

    .reporte-impresion {
        background: white;
        color: black;
        padding: 35px;
        border: 1px solid #aaa;
        border-radius: 8px;
        max-width: 1100px;
        margin: auto;
    }

    .reporte-impresion table {
        width: 100%;
        border-collapse: collapse;
        margin-top: 15px;
    }

    .reporte-impresion th {
        background: #eeeeee;
        color: black;
        border: 1px solid #999;
        padding: 8px;
        text-align: left;
    }

    .reporte-impresion td {
        border: 1px solid #bbb;
        padding: 8px;
    }

    .reporte-impresion .derecha {
        text-align: right;
    }

    .reporte-impresion .centro {
        text-align: center;
    }

    .reporte-impresion .total-final {
        font-size: 22px;
        font-weight: bold;
        text-align: right;
    }

    @media print {

        header,
        footer,
        [data-testid="stSidebar"],
        [data-testid="stToolbar"] {
            display: none !important;
        }

        .reporte-impresion {
            border: none !important;
            box-shadow: none !important;
            max-width: 100% !important;
        }

    }

    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# ENCABEZADO
# ============================================================

st.markdown(
    """
    <h1 class="titulo-principal">
        🚚 DISTRIBUCIONES INESCO S.A.S.
    </h1>

    <h3 class="subtitulo">
        Módulo de Liquidación de Rutas y Cruce con LiquiYa
    </h3>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# FUNCIONES GENERALES
# ============================================================

def limpiar_numero_monetario(valor):
    """
    Convierte valores del PDF.

    Ejemplos:

        32.500       -> 32500
        1.250.000    -> 1250000
        7.110.218    -> 7110218
    """

    if valor is None:
        return 0.0

    texto = str(valor).strip()

    if not texto:
        return 0.0

    texto = texto.replace("$", "")
    texto = texto.replace(" ", "")

    # Formato colombiano:
    # 7.110.218
    if "." in texto and "," not in texto:
        texto = texto.replace(".", "")

    # Formato:
    # 7.110.218,50
    elif "." in texto and "," in texto:
        texto = texto.replace(".", "")
        texto = texto.replace(",", ".")

    elif "," in texto:
        texto = texto.replace(",", ".")

    try:
        return float(texto)

    except ValueError:
        return 0.0


def convertir_cantidad(valor):
    """
    Convierte cantidades.
    """

    if valor is None:
        return 0.0

    texto = str(valor).strip()

    if not texto:
        return 0.0

    texto = texto.replace(",", ".")

    try:
        return float(texto)

    except ValueError:
        return 0.0


def normalizar_codigo(codigo):
    """
    Conserva el código para mostrarlo.

    Ejemplo:

        056706 -> 056706
        160318 -> 160318
    """

    if codigo is None:
        return ""

    texto = str(codigo).strip()

    # Si viene como 56706.0
    if texto.endswith(".0"):
        texto = texto[:-2]

    return texto


def codigo_clave(codigo):
    """
    Crea una clave de búsqueda que ignora ceros iniciales.

    056706 -> 56706
    56706  -> 56706
    160318 -> 160318

    Esto permite buscar el producto aunque el usuario
    escriba o no los ceros iniciales.
    """

    texto = normalizar_codigo(codigo)

    if not texto:
        return ""

    solo_digitos = re.sub(
        r"\D",
        "",
        texto
    )

    if not solo_digitos:
        return ""

    try:
        return str(
            int(solo_digitos)
        )

    except ValueError:
        return solo_digitos


# ============================================================
# DETECTAR RUTA EN ENCABEZADOS
# ============================================================

def detectar_ruta_en_linea(linea):
    """
    Busca específicamente:

        Ruta: ML3E53

    No busca simplemente ML3E porque dentro del PDF
    también aparecen códigos como ML3E63.
    """

    if not linea:
        return None

    resultado = re.search(
        r"Ruta\s*:\s*(ML3E\d+)",
        linea,
        re.IGNORECASE
    )

    if resultado:
        return resultado.group(1).upper()

    return None


# ============================================================
# DETECTAR CLIENTE
# ============================================================

def detectar_cliente_encabezado(linea):
    """
    Detecta estructuras como:

    0009 1224773744 Ruta: ML3E53 Fecha de Entrega: 06.10.2026

    Devuelve:

        NumeroCliente
        Ruta
    """

    if not linea:
        return None

    ruta = detectar_ruta_en_linea(linea)

    if not ruta:
        return None

    numero_cliente = ""

    resultado_cliente = re.match(
        r"^\s*(\d{4})\b",
        linea
    )

    if resultado_cliente:
        numero_cliente = (
            resultado_cliente.group(1)
        )

    return {
        "NumeroCliente": numero_cliente,
        "Ruta": ruta,
    }


# ============================================================
# OBTENER NOMBRE DEL CLIENTE
# ============================================================

def obtener_nombre_cliente(
    lineas,
    indice_encabezado
):
    """
    En el PDF normalmente la línea siguiente contiene:

        NOMBRE DEL CLIENTE       DIRECCIÓN

    Separamos usando los espacios grandes.
    """

    indice = indice_encabezado + 1

    while indice < len(lineas):

        linea = lineas[indice].strip()

        if not linea:
            indice += 1
            continue

        # Si encontramos otro cliente, no hay nombre.
        if detectar_cliente_encabezado(linea):
            return "CLIENTE SIN NOMBRE"

        # Ignorar algunos encabezados.
        texto_mayus = linea.upper()

        if (
            "PEDIDO" in texto_mayus
            or "RUTA:" in texto_mayus
            or "FECHA DE ENTREGA:" in texto_mayus
        ):
            indice += 1
            continue

        # Separar nombre y dirección.
        partes = re.split(
            r"\s{2,}",
            linea
        )

        if partes:

            nombre = partes[0].strip()

            if nombre:
                return nombre

        indice += 1

    return "CLIENTE SIN NOMBRE"


# ============================================================
# ANALIZAR PRODUCTO
# ============================================================

def analizar_linea_producto(linea):
    """
    Detecta una línea real de producto.

    Ejemplo:

    4057637803 402537714 160318 CC 400ML
    2 30.000 60.000

    También:

    4057624469 402537714 056705 QTC350
    0 / 15 62.500 31.250
    """

    if not linea:
        return None

    linea = linea.strip()

    # --------------------------------------------------------
    # Pedido
    # Transporte
    # Código
    # Descripción
    # Cantidad
    # Precio
    # Importe
    # --------------------------------------------------------

    patron = re.compile(
        r"^\s*"
        r"(\d{8,12})\s+"
        r"(\d{7,12})\s+"
        r"(\d{4,8})\s+"
        r"(.+?)\s+"
        r"("
        r"(?:\d+(?:[.,]\d+)?\s*/\s*\d+(?:[.,]\d+)?)"
        r"|"
        r"(?:\d+(?:[.,]\d+)?)"
        r")"
        r"\s+"
        r"([\d.,]+)"
        r"\s+"
        r"([\d.,]+)"
        r"(?:\s+.*)?$",
        re.IGNORECASE
    )

    resultado = patron.match(linea)

    if not resultado:
        return None

    pedido = resultado.group(1)

    transporte = resultado.group(2)

    codigo = resultado.group(3)

    descripcion = resultado.group(4).strip()

    cantidad_texto = resultado.group(5)

    precio_texto = resultado.group(6)

    importe_texto = resultado.group(7)

    # --------------------------------------------------------
    # CANTIDADES
    # --------------------------------------------------------

    cajas = 0.0
    botellas = 0.0

    if "/" in cantidad_texto:

        partes = cantidad_texto.split("/")

        cajas = convertir_cantidad(
            partes[0].strip()
        )

        botellas = convertir_cantidad(
            partes[1].strip()
        )

    else:

        cajas = convertir_cantidad(
            cantidad_texto
        )

    # --------------------------------------------------------
    # VALORES
    # --------------------------------------------------------

    precio = limpiar_numero_monetario(
        precio_texto
    )

    importe = limpiar_numero_monetario(
        importe_texto
    )

    return {
        "Pedido": pedido,
        "Transporte": transporte,
        "Código": normalizar_codigo(codigo),
        "Codigo_Key": codigo_clave(codigo),
        "Producto": descripcion,
        "Cajas": cajas,
        "Botellas": botellas,
        "Precio_Unitario": precio,
        "Importe_Total": importe,
    }


# ============================================================
# EXTRAER TOTAL DE CONTADO DE UNA RUTA
# ============================================================

def extraer_total_contado(linea):
    """
    IMPORTANTE:

    Solo acepta el resumen general:

        Total Venta de Contado CO 7.110.218

    NO acepta:

        Total a Cobrar Cont/Prom/RecCredito/ComAdm 151.600

    porque ese es el total de un cliente.
    """

    if not linea:
        return None

    patron = re.compile(
        r"^\s*"
        r"Total\s+"
        r"Venta\s+de\s+Contado"
        r"(?:\s+CO)?"
        r"\s+"
        r"([\d.,]+)"
        r"\s*$",
        re.IGNORECASE
    )

    resultado = patron.match(linea)

    if not resultado:
        return None

    return limpiar_numero_monetario(
        resultado.group(1)
    )


# ============================================================
# EXTRAER CRÉDITO
# ============================================================

def extraer_total_credito(linea):

    if not linea:
        return None

    patron = re.compile(
        r"^\s*"
        r"Total\s+"
        r"Vta\s+CréditoFormal\s+CO"
        r"\s+"
        r"([\d.,]+)"
        r"\s*$",
        re.IGNORECASE
    )

    resultado = patron.match(linea)

    if not resultado:
        return None

    return limpiar_numero_monetario(
        resultado.group(1)
    )


# ============================================================
# EXTRAER TODO EL PDF
# ============================================================

@st.cache_data
def extraer_datos_completos(
    contenido_pdf
):

    registros = []

    rutas_detectadas = set()

    totales_por_ruta = {}

    creditos_por_ruta = {}

    clientes_por_ruta = {}

    # --------------------------------------------------------
    # PDF
    # --------------------------------------------------------

    with pdfplumber.open(
        io.BytesIO(contenido_pdf)
    ) as pdf:

        ruta_actual = None

        cliente_actual = "CLIENTE SIN NOMBRE"

        numero_cliente_actual = ""

        # ----------------------------------------------------
        # PÁGINAS
        # ----------------------------------------------------

        for numero_pagina, pagina in enumerate(
            pdf.pages,
            start=1
        ):

            texto = pagina.extract_text()

            if not texto:
                continue

            lineas = texto.split("\n")

            # =================================================
            # PRIMERA PASADA:
            # DETECTAR RUTA DE LA PÁGINA
            # =================================================

            rutas_pagina = set()

            for linea in lineas:

                ruta_linea = detectar_ruta_en_linea(
                    linea
                )

                if ruta_linea:

                    rutas_pagina.add(
                        ruta_linea
                    )

            # =================================================
            # PROCESAR LÍNEAS
            # =================================================

            for indice, linea in enumerate(
                lineas
            ):

                linea_limpia = linea.strip()

                if not linea_limpia:
                    continue

                # =================================================
                # RUTA / CLIENTE
                # =================================================

                encabezado = detectar_cliente_encabezado(
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

                    clientes_por_ruta.setdefault(
                        ruta_actual,
                        set()
                    )

                    if numero_cliente_actual:

                        clientes_por_ruta[
                            ruta_actual
                        ].add(
                            numero_cliente_actual
                        )

                    continue

                # =================================================
                # TOTAL VENTA DE CONTADO
                # =================================================

                total_contado = extraer_total_contado(
                    linea_limpia
                )

                if total_contado is not None:

                    ruta_para_total = (
                        ruta_actual
                    )

                    # Si la página tiene una sola ruta,
                    # podemos utilizarla como respaldo.
                    if (
                        not ruta_para_total
                        and len(rutas_pagina) == 1
                    ):

                        ruta_para_total = (
                            list(rutas_pagina)[0]
                        )

                    if ruta_para_total:

                        totales_por_ruta[
                            ruta_para_total
                        ] = total_contado

                        rutas_detectadas.add(
                            ruta_para_total
                        )

                    continue

                # =================================================
                # TOTAL CRÉDITO
                # =================================================

                total_credito = extraer_total_credito(
                    linea_limpia
                )

                if total_credito is not None:

                    ruta_para_credito = (
                        ruta_actual
                    )

                    if (
                        not ruta_para_credito
                        and len(rutas_pagina) == 1
                    ):

                        ruta_para_credito = (
                            list(rutas_pagina)[0]
                        )

                    if ruta_para_credito:

                        creditos_por_ruta[
                            ruta_para_credito
                        ] = total_credito

                    continue

                # =================================================
                # PRODUCTO
                # =================================================

                producto = analizar_linea_producto(
                    linea_limpia
                )

                if producto is None:
                    continue

                # Si todavía no tenemos ruta, no inventamos una.
                if not ruta_actual:
                    continue

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
    # DATAFRAME
    # ============================================================

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

    if registros:

        df = pd.DataFrame(
            registros
        )

        for columna in columnas:

            if columna not in df.columns:

                df[columna] = ""

        df = df[columnas]

    else:

        df = pd.DataFrame(
            columns=columnas
        )

    # ============================================================
    # ORDENAR
    # ============================================================

    if not df.empty:

        df = df.sort_values(
            by=[
                "Ruta",
                "NumeroCliente",
                "Código",
            ],
            kind="stable"
        ).reset_index(
            drop=True
        )

    # ============================================================
    # RUTAS
    # ============================================================

    rutas = sorted(
        rutas_detectadas
    )

    return (
        df,
        totales_por_ruta,
        creditos_por_ruta,
        rutas,
        clientes_por_ruta,
    )


# ============================================================
# CALCULAR VALORES DE DEVOLUCIÓN
# ============================================================

def calcular_valor_devolucion(
    fila_producto,
    cajas_devueltas,
    botellas_devueltas
):
    """
    Calcula usando el IMPORTE REAL del registro.

    NO utiliza:

        cantidad * precio

    cuando el importe del PDF indica otra cosa.

    Tampoco utiliza un factor fijo de 30.
    """

    importe_original = float(
        fila_producto["Importe_Total"]
    )

    cajas_originales = float(
        fila_producto["Cajas"]
    )

    botellas_originales = float(
        fila_producto["Botellas"]
    )

    valor_cajas = 0.0

    valor_botellas = 0.0

    # --------------------------------------------------------
    # SI LA LÍNEA ES POR CAJAS
    # --------------------------------------------------------

    if (
        cajas_devueltas > 0
        and cajas_originales > 0
    ):

        valor_por_caja = (
            importe_original
            / cajas_originales
        )

        valor_cajas = (
            cajas_devueltas
            * valor_por_caja
        )

    # --------------------------------------------------------
    # SI LA LÍNEA ES POR BOTELLAS
    # --------------------------------------------------------

    if (
        botellas_devueltas > 0
        and botellas_originales > 0
    ):

        valor_por_botella = (
            importe_original
            / botellas_originales
        )

        valor_botellas = (
            botellas_devueltas
            * valor_por_botella
        )

    return (
        valor_cajas
        + valor_botellas
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
        """
        ### Pasos

        **1.** Sube el PDF diario.

        **2.** Selecciona la ruta.

        **3.** Busca el código devuelto.

        **4.** Selecciona el cliente si es necesario.

        **5.** Registra cajas/botellas.

        **6.** Genera el comprobante.
        """
    )


# ============================================================
# SIN PDF
# ============================================================

if pdf_subido is None:

    st.info(
        "👋 Sube el PDF de la planilla de rutas "
        "para comenzar."
    )

    st.stop()


# ============================================================
# LEER PDF
# ============================================================

with st.spinner(
    "🔎 Analizando PDF..."
):

    contenido_pdf = (
        pdf_subido.getvalue()
    )

    (
        df_entregas,
        totales_por_ruta,
        creditos_por_ruta,
        rutas_disponibles,
        clientes_por_ruta,
    ) = extraer_datos_completos(
        contenido_pdf
    )


# ============================================================
# RESULTADO
# ============================================================

st.success(
    f"✅ PDF analizado correctamente. "
    f"Se encontraron "
    f"{len(df_entregas)} líneas de productos."
)


# ============================================================
# DIAGNÓSTICO
# ============================================================

with st.expander(
    "🔍 Diagnóstico de lectura"
):

    c1, c2, c3 = st.columns(3)

    with c1:

        st.metric(
            "Líneas de productos",
            len(df_entregas)
        )

    with c2:

        if not df_entregas.empty:

            clientes = (
                df_entregas[
                    "NumeroCliente"
                ]
                .replace(
                    "",
                    pd.NA
                )
                .dropna()
                .nunique()
            )

        else:

            clientes = 0

        st.metric(
            "Clientes",
            clientes
        )

    with c3:

        st.metric(
            "Rutas",
            len(rutas_disponibles)
        )

    st.write(
        "**Rutas detectadas:**"
    )

    if rutas_disponibles:

        st.write(
            ", ".join(
                rutas_disponibles
            )
        )

    else:

        st.warning(
            "No se detectaron rutas."
        )

    # --------------------------------------------------------
    # TOTALES DETECTADOS
    # --------------------------------------------------------

    if totales_por_ruta:

        st.write(
            "### 💰 Totales de Venta de Contado detectados"
        )

        filas_totales = []

        for ruta in sorted(
            totales_por_ruta
        ):

            filas_totales.append(
                {
                    "Ruta":
                        ruta,

                    "Venta de Contado":
                        totales_por_ruta[ruta],

                    "Crédito":
                        creditos_por_ruta.get(
                            ruta,
                            0.0
                        ),

                    "Clientes":
                        len(
                            clientes_por_ruta.get(
                                ruta,
                                set()
                            )
                        ),
                }
            )

        df_totales = pd.DataFrame(
            filas_totales
        )

        st.dataframe(
            df_totales.style.format(
                {
                    "Venta de Contado":
                        "${:,.2f}",

                    "Crédito":
                        "${:,.2f}",
                }
            ),
            use_container_width=True
        )

    # --------------------------------------------------------
    # MUESTRA DE PRODUCTOS
    # --------------------------------------------------------

    if not df_entregas.empty:

        st.write(
            "### 📦 Muestra de productos detectados"
        )

        st.dataframe(
            df_entregas.head(20),
            use_container_width=True
        )


# ============================================================
# VERIFICAR RUTAS
# ============================================================

if not rutas_disponibles:

    st.error(
        "❌ No se pudieron detectar las rutas."
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
    "Selecciona la ruta a liquidar:",
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
# TOTAL REAL DEL LIBRO
# ============================================================

total_libro_ruta = float(
    totales_por_ruta.get(
        ruta_elegida,
        0.0
    )
)

total_credito_ruta = float(
    creditos_por_ruta.get(
        ruta_elegida,
        0.0
    )
)


# ============================================================
# MOSTRAR RESUMEN
# ============================================================

st.markdown("---")

m1, m2, m3 = st.columns(3)

with m1:

    st.metric(
        "🚚 Ruta",
        ruta_elegida
    )

with m2:

    st.metric(
        "💰 Venta de Contado",
        f"${total_libro_ruta:,.2f}"
    )

with m3:

    st.metric(
        "📦 Líneas",
        len(df_ruta_actual)
    )


# ============================================================
# VALIDACIÓN
# ============================================================

with st.expander(
    "🧮 Validación del valor leído"
):

    st.write(
        f"**Ruta seleccionada:** {ruta_elegida}"
    )

    st.write(
        f"**Venta de Contado según el PDF:** "
        f"${total_libro_ruta:,.2f}"
    )

    st.write(
        f"**Vta CréditoFormal según el PDF:** "
        f"${total_credito_ruta:,.2f}"
    )

    st.success(
        "Este valor viene exclusivamente de la línea "
        "'Total Venta de Contado' del resumen de la ruta. "
        "No se suman los 'Total a Cobrar' de cada cliente."
    )


# ============================================================
# CATÁLOGO
# ============================================================

with st.expander(
    "📦 Ver productos leídos de esta ruta"
):

    if df_ruta_actual.empty:

        st.warning(
            "No hay productos para esta ruta."
        )

    else:

        columnas_catalogo = [
            "NumeroCliente",
            "Cliente",
            "Código",
            "Producto",
            "Cajas",
            "Botellas",
            "Precio_Unitario",
            "Importe_Total",
        ]

        st.dataframe(
            df_ruta_actual[
                columnas_catalogo
            ].style.format(
                {
                    "Precio_Unitario":
                        "${:,.2f}",

                    "Importe_Total":
                        "${:,.2f}",
                }
            ),
            use_container_width=True
        )


# ============================================================
# BUSCAR PRODUCTO
# ============================================================

st.markdown("---")

st.subheader(
    "🔎 Buscar producto devuelto"
)

codigo_busqueda = st.text_input(
    "Código del producto:",
    placeholder="Ejemplo: 160318 o 056706"
)


if codigo_busqueda.strip():

    codigo_busqueda = normalizar_codigo(
        codigo_busqueda
    )

    clave_busqueda = codigo_clave(
        codigo_busqueda
    )

    coincidencias = df_ruta_actual[
        df_ruta_actual["Codigo_Key"]
        == clave_busqueda
    ].copy()

    # --------------------------------------------------------
    # ENCONTRADO
    # --------------------------------------------------------

    if not coincidencias.empty:

        nombre_producto = (
            coincidencias.iloc[0]["Producto"]
        )

        st.success(
            f"✅ Producto encontrado: "
            f"{nombre_producto}"
        )

        st.write(
            f"**Código ingresado:** "
            f"`{codigo_busqueda}`"
        )

        st.write(
            f"**Coincidencias en {ruta_elegida}:** "
            f"{len(coincidencias)}"
        )

        columnas_clientes = [
            "NumeroCliente",
            "Cliente",
            "Código",
            "Producto",
            "Cajas",
            "Botellas",
            "Precio_Unitario",
            "Importe_Total",
        ]

        st.dataframe(
            coincidencias[
                columnas_clientes
            ].style.format(
                {
                    "Precio_Unitario":
                        "${:,.2f}",

                    "Importe_Total":
                        "${:,.2f}",
                }
            ),
            use_container_width=True
        )

        # ----------------------------------------------------
        # INFORMACIÓN SOBRE PRECIOS
        # ----------------------------------------------------

        valores_caja = []

        valores_botella = []

        for _, fila in coincidencias.iterrows():

            importe = float(
                fila["Importe_Total"]
            )

            cajas = float(
                fila["Cajas"]
            )

            botellas = float(
                fila["Botellas"]
            )

            if cajas > 0:

                valores_caja.append(
                    round(
                        importe / cajas,
                        2
                    )
                )

            if botellas > 0:

                valores_botella.append(
                    round(
                        importe / botellas,
                        2
                    )
                )

        valores_caja = sorted(
            set(valores_caja)
        )

        valores_botella = sorted(
            set(valores_botella)
        )

        if len(valores_caja) > 1:

            st.warning(
                "⚠️ Este producto aparece con "
                "diferentes valores por caja. "
                "Para una devolución exacta debes "
                "seleccionar el cliente correspondiente."
            )

        elif len(valores_caja) == 1:

            st.info(
                f"Valor por caja encontrado: "
                f"${valores_caja[0]:,.2f}"
            )

        if len(valores_botella) > 1:

            st.warning(
                "⚠️ Este producto aparece con "
                "diferentes valores por botella."
            )

        elif len(valores_botella) == 1:

            st.info(
                f"Valor por botella encontrado: "
                f"${valores_botella[0]:,.2f}"
            )

    # --------------------------------------------------------
    # NO ENCONTRADO
    # --------------------------------------------------------

    else:

        st.error(
            f"❌ El código {codigo_busqueda} "
            f"no aparece en la ruta {ruta_elegida}."
        )

        st.info(
            "El sistema compara ignorando ceros iniciales. "
            "Por ejemplo, 056706 y 56706 se consideran "
            "el mismo código."
        )


# ============================================================
# REGISTRO DE DEVOLUCIONES
# ============================================================

st.markdown("---")

st.subheader(
    f"🔄 Registro de devoluciones — {ruta_elegida}"
)

st.write(
    """
    Registra el código y las cantidades devueltas.

    Si el mismo código tiene diferentes precios según
    el cliente, podrás seleccionar el cliente para evitar
    que el sistema invente un valor.
    """
)


# ============================================================
# EDITOR
# ============================================================

df_base_devoluciones = pd.DataFrame(
    [
        {
            "Código": "",
            "Cliente": "",
            "Cajas": 0.0,
            "Botellas": 0.0,
        }
    ]
)


devoluciones = st.data_editor(
    df_base_devoluciones,
    num_rows="dynamic",
    use_container_width=True,
    key="tabla_devoluciones",
    column_config={

        "Código":
            st.column_config.TextColumn(
                "Código",
                help="Ejemplo: 160318 o 056706"
            ),

        "Cliente":
            st.column_config.TextColumn(
                "Cliente",
                help=(
                    "Opcional si el código tiene "
                    "un único valor. Si tiene varios "
                    "precios, selecciona/escribe el cliente."
                )
            ),

        "Cajas":
            st.column_config.NumberColumn(
                "Cajas devueltas",
                min_value=0.0,
                step=1.0
            ),

        "Botellas":
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


for _, fila_dev in devoluciones.iterrows():

    codigo_ingresado = (
        str(
            fila_dev.get(
                "Código",
                ""
            )
        ).strip()
    )

    if (
        not codigo_ingresado
        or codigo_ingresado.lower() == "none"
    ):
        continue

    clave = codigo_clave(
        codigo_ingresado
    )

    cajas_dev = convertir_cantidad(
        fila_dev.get(
            "Cajas",
            0
        )
    )

    botellas_dev = convertir_cantidad(
        fila_dev.get(
            "Botellas",
            0
        )
    )

    cliente_elegido = (
        str(
            fila_dev.get(
                "Cliente",
                ""
            )
        ).strip()
    )

    if (
        cajas_dev == 0
        and botellas_dev == 0
    ):
        continue

    # --------------------------------------------------------
    # BUSCAR CÓDIGO
    # --------------------------------------------------------

    coincidencias_codigo = df_ruta_actual[
        df_ruta_actual["Codigo_Key"]
        == clave
    ].copy()

    # --------------------------------------------------------
    # NO EXISTE
    # --------------------------------------------------------

    if coincidencias_codigo.empty:

        resumen_devoluciones.append(
            {
                "Código":
                    codigo_ingresado,

                "Producto":
                    "NO ENCONTRADO",

                "Cliente":
                    cliente_elegido,

                "Cajas":
                    cajas_dev,

                "Botellas":
                    botellas_dev,

                "Valor":
                    0.0,

                "Estado":
                    "❌ Código no encontrado",
            }
        )

        continue

    # --------------------------------------------------------
    # FILTRAR CLIENTE SI SE INDICÓ
    # --------------------------------------------------------

    coincidencias = (
        coincidencias_codigo.copy()
    )

    if cliente_elegido:

        coincidencias_cliente = (
            coincidencias[
                coincidencias[
                    "Cliente"
                ].astype(str).str.contains(
                    cliente_elegido,
                    case=False,
                    na=False
                )
            ]
        )

        if not coincidencias_cliente.empty:

            coincidencias = (
                coincidencias_cliente
            )

    # --------------------------------------------------------
    # SI HAY VARIOS VALORES POSIBLES
    # --------------------------------------------------------

    valores_posibles = []

    for _, fila_producto in coincidencias.iterrows():

        importe = float(
            fila_producto["Importe_Total"]
        )

        cajas = float(
            fila_producto["Cajas"]
        )

        botellas = float(
            fila_producto["Botellas"]
        )

        if (
            cajas > 0
            and cajas_dev > 0
        ):

            valores_posibles.append(
                round(
                    importe / cajas,
                    2
                )
            )

        if (
            botellas > 0
            and botellas_dev > 0
        ):

            valores_posibles.append(
                round(
                    importe / botellas,
                    2
                )
            )

    valores_unicos = sorted(
        set(valores_posibles)
    )

    # --------------------------------------------------------
    # SI HAY VARIOS VALORES Y NO SE ESCOGIÓ CLIENTE
    # --------------------------------------------------------

    if (
        len(valores_unicos) > 1
        and not cliente_elegido
    ):

        resumen_devoluciones.append(
            {
                "Código":
                    codigo_ingresado,

                "Producto":
                    coincidencias.iloc[0][
                        "Producto"
                    ],

                "Cliente":
                    "SELECCIONAR CLIENTE",

                "Cajas":
                    cajas_dev,

                "Botellas":
                    botellas_dev,

                "Valor":
                    0.0,

                "Estado":
                    "⚠️ Hay diferentes valores. "
                    "Selecciona el cliente.",
            }
        )

        continue

    # --------------------------------------------------------
    # SI SE ESCRIBIÓ CLIENTE PERO NO COINCIDIÓ
    # --------------------------------------------------------

    if (
        cliente_elegido
        and coincidencias.equals(
            coincidencias_codigo
        )
        and len(coincidencias_codigo) > 1
    ):

        resumen_devoluciones.append(
            {
                "Código":
                    codigo_ingresado,

                "Producto":
                    coincidencias_codigo.iloc[0][
                        "Producto"
                    ],

                "Cliente":
                    "CLIENTE NO IDENTIFICADO",

                "Cajas":
                    cajas_dev,

                "Botellas":
                    botellas_dev,

                "Valor":
                    0.0,

                "Estado":
                    "⚠️ No coincidió el cliente.",
            }
        )

        continue

    # --------------------------------------------------------
    # TOMAR REGISTRO
    # --------------------------------------------------------

    producto_original = (
        coincidencias.iloc[0]
    )

    nombre_producto = (
        producto_original[
            "Producto"
        ]
    )

    cliente_original = (
        producto_original[
            "Cliente"
        ]
    )

    # --------------------------------------------------------
    # CALCULAR
    # --------------------------------------------------------

    valor_devolucion = (
        calcular_valor_devolucion(
            producto_original,
            cajas_dev,
            botellas_dev
        )
    )

    # --------------------------------------------------------
    # SI NO PUDO CALCULAR
    # --------------------------------------------------------

    if valor_devolucion <= 0:

        estado = (
            "⚠️ No fue posible calcular "
            "el valor con esta presentación."
        )

    else:

        estado = "✅ Calculado"

        total_valor_devuelto += (
            valor_devolucion
        )

    # --------------------------------------------------------
    # GUARDAR
    # --------------------------------------------------------

    resumen_devoluciones.append(
        {
            "Código":
                codigo_ingresado,

            "Producto":
                nombre_producto,

            "Cliente":
                cliente_original,

            "Cajas":
                cajas_dev,

            "Botellas":
                botellas_dev,

            "Valor":
                valor_devolucion,

            "Estado":
                estado,
        }
    )


# ============================================================
# RESUMEN
# ============================================================

st.markdown("---")

st.subheader(
    "📋 Resumen de devoluciones"
)


if resumen_devoluciones:

    df_resumen = pd.DataFrame(
        resumen_devoluciones
    )

    st.dataframe(
        df_resumen.style.format(
            {
                "Valor":
                    "${:,.2f}"
            }
        ),
        use_container_width=True
    )

else:

    st.info(
        "Todavía no hay devoluciones registradas."
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


l1, l2, l3 = st.columns(3)


with l1:

    st.metric(
        "Venta de Contado",
        f"${total_libro_ruta:,.2f}"
    )


with l2:

    st.metric(
        "Devoluciones",
        f"- ${total_valor_devuelto:,.2f}"
    )


with l3:

    st.metric(
        "NETO A LIQUIDAR",
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

        codigo = str(
            devolucion["Código"]
        )

        clave = codigo_clave(
            codigo
        )

        coincidencias = df_ruta_actual[
            df_ruta_actual["Codigo_Key"]
            == clave
        ]

        if coincidencias.empty:

            continue

        with st.expander(
            f"📦 {devolucion['Producto']} "
            f"— Código {codigo}"
        ):

            st.write(
                "Estos son los clientes de la ruta "
                "que tienen este producto:"
            )

            columnas = [
                "NumeroCliente",
                "Cliente",
                "Código",
                "Producto",
                "Cajas",
                "Botellas",
                "Precio_Unitario",
                "Importe_Total",
            ]

            st.dataframe(
                coincidencias[
                    columnas
                ].style.format(
                    {
                        "Precio_Unitario":
                            "${:,.2f}",

                        "Importe_Total":
                            "${:,.2f}",
                    }
                ),
                use_container_width=True
            )


# ============================================================
# GENERAR COMPROBANTE
# ============================================================

st.markdown("---")

st.subheader(
    "🖨️ Comprobante profesional"
)


st.write(
    "Genera una vista limpia para imprimir "
    "o guardar como PDF desde el navegador."
)


if st.button(
    "🧾 Generar comprobante",
    use_container_width=True
):

    fecha_actual = datetime.now().strftime(
        "%d/%m/%Y %H:%M"
    )

    filas_reporte = ""

    for _, fila in pd.DataFrame(
        resumen_devoluciones
    ).iterrows():

        filas_reporte += f"""
        <tr>
            <td class="centro">
                {fila.get("Código", "")}
            </td>

            <td>
                {fila.get("Producto", "")}
            </td>

            <td>
                {fila.get("Cliente", "")}
            </td>

            <td class="centro">
                {fila.get("Cajas", 0):,.0f}
            </td>

            <td class="centro">
                {fila.get("Botellas", 0):,.0f}
            </td>

            <td class="derecha">
                ${fila.get("Valor", 0):,.2f}
            </td>

            <td>
                {fila.get("Estado", "")}
            </td>
        </tr>
        """

    html_reporte = f"""
    <div class="reporte-impresion">

        <div style="text-align:center;">

            <h1>
                DISTRIBUCIONES INESCO S.A.S.
            </h1>

            <h2>
                COMPROBANTE DE DEVOLUCIÓN
            </h2>

            <p>
                Liquidación de Ruta
            </p>

        </div>

        <hr>

        <table>

            <tr>
                <td>
                    <b>Ruta</b>
                </td>

                <td>
                    {ruta_elegida}
                </td>

                <td>
                    <b>Fecha</b>
                </td>

                <td>
                    {fecha_actual}
                </td>
            </tr>

            <tr>
                <td>
                    <b>Venta de Contado</b>
                </td>

                <td>
                    ${total_libro_ruta:,.2f}
                </td>

                <td>
                    <b>Crédito</b>
                </td>

                <td>
                    ${total_credito_ruta:,.2f}
                </td>
            </tr>

        </table>

        <h3>
            Detalle de productos devueltos
        </h3>

        <table>

            <thead>

                <tr>

                    <th>
                        Código
                    </th>

                    <th>
                        Producto
                    </th>

                    <th>
                        Cliente
                    </th>

                    <th>
                        Cajas
                    </th>

                    <th>
                        Botellas
                    </th>

                    <th>
                        Valor
                    </th>

                    <th>
                        Estado
                    </th>

                </tr>

            </thead>

            <tbody>

                {filas_reporte}

            </tbody>

        </table>

        <br>

        <table>

            <tr>

                <td>
                    <b>Total Venta de Contado</b>
                </td>

                <td class="derecha">
                    ${total_libro_ruta:,.2f}
                </td>

            </tr>

            <tr>

                <td>
                    <b>Total Devoluciones</b>
                </td>

                <td class="derecha">
                    - ${total_valor_devuelto:,.2f}
                </td>

            </tr>

            <tr>

                <td>
                    <b>NETO A LIQUIDAR</b>
                </td>

                <td class="total-final">
                    ${neto_a_liquidar:,.2f}
                </td>

            </tr>

        </table>

        <br><br>

        <table>

            <tr>

                <td
                    style="
                    height:100px;
                    text-align:center;
                    vertical-align:bottom;
                    "
                >

                    ___________________________<br>

                    Firma del conductor

                </td>

                <td
                    style="
                    height:100px;
                    text-align:center;
                    vertical-align:bottom;
                    "
                >

                    ___________________________<br>

                    Firma del liquidador

                </td>

            </tr>

        </table>

        <br>

        <p style="font-size:11px;color:#666;">

            Documento generado automáticamente a partir
            del libro de rutas.

        </p>

    </div>
    """

    st.markdown(
        html_reporte,
        unsafe_allow_html=True
    )

    st.info(
        "💡 Para imprimir: presiona Ctrl + P "
        "y selecciona 'Guardar como PDF' o tu impresora."
    )


# ============================================================
# FINAL
# ============================================================

st.markdown("---")

st.caption(
    "InescoRoute • Lectura dinámica del libro de rutas "
    "• Liquidación y control de devoluciones"
)
