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
  totales_por_ruta = {
      "ML3E51": 0.0,
      "ML3E52": 0.0,
      "ML3E53": 0.0,
  }

  with pdfplumber.open(archivo) as pdf:
    ruta_actual = "ML3E53"
    cliente_actual = "CLIENTE GENERAL"

    for num_pag, pagina in enumerate(pdf.pages):
      texto = pagina.extract_text()
      if not texto:
        continue

      # Detectar la ruta activa en la página
      if "ML3E51" in texto or "MLE 351" in texto:
        ruta_actual = "ML3E51"
      elif "ML3E52" in texto or "MLE 352" in texto:
        ruta_actual = "ML3E52"
      elif "ML3E53" in texto or "MLE 353" in texto:
        ruta_actual = "ML3E53"

      lineas = texto.split("\n")

      # Extracción precisa de totales al final de los reportes/rutas
      for i, linea in enumerate(lineas):
        if "Total a Cobrar" in linea or "Total a Cobrar Cont/Prom" in linea:
          partes = linea.split()
          for p in partes:
            p_limpio = p.replace(".", "").replace(",", ".")
            try:
              val = float(p_limpio)
              if val > 1000:
                totales_por_ruta[ruta_actual] = val
            except ValueError:
              pass

      # Barrido de líneas para capturar clientes
      for i, linea in enumerate(lineas):
        if "Fecha de Entrega:" in linea or "Ruta:" in linea:
          if i >= 2:
            posible_cliente = lineas[i - 2].strip()
            if len(posible_cliente) > 3 and not posible_cliente.isdigit() and "ML3" not in posible_cliente:
              cliente_actual = posible_cliente

      # Extracción estricta de tablas de productos
      tablas = pagina.extract_tables()
      for tabla in tablas:
        for fila in tabla:
          fila_limpia = [
              str(cell).strip() for cell in fila if cell is not None and str(cell).strip() != ""
          ]
          if len(fila_limpia) >= 3:
            codigo_encontrado = ""
            producto_encontrado = ""
            cajas_fila = 0.0
            botellas_fila = 0.0
            importe_total = 0.0

            for cell in fila_limpia:
              if cell.isdigit() and 4 <= len(cell) <= 10:
                codigo_encontrado = cell
              elif not cell.isdigit() and len(cell) > 2 and "Venta" not in cell and "Ruta" not in cell and "SUB" not in cell:
                if producto_encontrado == "":
                  producto_encontrado = cell

              if "/" in cell:
                partes_cb = cell.split("/")
                try:
                  if len(partes_cb) == 2:
                    cajas_fila = float(partes_cb[0].strip())
                    botellas_fila = float(partes_cb[1].strip())
                except ValueError:
                  pass

              cell_num_clean = cell.replace(".", "").replace(",", ".").replace("$", "")
              try:
                num = float(cell_num_clean)
                if 1000 <= num <= 5000000:
                  importe_total = num
              except ValueError:
                pass

            if codigo_encontrado:
              total_unidades_base = (cajas_fila * 15.0) + botellas_fila if (cajas_fila > 0 or botellas_fila > 0) else 15.0
              if total_unidades_base == 0:
                total_unidades_base = 15.0

              precio_unitario_real = (importe_total / total_unidades_base) if importe_total > 0 else 1200.0

              registros_entregas.append({
                  "Ruta": ruta_actual,
                  "Cliente": cliente_actual,
                  "Código": codigo_encontrado,
                  "Producto": producto_encontrado if producto_encontrado else f"PRODUCTO REF {codigo_encontrado}",
                  "Cajas_Fila": cajas_fila,
                  "Botellas_Fila": botellas_fila,
                  "Importe_Total": importe_total,
                  "Precio_Unitario": precio_unitario_real
              })

  if len(registros_entregas) > 0:
    df_entregas = pd.DataFrame(registros_entregas)
  else:
    df_entregas = pd.DataFrame(columns=["Ruta", "Cliente", "Código", "Producto", "Cajas_Fila", "Botellas_Fila", "Importe_Total", "Precio_Unitario"])

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
  st.markdown("3. Registra código, cajas y botellas devueltas.")
  st.markdown("4. Imprime y cruza con LiquiYa.")

if pdf_subido is not None:
  with st.spinner("Leyendo estructura y extrayendo datos del PDF..."):
    df_entregas, dict_totales = extraer_datos_completos(pdf_subido)
  st.success("¡Archivo analizado con éxito!")

  rutas_disponibles = ["ML3E51", "ML3E52", "ML3E53"]

  col_s1, col_s2 = st.columns([1, 2])
  with col_s1:
    ruta_elegida = st.selectbox(
        "🎯 Selecciona la Ruta a Liquidar:", rutas_disponibles
    )

  st.markdown("---")

  total_libro_ruta = dict_totales.get(ruta_elegida, 0.0)

  if total_libro_ruta == 0.0:
    st.warning(
        f"⚠️ No se detectó automáticamente el total para la ruta {ruta_elegida}."
        " Asignalo aquí:"
    )
    total_libro_ruta = st.number_input(
        "Total de la Ruta:",
        min_value=0.0,
        value=172064.0 if ruta_elegida == "ML3E53" else 15000000.0,
        step=1000.0,
    )

  m1, m2, m3 = st.columns(3)
  m1.metric("Ruta Activa", ruta_elegida)
  m2.metric(f"Total Ruta ({ruta_elegida})", f"${total_libro_ruta:,.2f}")
  m3.metric("Plataforma de Cruce", "LiquiYa", "Listo para validar")

  if not df_entregas.empty and "Ruta" in df_entregas.columns:
    df_ruta_actual = df_entregas[df_entregas["Ruta"] == ruta_elegida]
  else:
    df_ruta_actual = pd.DataFrame(columns=["Ruta", "Cliente", "Código", "Producto", "Cajas_Fila", "Botellas_Fila", "Importe_Total", "Precio_Unitario"])

  st.subheader(
      f"🔄 Registro de Devoluciones del Camión - Ruta {ruta_elegida}"
  )
  st.write(
      "Ingresa el código del producto y separa las cantidades en **Cajas Devueltas** y **Botellas Devueltas**:"
  )

  df_base_dev = pd.DataFrame([
      {"Código": "056624", "Cajas_Devueltas": 0.0, "Botellas_Devueltas": 2.0},
  ])

  devoluciones_ingresadas = st.data_editor(
      df_base_dev, num_rows="dynamic", use_container_width=True
  )

  resumen_devoluciones = []
  total_valor_devuelto = 0.0

  for _, row in devoluciones_ingresadas.iterrows():
    codigo_raw = row["Código"]
    if codigo_raw is None or str(codigo_raw).strip() == "" or str(codigo_raw).lower() == "none":
      continue
    
    codigo = str(codigo_raw).strip()
    
    # Manejo seguro de valores nulos o vacíos en el editor
    val_cajas = row["Cajas_Devueltas"]
    cajas_dev = float(val_cajas) if (val_cajas is not None and str(val_cajas).lower() != "none") else 0.0

    val_botellas = row["Botellas_Devueltas"]
    botellas_dev = float(val_botellas) if (val_botellas is not None and str(val_botellas).lower() != "none") else 0.0

    if cajas_dev == 0.0 and botellas_dev == 0.0:
      continue

    coincidencias = df_ruta_actual[df_ruta_actual["Código"].astype(str) == codigo]

    nombre_prod = f"PRODUCTO REF {codigo}"
    precio_unitario = 1200.0

    if not coincidencias.empty:
      nombre_prod = coincidencias.iloc[0]["Producto"]
      p_calc = coincidencias.iloc[0]["Precio_Unitario"]
      if p_calc > 0:
        precio_unitario = p_calc
    elif codigo == "056624":
      precio_unitario = 1200.0

    total_unidades_dev = (cajas_dev * 15.0) + botellas_dev
    subtotal_dev = total_unidades_dev * precio_unitario
    total_valor_devuelto += subtotal_dev

    resumen_devoluciones.append({
        "Código": codigo,
        "Producto": nombre_prod,
        "Cajas Dev.": cajas_dev,
        "Botellas Dev.": botellas_dev,
        "Precio Unitario": precio_unitario,
        "Subtotal Devolución": subtotal_dev,
    })

  if len(resumen_devoluciones) > 0:
    df_resumen = pd.DataFrame(resumen_devoluciones)
    st.subheader("📋 Resumen Financiero de Devoluciones")
    st.dataframe(
        df_resumen.style.format({
            "Precio Unitario": "${:,.2f}",
            "Subtotal Devolución": "${:,.2f}",
        }),
        use_container_width=True,
    )
  else:
    df_resumen = pd.DataFrame(columns=["Código", "Producto", "Cajas Dev.", "Botellas Dev.", "Precio Unitario", "Subtotal Devolución"])
    st.info("Ingresa al menos un código y cantidad válida arriba para calcular el resumen.")

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
  st.subheader("🏪 Trazabilidad por Cliente y Tienda")

  if len(resumen_devoluciones) > 0:
    for row in resumen_devoluciones:
      codigo_dev = str(row["Código"])
      tiendas_afectadas = df_ruta_actual[df_ruta_actual["Código"].astype(str) == codigo_dev]

      with st.expander(
          f"📦 Producto: {row['Producto']} (Código: {codigo_dev}) — Clientes que recibieron este producto"
      ):
        if not tiendas_afectadas.empty:
          st.dataframe(
              tiendas_afectadas[["Cliente", "Código", "Producto", "Cajas_Fila", "Botellas_Fila", "Importe_Total", "Precio_Unitario"]].style.format({
                  "Importe_Total": "${:,.2f}",
                  "Precio_Unitario": "${:,.2f}"
              }),
              use_container_width=True,
          )
        else:
          st.info("No se encontró una coincidencia tabular exacta para este código en esta ruta del PDF. (Se aplicó cálculo estándar).")
  else:
    st.info("Agrega devoluciones para ver la trazabilidad de los clientes afectados.")

  with st.expander("🔍 Ver datos brutos extraídos del PDF"):
    if not df_ruta_actual.empty:
      st.dataframe(df_ruta_actual, use_container_width=True)
    else:
      st.info("No se detectaron filas tabulares automáticas.")

  st.markdown("---")
  st.subheader("🖨️ Reporte Listo para Imprimir o Guardar")
  if st.button("📄 Generar Vista de Impresión"):
    st.markdown(
        f"""
        <div style="background-color: white; color: black; padding: 20px; border-radius: 10px; border: 2px solid #ccc;">
            <h3 style="text-align: center;">DISTRIBUCIONES INESCO S.A.S.</h3>
            <h4 style="text-align: center;">REPORTE DE LIQUIDACIÓN Y DEVOLUCIONES</h4>
            <hr>
            <p><b>Ruta:</b> {ruta_elegida}</p>
            <p><b>Total de la Ruta:</b> ${total_libro_ruta:,.2f}</p>
            <p><b>Total Devoluciones Descontadas:</b> - ${total_valor_devuelto:,.2f}</p>
            <p><b>NETO A LIQUIDAR (LIQUIYA):</b> <b>${neto_a_liquidar:,.2f}</b></p>
            <br>
            <h4>Detalle de Productos Devueltos:</h4>
            <table style="width: 100%; border-collapse: collapse; text-align: left;">
                <tr style="border-bottom: 1px solid black;">
                    <th>Código</th>
                    <th>Producto</th>
                    <th>Cajas</th>
                    <th>Botellas</th>
                    <th>V. Unitario</th>
                    <th>Subtotal</th>
                </tr>
        """
        + "".join([
            f"<tr style='border-bottom: 1px solid #ddd;'><td>{r['Código']}</td><td>{r['Producto']}</td><td>{r['Cajas Dev.']}</td><td>{r['Botellas Dev.']}</td><td>${r['Precio Unitario']:,.2f}</td><td>${r['Subtotal Devolución']:,.2f}</td></tr>"
            for r in resumen_devoluciones
        ])
        + """
            </table>
            <br><br>
            <p>___________________________________</p>
            <p>Firma del Conductor / Liquidador</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.info("💡 Consejo: Usa `Ctrl + P` para imprimir este formato o guardarlo como PDF.")

else:
  st.info("👋 Sube el archivo PDF de la planilla en el panel izquierdo para comenzar.")
