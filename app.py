import re
import pandas as pd
import pdfplumber
import streamlit as st

st.set_page_config(
    page_title="InescoRoute - Liquidación y Devoluciones",
    page_icon="🚚",
    layout="wide",
)

st.markdown(
    """    DISTRIBUCIONES INESCO S.A.S.
Módulo de Liquidación de Rutas y Cruce con LiquiYa
""",
    unsafe_allow_html=True,
)


@st.cache_data
def extraer_datos_completos(archivo):
  registros_entregas = []
  # Inicializamos en 0.0 para que NUNCA use valores quemados de libros anteriores
  totales_por_ruta = {
      "MLE 351": 0.0,
      "MLE 352": 0.0,
      "MLE 353": 0.0,
  }

  with pdfplumber.open(archivo) as pdf:
    ruta_actual = "MLE 351"

    for num_pag, pagina in enumerate(pdf.pages):
      texto = pagina.extract_text()
      if not texto:
        continue

      # Detectar la ruta activa según el texto de la página
      if "MLE 351" in texto or "ML3E51" in texto:
        ruta_actual = "MLE 351"
      elif "MLE 352" in texto or "ML3E52" in texto:
        ruta_actual = "MLE 352"
      elif "MLE 353" in texto or "ML3E53" in texto:
        ruta_actual = "MLE 353"

      # Búsqueda avanzada y flexible del total de contado en el texto de la página
      lineas = texto.split("\n")
      for i, linea in enumerate(lineas):
        if "Venta de Contado" in linea or "Contado CO" in linea:
          # Revisar la línea actual y la siguiente por si el valor está abajo
            bloque_texto = linea + " " + (lineas[i + 1] if i + 1 < len(lineas) else "")
            # Buscar patrones de números grandes con puntos y comas (ej: 24.273.437 o 7.110.218)
            candidatos = re.findall(r'\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+', bloque_texto)
            for c in candidatos:
              c_limpio = c.replace(".", "").replace(",", ".")
              try:
                val = float(c_limpio)
                # Un total de contado de ruta siempre es superior a 100,000 pesos
                if val > 100000:
                  totales_por_ruta[ruta_actual] = val
              except ValueError:
                pass

      # Extraer filas de las tablas del PDF para la trazabilidad por cliente y producto
      tablas = pagina.extract_tables()
      for tabla in tablas:
        for fila in tabla:
          fila_limpia = [
              str(cell).strip() for cell in fila if cell is not None
          ]
          if len(fila_limpia) >= 3:
            registros_entregas.append({
                "Ruta": ruta_actual,
                "Cliente": fila_limpia[2]
                if len(fila_limpia) > 2
                else "CLIENTE GENERAL",
                "Código": fila_limpia[1]
                if len(fila_limpia) > 1
                else "000000",
                "Producto": fila_limpia[3]
                if len(fila_limpia) > 3
                else "PRODUCTO",
                "Cantidad": 1.0,
            })

  df_entregas = pd.DataFrame(registros_entregas)
  
  # Si alguna ruta no encontró su valor en el texto del PDF, asignamos temporalmente 0 para que se note
  for r in ["MLE 351", "MLE 352", "MLE 353"]:
    if totales_por_ruta[r] == 0.0:
      totales_por_ruta[r] = 0.0

  if df_entregas.empty:
    df_entregas = pd.DataFrame([{
        "Ruta": "MLE 351",
        "Cliente": "CLIENTE GENERAL",
        "Código": "120118",
        "Producto": "PRODUCTO GENERAL",
        "Cantidad": 1.0,
    }])

  return df_entregas, totales_por_ruta


with st.sidebar:
  st.header("📂 Archivo Nocturno")
  pdf_subido = st.file_uploader(
      "Sube el PDF de la planilla de rutas", type=["pdf"]
  )
  st.markdown("---")
  st.markdown("### Pasos:")
  st.markdown("1. Sube el PDF diario.")
  st.markdown("2. Selecciona la ruta a liquidar.")
  st.markdown("3. Registra código y cantidad devuelta.")
  st.markdown("4. Revisa clientes afectados y cruza con LiquiYa.")

if pdf_subido is not None:
  with st.spinner("Procesando libro y extrayendo rutas y clientes..."):
    df_entregas, dict_totales = extraer_datos_completos(pdf_subido)
  st.success("¡Planilla y datos leídos con éxito!")

  rutas_disponibles = ["MLE 351", "MLE 352", "MLE 353"]

  col_s1, col_s2 = st.columns([1, 2])
  with col_s1:
    ruta_elegida = st.selectbox(
        "🎯 Selecciona la Ruta a Liquidar:", rutas_disponibles
    )

  st.markdown("---")

  # Obtenemos el total de contado leído directamente del PDF actual
  total_libro_ruta = dict_totales.get(ruta_elegida, 0.0)

  # Si el PDF no logró extraerlo automáticamente, permitimos ingresarlo o ajustarlo de forma manual para evitar bloqueos
  if total_libro_ruta == 0.0:
    st.warning(f"⚠️ No se detectó automáticamente el total de contado para la ruta {ruta_elegida} en este PDF. Puedes ajustarlo abajo:")
    total_libro_ruta = st.number_input("Ingresa el Total de Contado del libro para esta ruta:", min_value=0.0, value=15000000.0, step=1000.0)

  m1, m2, m3 = st.columns(3)
  m1.metric("Ruta Activa", ruta_elegida)
  m2.metric(
      f"Total Contado ({ruta_elegida})", f"${total_libro_ruta:,.2f}"
  )
  m3.metric("Plataforma de Cruce", "LiquiYa", "Pendiente validación")

  st.subheader(
      f"🔄 Registro de Devoluciones del Camión - Ruta {ruta_elegida}"
  )
  st.write(
      "Ingresa el código del producto devuelto, su nombre y la cantidad física"
      " que trajo el conductor:"
  )

  df_base_dev = pd.DataFrame([
      {"Código": "120118", "Producto": "CITRUSNVO", "Cantidad_Devuelta": 0.0},
      {"Código": "160318", "Producto": "CC 400ML", "Cantidad_Devuelta": 0.0},
  ])

  devoluciones_ingresadas = st.data_editor(
      df_base_dev, num_rows="dynamic", use_container_width=True
  )

  precios_catalogo = {
      "120118": 50000.0,
      "160318": 30000.0,
      "1224839356": 81880.0,
      "1224726708": 712300.0,
  }

  resumen_devoluciones = []
  total_valor_devuelto = 0.0

  for _, row in devoluciones_ingresadas.iterrows():
    codigo = str(row["Código"])
    cantidad = float(row["Cantidad_Devuelta"])
    precio_unitario = precios_catalogo.get(codigo, 35000.0)
    subtotal_dev = cantidad * precio_unitario
    total_valor_devuelto += subtotal_dev

    resumen_devoluciones.append({
        "Código": codigo,
        "Producto": row["Producto"],
        "Cant. Devuelta": cantidad,
        "Precio Unitario": f"${precio_unitario:,.2f}",
        "Subtotal Devolución": f"${subtotal_dev:,.2f}",
    })

  df_resumen = pd.DataFrame(resumen_devoluciones)
  st.subheader("📋 Resumen Financiero de Devoluciones")
  st.dataframe(df_resumen, use_container_width=True)

  neto_a_liquidar = total_libro_ruta - total_valor_devuelto

  st.markdown("---")
  c_res1, c_res2 = st.columns(2)
  c_res1.metric(
      "Total Devoluciones a Descontar",
      f"- ${total_valor_devuelto:,.2f}",
      delta_color="inverse",
  )
  c_res2.metric(
      "Neto a Liquidar (Cruce con LiquiYa)",
      f"${neto_a_liquidar:,.2f}",
      delta="Cruce esperado",
  )

  st.markdown("---")
  st.subheader(
      "🏪 Trazabilidad por Cliente: ¿A qué tiendas se les programó o"
      " entregó este producto?"
  )
  st.write(
      "Aquí puedes verificar qué clientes de la ruta tenían asignados los"
      " productos devueltos:"
  )

  df_ruta_actual = df_entregas[df_entregas["Ruta"] == ruta_elegida]

  for _, row in devoluciones_ingresadas.iterrows():
    codigo_dev = str(row["Código"])
    if row["Cantidad_Devuelta"] > 0:
      coincidencias = df_ruta_actual[
          df_ruta_actual["Código"].astype(str).str.contains(codigo_dev)
          | df_ruta_actual["Producto"]
          .astype(str)
          .str.upper()
          .str.contains(str(row["Producto"]).upper())
      ]

      with st.expander(
          f"📦 Producto Devuelto: {row['Producto']} (Código: {codigo_dev}) — Ver"
          f" Clientes Afectados"
      ):
        if not coincidencias.empty:
          st.dataframe(
              coincidencias[["Cliente", "Código", "Producto", "Cantidad"]],
              use_container_width=True,
          )
        else:
          st.warning(
              "No se encontró una coincidencia exacta de este código en las"
              " entregas tabuladas de esta ruta específica."
          )

else:
  st.info(
      "👋 Sube el archivo PDF de la planilla en el panel izquierdo para"
      " comenzar con la liquidación por ruta."
  )
