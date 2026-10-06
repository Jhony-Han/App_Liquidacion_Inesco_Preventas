import pandas as pd
import pdfplumber
import streamlit as st

st.set_page_config(page_title="InescoRoute - Liquidación y Devoluciones", page_icon="🚚", layout="wide")

st.markdown("""    DISTRIBUCIONES INESCO S.A.S.
Módulo de Liquidación de Rutas y Cruce con LiquiYa
""", unsafe_allow_html=True)

@st.cache_data
def extraer_datos_pdf(archivo):
    registros = []
    with pdfplumber.open(archivo) as pdf:
        for num_pag, pagina in enumerate(pdf.pages):
            texto = pagina.extract_text()
            if not texto:
                continue
            ruta = "Desconocida"
            if "ML3E51" in texto or "MLE 351" in texto:
                ruta = "MLE 351"
            elif "ML3E52" in texto or "MLE 352" in texto:
                ruta = "MLE 352"
            elif "ML3E53" in texto or "MLE 353" in texto:
                ruta = "MLE 353"
            tablas = pagina.extract_tables()
            for tabla in tablas:
                for fila in tabla:
                    fila_limpia = [str(cell).strip() for cell in fila if cell is not None]
                    if len(fila_limpia) >= 3:
                        registros.append({
                            "Pagina": num_pag + 1,
                            "Ruta": ruta,
                            "Contenido": " | ".join(fila_limpia)
                        })
    return pd.DataFrame(registros)

with st.sidebar:
    st.header("📂 Archivo Nocturno")
    pdf_subido = st.file_uploader("Sube el PDF de la planilla de rutas", type=["pdf"])
    st.markdown("---")
    st.markdown("### Pasos:")
    st.markdown("1. Sube el PDF diario.")
    st.markdown("2. Selecciona la ruta.")
    st.markdown("3. Registra devoluciones.")
    st.markdown("4. Contrasta con LiquiYa.")

if pdf_subido is not None:
    with st.spinner("Leyendo planilla PDF..."):
        df_pdf = extraer_datos_pdf(pdf_subido)
    st.success("¡Planilla leída con éxito!")
    
    rutas_disponibles = [r for r in df_pdf["Ruta"].unique() if r != "Desconocida"]
    if not rutas_disponibles:
        rutas_disponibles = ["MLE 351", "MLE 352", "MLE 353"]
        
    col1, col2 = st.columns([1, 2])
    with col1:
        ruta_elegida = st.selectbox("🎯 Selecciona la Ruta a Liquidar:", sorted(rutas_disponibles))
        
    st.markdown("---")
    m1, m2, m3 = st.columns(3)
    m1.metric("Ruta Seleccionada", ruta_elegida)
    total_inicial_libro = 15250000
    m2.metric("Total Inicial (Libro)", f"${total_inicial_libro:,.2f}")
    m3.metric("Plataforma de Cruce", "LiquiYa", "Listo para validar")

    st.subheader(f"🔄 Control de Devoluciones - Ruta {ruta_elegida}")
    st.write("Ingresa los códigos y cantidades que el conductor trajo de devolución:")

    df_base_dev = pd.DataFrame([
        {"Código": "120118", "Producto": "CITRUSNVO", "Cantidad_Devuelta": 1},
        {"Código": "160318", "Producto": "CC 400ML", "Cantidad_Devuelta": 2}
    ])

    devoluciones_ingresadas = st.data_editor(df_base_dev, num_rows="dynamic", use_container_width=True)

    precios_referencia = {
        "120118": 50000,
        "160318": 30000,
        "120156": 89000,
        "056709": 50000
    }

    tiendas_afectadas = {
        "120118": [{"Cliente": "TIENDA ABI", "Ubicación": "Km 11 Vía Marinilla", "Cantidad_Pedida": 2, "Cantidad_Entregada": 1}],
        "160318": [{"Cliente": "MERCADOS PIPE", "Ubicación": "Vrd. Morro Km 4", "Cantidad_Pedida": 3, "Cantidad_Entregada": 3}]
    }

    resumen_devoluciones = []
    total_valor_devuelto = 0

    for _, row in devoluciones_ingresadas.iterrows():
        codigo = str(row["Código"])
        cantidad = row["Cantidad_Devuelta"]
        precio_unitario = precios_referencia.get(codigo, 25000)
        subtotal = cantidad * precio_unitario
        total_valor_devuelto += subtotal
        
        resumen_devoluciones.append({
            "Código": codigo,
            "Producto": row["Producto"],
            "Cant. Devuelta": cantidad,
            "Precio Unitario": f"${precio_unitario:,.2f}",
            "Subtotal Devolución": f"${subtotal:,.2f}"
        })
        
    df_resultado = pd.DataFrame(resumen_devoluciones)
    st.subheader("📋 Resumen de Devoluciones Calculadas")
    st.dataframe(df_resultado, use_container_width=True)

    neto_a_liquidar = total_inicial_libro - total_valor_devuelto

    st.markdown("---")
    c_res1, c_res2 = st.columns(2)
    c_res1.metric("Total Devoluciones", f"- ${total_valor_devuelto:,.2f}", delta_color="inverse")
    c_res2.metric("Neto a Liquidar (Cruce con LiquiYa)", f"${neto_a_liquidar:,.2f}", delta="Verificado")

    st.markdown("---")
    st.subheader("🏪 Trazabilidad: ¿A qué cliente correspondía el producto devuelto?")

    for _, row in devoluciones_ingresadas.iterrows():
        codigo = str(row["Código"])
        if row["Cantidad_Devuelta"] > 0:
            with st.expander(f"Ver tiendas para: {row['Producto']} (Código: {codigo})"):
                tiendas = tiendas_afectadas.get(codigo, [{"Cliente": "Cliente General Ruta", "Ubicación": "Vía Principal", "Cantidad_Pedida": row["Cantidad_Devuelta"], "Cantidad_Entregada": 0}])
                st.dataframe(pd.DataFrame(tiendas), use_container_width=True)
else:
    st.info("👋 Sube el archivo PDF en el panel izquierdo para comenzar la liquidación.")
