@st.cache_data
def extraer_datos_completos(archivo):
    registros_completos = []
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

            # Extraer total de la ruta de forma más robusta
            for linea in lineas:
                if "Total a Cobrar" in linea or "Total" in linea:
                    if "Cont" in linea or "Prom" in linea or "Rec" in linea or "Com" in linea:
                        partes = linea.split()
                        for p in partes:
                            p_limpio = p.replace("$", "").replace(".", "").replace(",", ".")
                            try:
                                val = float(p_limpio)
                                if val > 5000:  # Umbral seguro para totales de ruta
                                    totales_por_ruta[ruta_actual] = val
                            except ValueError:
                                pass

            # Detectar clientes en el texto de la página
            for i, linea in enumerate(lineas):
                if "Fecha de Entrega:" in linea or "Ruta:" in linea:
                    if i >= 2:
                        posible_cliente = lineas[i - 2].strip()
                        if len(posible_cliente) > 2 and not posible_cliente.isdigit() and "ML3" not in posible_cliente and "CR" not in posible_cliente:
                            cliente_actual = posible_cliente

            # Extracción mediante tablas nativas mejorada
            tablas = pagina.extract_tables()
            for tabla in tablas:
                for fila in tabla:
                    fila_limpia = [
                        str(cell).strip() for cell in fila if cell is not None and str(cell).strip() != ""
                    ]
                    if len(fila_limpia) >= 3:
                        codigo_encontrado = ""
                        producto_encontrado = ""
                        cajas_val = 0.0
                        botellas_val = 0.0
                        precio_unitario = 0.0
                        importe_total = 0.0

                        # Análisis secuencial de las celdas de la fila de la tabla
                        for cell in fila_limpia:
                            # 1. Buscar código de producto (4 a 8 dígitos)
                            if cell.isdigit() and 4 <= len(cell) <= 8 and not codigo_encontrado:
                                codigo_encontrado = str(int(cell))
                            # 2. Capturar descripción si no es un número puro ni etiqueta del sistema
                            elif not cell.isdigit() and len(cell) > 2 and not any(w in cell for w in ["Venta", "Ruta", "SUB", "Total", "Pag:", "Fecha"]):
                                if producto_encontrado == "" and not any(char.isdigit() for char in cell[:2]):
                                    producto_encontrado = cell

                            # 3. Detectar formato cajas / botellas (ej. "0 / 15")
                            if "/" in cell and not cajas_val:
                                partes_cb = cell.split("/")
                                try:
                                    if len(partes_cb) == 2:
                                        cajas_val = float(partes_cb[0].strip().split()[-1])
                                        botellas_val = float(partes_cb[1].strip().split()[0])
                                except Exception:
                                    pass

                            # 4. Limpiar y capturar importes y precios reales
                            cell_clean = cell.replace("$", "").replace(".", "").replace(",", ".")
                            try:
                                num = float(cell_clean)
                                # Si es un valor monetario alto, lo tomamos como importe total de la línea
                                if num > 10000:
                                    importe_total = num
                                # Si es un valor menor, evaluamos si corresponde al precio unitario
                                elif 500 <= num <= 50000 and precio_unitario == 0.0:
                                    precio_unitario = num
                            except ValueError:
                                pass

                        if codigo_encontrado:
                            registros_completos.append({
                                "Ruta": ruta_actual,
                                "Cliente": cliente_actual,
                                "Código": codigo_encontrado,
                                "Producto": producto_encontrado if producto_encontrado else f"PRODUCTO REF {codigo_encontrado}",
                                "Cajas": cajas_val,
                                "Botellas": botellas_val,
                                "Precio_Unitario": precio_unitario,
                                "Importe_Total": importe_total
                            })

    if len(registros_completos) > 0:
        df_result = pd.DataFrame(registros_completos)
        # Eliminar posibles duplicados exactos de lectura en tabla
        df_result = df_result.drop_duplicates()
    else:
        df_result = pd.DataFrame(columns=["Ruta", "Cliente", "Código", "Producto", "Cajas", "Botellas", "Precio_Unitario", "Importe_Total"])

    return df_result, totales_por_ruta
