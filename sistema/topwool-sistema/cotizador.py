"""Cotizador para clientas: productos, precios (lista Base / Base detalle) y stock de Defontana."""
import io
import json
import re
import secrets
import unicodedata
from datetime import datetime, timedelta

import openpyxl
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text, select

import db

IVA = 0.19
COMENTARIO = ("COTIZACIÓN VÁLIDA POR 30 DÍAS, SUJETA A DISPONIBILIDAD DE STOCK. "
              "NO INCLUYE ENVÍO. PAGO CONTRA ENTREGA.")


class Producto(db.Base):
    __tablename__ = "cot_productos"
    codigo = Column(String(120), primary_key=True)
    nombre = Column(String(250))
    tipo = Column(String(20))            # hilado | vellon | otro
    unidad = Column(String(10))          # kg | un
    peso_kg = Column(Float, default=1.0)  # kilos que pesa 1 unidad (0,1 para ovillos de 100 g)
    precio_mayor = Column(Float)         # lista "Base" (neto)
    precio_detalle = Column(Float)       # lista "Base detalle" (neto)
    visible = Column(Boolean, default=True)
    editado = Column(Boolean, default=False)   # si se cambió a mano, la lista no pisa tipo/unidad/visible
    actualizado = Column(DateTime)


class Stock(db.Base):
    __tablename__ = "cot_stock"
    codigo = Column(String(120), primary_key=True)
    saldo = Column(Float)
    fecha = Column(DateTime)


class Cotizacion(db.Base):
    __tablename__ = "cotizaciones"
    id = Column(Integer, primary_key=True)
    token = Column(String(40), unique=True, index=True)
    creada = Column(DateTime)
    tipo_cliente = Column(String(20))    # natural | empresa
    nombre = Column(String(200))
    rut = Column(String(20))
    razon_social = Column(String(200))
    giro = Column(String(200))
    direccion = Column(String(250))
    comuna = Column(String(120))
    ciudad = Column(String(120))
    telefono = Column(String(40))
    correo = Column(String(160))
    vendedor = Column(String(60))
    items = Column(Text)                 # JSON con las líneas calculadas
    neto = Column(Float)
    iva = Column(Float)
    total = Column(Float)
    estado = Column(String(20), default="nueva")   # nueva | en_defontana | descartada
    folio_defontana = Column(String(40))
    nota = Column(Text)


# ---------- utilidades ----------
def norm(t):
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", t).strip()


def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = re.sub(r"[^\d,.\-]", "", str(v))
    if not t:
        return None
    if "," in t and "." in t:          # 16.723,50
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    elif t.count(".") >= 1 and len(t.split(".")[-1]) == 3:   # 16.723
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


def rut_valido(rut: str) -> bool:
    r = re.sub(r"[^0-9kK]", "", rut or "").upper()
    if len(r) < 2 or not r[:-1].isdigit():
        return False
    cuerpo, dv = r[:-1], r[-1]
    s, m = 0, 2
    for d in reversed(cuerpo):
        s += int(d) * m
        m = 2 if m == 7 else m + 1
    esperado = 11 - s % 11
    esperado = "0" if esperado == 11 else "K" if esperado == 10 else str(esperado)
    return dv == esperado


def rut_formato(rut: str) -> str:
    r = re.sub(r"[^0-9kK]", "", rut or "").upper()
    if len(r) < 2:
        return rut or ""
    cuerpo = f"{int(r[:-1]):,}".replace(",", ".")
    return f"{cuerpo}-{r[-1]}"


def clasificar(codigo: str, nombre: str):
    """Adivina tipo y unidad; se puede corregir a mano en la pantalla de productos."""
    n = norm(nombre)
    if "vell" in n:
        tipo = "vellon"
    elif any(p in n for p in ("hilad", "lana", "algod", "sock", "merino", "ovillo", "hilo")):
        tipo = "hilado"
    else:
        tipo = "otro"
    c = (codigo or "").upper()
    if c.endswith("100"):
        return tipo, "un", 0.1
    if c.endswith("50"):
        return tipo, "un", 0.05
    return tipo, "kg", 1.0


# ---------- lista de precios ----------
def leer_lista_precios(contenido: bytes):
    """Excel exportado de Defontana con las listas Base y Base detalle.

    Acepta dos formas:
      a) una fila por producto, con columnas 'Base' y 'Base detalle';
      b) una fila por producto y lista, con una columna 'Lista' y otra 'Precio'.
    """
    wb = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
    productos = {}
    for ws in wb.worksheets:
        enc = None
        for r in ws.iter_rows(values_only=True):
            celdas = [norm(x) for x in (r or ())]
            if enc is None:
                if any(c.startswith("codigo") or c == "cod" or c == "sku" for c in celdas) and \
                   any("base" in c or "precio" in c or "lista" in c for c in celdas):
                    enc = celdas
                continue
            if not r or not any(r):
                continue
            fila = dict(zip(enc, r))
            col = lambda *pref: next((fila[k] for k in enc if k and any(k.startswith(p) for p in pref)), None)  # noqa: E731
            codigo = str(col("codigo", "cod", "sku") or "").strip().upper()
            if not codigo or codigo.startswith("TOTAL"):
                continue
            nombre = str(col("descripcion", "nombre", "detalle", "articulo", "producto", "glosa") or "").strip()
            p = productos.setdefault(codigo, {"codigo": codigo, "nombre": nombre, "mayor": None, "detalle": None})
            if nombre and not p["nombre"]:
                p["nombre"] = nombre
            base_det = next((k for k in enc if k and "base" in k and "detalle" in k), None)
            base = next((k for k in enc if k and "base" in k and "detalle" not in k), None)
            if base or base_det:
                if base_det and _num(fila.get(base_det)) is not None:
                    p["detalle"] = _num(fila.get(base_det))
                if base and _num(fila.get(base)) is not None:
                    p["mayor"] = _num(fila.get(base))
            else:
                lista = norm(col("lista", "nombre lista") or "")
                precio = _num(col("precio", "valor", "neto"))
                if precio is None:
                    continue
                if "detalle" in lista:
                    p["detalle"] = precio
                elif "base" in lista:
                    p["mayor"] = precio
        if productos:
            break
    if not productos:
        raise ValueError("No encontré una fila de encabezados con 'Código' y las columnas de precio "
                         "('Base' y 'Base detalle'). ¿Es la lista de precios de Defontana?")
    return list(productos.values())


def guardar_lista(items):
    ahora = datetime.now()
    nuevos = cambiados = 0
    with db.Session() as s:
        actuales = {p.codigo: p for p in s.scalars(select(Producto))}
        for it in items:
            p = actuales.get(it["codigo"])
            if p is None:
                tipo, unidad, peso = clasificar(it["codigo"], it["nombre"])
                p = Producto(codigo=it["codigo"], tipo=tipo, unidad=unidad, peso_kg=peso, visible=True, editado=False)
                s.add(p)
                nuevos += 1
            else:
                cambiados += 1
            if it["nombre"]:
                p.nombre = it["nombre"]
            if it["mayor"] is not None:
                p.precio_mayor = it["mayor"]
            if it["detalle"] is not None:
                p.precio_detalle = it["detalle"]
            p.actualizado = ahora
        s.commit()
    return nuevos, cambiados


def guardar_stock(saldos: dict):
    ahora = datetime.now()
    with db.Session() as s:
        s.query(Stock).delete()
        for cod, saldo in saldos.items():
            s.add(Stock(codigo=cod, saldo=saldo, fecha=ahora))
        s.commit()


def fecha_stock():
    with db.Session() as s:
        st = s.scalars(select(Stock).limit(1)).first()
        return st.fecha if st else None


# ---------- catálogo y cálculo ----------
def ajustes_cot(aj):
    def f(k, d):
        try:
            return float(str(aj.get(k, d)).replace(",", "."))
        except ValueError:
            return d
    return {"kg_hilado": f("cot_mayor_kg_hilado", 10), "kg_vellon": f("cot_mayor_kg_vellon", 10)}


def catalogo_publico():
    """Lo que ve la clienta: sin cantidades de stock."""
    with db.Session() as s:
        prods = list(s.scalars(select(Producto).where(Producto.visible.is_(True)).order_by(Producto.nombre)))
    return [{"codigo": p.codigo, "nombre": p.nombre, "tipo": p.tipo, "unidad": p.unidad, "peso_kg": p.peso_kg or 1,
             "detalle": round(p.precio_detalle or p.precio_mayor or 0),
             "mayor": round(p.precio_mayor or p.precio_detalle or 0)}
            for p in prods if (p.precio_detalle or p.precio_mayor)]


def calcular(pedido, aj):
    """pedido: [{codigo, cantidad}] → líneas con precio aplicado, disponibilidad y totales."""
    reglas = ajustes_cot(aj)
    codigos = [str(x.get("codigo", "")).upper() for x in pedido]
    with db.Session() as s:
        prods = {p.codigo: p for p in s.scalars(select(Producto).where(Producto.codigo.in_(codigos)))}
        stock = {x.codigo: x.saldo for x in s.scalars(select(Stock).where(Stock.codigo.in_(codigos)))}
        hay_stock = s.scalars(select(Stock).limit(1)).first() is not None
    lineas = []
    for x in pedido:
        p = prods.get(str(x.get("codigo", "")).upper())
        cant = _num(x.get("cantidad")) or 0
        if not p or not p.visible or cant <= 0:
            continue
        if p.unidad == "un":
            cant = float(int(round(cant)))
        else:
            cant = round(cant, 2)
        if cant <= 0:
            continue
        lineas.append({"codigo": p.codigo, "nombre": p.nombre, "tipo": p.tipo, "unidad": p.unidad,
                       "cantidad": cant, "kg": cant * (p.peso_kg or 1),
                       "_mayor": p.precio_mayor or p.precio_detalle or 0,
                       "_detalle": p.precio_detalle or p.precio_mayor or 0,
                       "disponible": (stock.get(p.codigo, 0) >= cant - 1e-9) if hay_stock else None})
    kg_hilado = sum(l["kg"] for l in lineas if l["tipo"] == "hilado")
    hilado_mayor = kg_hilado >= reglas["kg_hilado"] - 1e-9
    kg_por_vellon = {}
    for l in lineas:
        if l["tipo"] == "vellon":
            kg_por_vellon[l["codigo"]] = kg_por_vellon.get(l["codigo"], 0) + l["kg"]
    neto = 0
    for l in lineas:
        if l["tipo"] == "hilado":
            mayor = hilado_mayor
        elif l["tipo"] == "vellon":
            mayor = kg_por_vellon[l["codigo"]] >= reglas["kg_vellon"] - 1e-9
        else:
            mayor = False
        l["por_mayor"] = mayor
        l["precio"] = round(l["_mayor"] if mayor else l["_detalle"])
        l["total"] = round(l["precio"] * l["cantidad"])
        neto += l["total"]
        del l["_mayor"], l["_detalle"]
    iva = round(neto * IVA)
    falta_hilado = max(0, reglas["kg_hilado"] - kg_hilado) if kg_hilado and not hilado_mayor else 0
    return {"lineas": lineas, "neto": neto, "iva": iva, "total": neto + iva,
            "kg_hilado": round(kg_hilado, 2), "hilado_mayor": hilado_mayor,
            "falta_hilado_kg": round(falta_hilado, 2), "reglas": reglas}


def guardar_cotizacion(datos: dict, calculo: dict, vendedor: str) -> Cotizacion:
    c = Cotizacion(token=secrets.token_urlsafe(18), creada=datetime.now(), vendedor=vendedor or "",
                   items=json.dumps(calculo["lineas"], ensure_ascii=False),
                   neto=calculo["neto"], iva=calculo["iva"], total=calculo["total"], estado="nueva",
                   **{k: datos.get(k, "") for k in ("tipo_cliente", "nombre", "rut", "razon_social", "giro",
                                                     "direccion", "comuna", "ciudad", "telefono", "correo")})
    with db.Session() as s:
        s.add(c)
        s.commit()
    return c


def vendedores(aj):
    """'javier=javier, mariajesus=jesu' → {'jesu': 'mariajesus', ...} (código del link → nombre)."""
    out = {}
    for par in db.lista(aj.get("vendedores", "")):
        if "=" in par:
            nombre, cod = [x.strip() for x in par.split("=", 1)]
            out[cod.lower()] = nombre
    return out


# ---------- número en palabras ----------
_U = ["", "UNO", "DOS", "TRES", "CUATRO", "CINCO", "SEIS", "SIETE", "OCHO", "NUEVE", "DIEZ", "ONCE", "DOCE",
      "TRECE", "CATORCE", "QUINCE", "DIECISÉIS", "DIECISIETE", "DIECIOCHO", "DIECINUEVE", "VEINTE",
      "VEINTIUNO", "VEINTIDÓS", "VEINTITRÉS", "VEINTICUATRO", "VEINTICINCO", "VEINTISÉIS", "VEINTISIETE",
      "VEINTIOCHO", "VEINTINUEVE"]
_D = ["", "", "", "TREINTA", "CUARENTA", "CINCUENTA", "SESENTA", "SETENTA", "OCHENTA", "NOVENTA"]
_C = ["", "CIENTO", "DOSCIENTOS", "TRESCIENTOS", "CUATROCIENTOS", "QUINIENTOS", "SEISCIENTOS",
      "SETECIENTOS", "OCHOCIENTOS", "NOVECIENTOS"]


def _cientos(n):
    if n == 100:
        return "CIEN"
    c, r = divmod(n, 100)
    if r < 30:
        resto = _U[r]
    else:
        d, u = divmod(r, 10)
        resto = _D[d] + (f" Y {_U[u]}" if u else "")
    return " ".join(x for x in (_C[c], resto) if x)


def en_palabras(n: int) -> str:
    n = int(round(n))
    if n == 0:
        return "CERO PESOS"
    partes = []
    millones, resto = divmod(n, 1_000_000)
    miles, unidades = divmod(resto, 1000)
    if millones:
        partes.append("UN MILLÓN" if millones == 1 else f"{_cientos(millones)} MILLONES".replace("UNO MILLONES", "UN MILLONES"))
    if miles:
        partes.append("MIL" if miles == 1 else f"{_cientos(miles)} MIL".replace("UNO MIL", "UN MIL"))
    if unidades:
        partes.append(_cientos(unidades))
    texto = " ".join(partes)
    texto = texto.replace("VEINTIUN MIL", "VEINTIÚN MIL").replace("VEINTIUN MILLONES", "VEINTIÚN MILLONES")
    if texto.endswith("UNO"):
        texto = texto[:-3] + "UN"
    de = " DE" if not miles and not unidades and millones else ""
    return f"{texto}{de} PESOS"


# ---------- PDF ----------
def _clp(v):
    return "$ " + f"{float(v or 0):,.0f}".replace(",", ".")


def _cant(l):
    c = l["cantidad"]
    txt = f"{c:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{txt} {'KG' if l['unidad'] == 'kg' else 'UN'}"


def pdf(c: Cotizacion) -> bytes:
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    naranja = colors.HexColor("#E8762B")
    gris = colors.HexColor("#666666")
    linea = colors.HexColor("#D9D9D9")
    st = lambda n, **k: ParagraphStyle(n, fontName=k.pop("f", "Helvetica"), fontSize=k.pop("s", 8.5),  # noqa: E731
                                       leading=k.pop("l", 11), **k)
    p_norm, p_eti = st("n"), st("e", s=7.5, textColor=gris)
    p_bold = st("b", f="Helvetica-Bold", s=11, l=14)
    e = lambda t: escape(str(t or "").upper())  # noqa: E731

    ancho = letter[0] - 2.4 * cm
    emp = Paragraph("<b>SOC MEIER Y MEIER LTDA</b><br/>CURTIDURIA Y TALLERES DE ACABADO<br/>"
                    "ARAUCO 407, CURACAUTIN<br/>Teléfono: 972138397<br/>Email: socmeierymeierltda@gmail.com",
                    st("emp", s=8.5, l=13))
    caja = Table([[Paragraph("<b>R.U.T: 77511490-8</b>", st("r", f="Helvetica-Bold", s=10, textColor=naranja, alignment=1))],
                  [Paragraph("<b>Cotización</b>", st("r2", f="Helvetica-Bold", s=11, textColor=naranja, alignment=1))],
                  [Paragraph(f"<b>Solicitud web N° {c.id}</b>", st("r3", f="Helvetica-Bold", s=10, textColor=naranja, alignment=1))]],
                 colWidths=[6 * cm])
    caja.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 1, naranja), ("TOPPADDING", (0, 0), (-1, -1), 6),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    marca = Paragraph("<b>MEIER&nbsp;&amp;&nbsp;MEIER</b><br/><font size=8 color='#666666'>TOPWOOL</font>",
                      st("m", f="Times-Bold", s=13, l=16))
    cab = Table([[marca, emp, caja]], colWidths=[4 * cm, ancho - 10 * cm, 6 * cm])
    cab.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))

    fecha = c.creada or datetime.now()
    vence = fecha + timedelta(days=30)
    dato = lambda et, val: Paragraph(f"<font size=7.5 color='#666666'>{et}</font><br/>{e(val) or '—'}", p_norm)  # noqa: E731
    nombre_cli = c.razon_social or c.nombre
    datos = Table([
        [dato("Señor (es)", nombre_cli), dato("Ciudad", c.ciudad or c.comuna), dato("Giro", c.giro), dato("R.U.T", c.rut)],
        [dato("Dirección", c.direccion), dato("Condición de pago", "Contra entrega"), dato("Vendedor", c.vendedor), dato("Contacto", c.nombre if c.nombre != nombre_cli else "")],
        [dato("Comuna", c.comuna), dato("Fecha documento", fecha.strftime("%d-%m-%Y")), dato("Válida hasta", vence.strftime("%d-%m-%Y")), dato("Teléfono", c.telefono)],
    ], colWidths=[ancho * 0.34, ancho * 0.2, ancho * 0.26, ancho * 0.2])
    datos.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.5, linea), ("LINEBELOW", (0, -1), (-1, -1), 0.5, linea),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))

    lineas = json.loads(c.items or "[]")
    filas = [[Paragraph(f"<b>{h}</b>", p_norm) for h in ("N°", "Código", "Detalle", "Cant", "P. Unitario", "Total")]]
    for i, l in enumerate(lineas, 1):
        det = escape(l["nombre"] or "") + (" <font color='#666666' size=7>(precio por mayor)</font>" if l.get("por_mayor") else "")
        filas.append([str(i), Paragraph(escape(l["codigo"]), p_norm), Paragraph(det, p_norm),
                      _cant(l), _clp(l["precio"]), _clp(l["total"])])
    items = Table(filas, colWidths=[1.1 * cm, 3 * cm, ancho - 12.1 * cm, 2.3 * cm, 2.8 * cm, 2.9 * cm], repeatRows=1)
    items.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F3F3")),
                               ("BOX", (0, 0), (-1, -1), 0.5, linea), ("LINEBELOW", (0, 0), (-1, 0), 0.5, linea),
                               ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("ALIGN", (3, 0), (-1, -1), "RIGHT")]))

    coment = Table([[Paragraph(f"<font size=8 color='#666666'>Comentario</font><br/>{COMENTARIO}", p_norm)]],
                   colWidths=[ancho * 0.6 - 0.3 * cm])
    coment.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, linea), ("TOPPADDING", (0, 0), (-1, -1), 6),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 30)]))
    tot = Table([["Afecto (neto)", _clp(c.neto)], ["19% IVA", _clp(c.iva)],
                 [Paragraph("<b>Total</b>", p_bold), Paragraph(f"<font color='#E8762B'><b>{_clp(c.total)}</b></font>", st("t", f="Helvetica-Bold", s=11, l=14, alignment=2))],
                 [Paragraph(en_palabras(c.total), p_eti), ""]],
                colWidths=[ancho * 0.4 * 0.5, ancho * 0.4 * 0.5])
    tot.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, linea), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                             ("SPAN", (0, 3), (1, 3)), ("FONTSIZE", (0, 0), (-1, -1), 9)]))
    pie = Table([[coment, tot]], colWidths=[ancho * 0.6, ancho * 0.4])
    pie.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    nota = Paragraph("Cotización generada por la clienta en línea. Precios netos según lista vigente; "
                     "la disponibilidad se confirma al momento de la compra.", p_eti)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=1.2 * cm, rightMargin=1.2 * cm,
                            topMargin=1.2 * cm, bottomMargin=1.2 * cm, title=f"Cotización {c.id} - {nombre_cli}")
    doc.build([cab, Spacer(1, 14), datos, Spacer(1, 12), items, Spacer(1, 12), pie, Spacer(1, 8), nota])
    return buf.getvalue()
