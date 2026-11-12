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
    tipo = Column(String(30))            # hilado_merino | hilado_corriedale | vellon_natural | vellon_tenido | otro
    familia = Column(String(80))         # Malal, Relmu, Coirón… (para la regla de unidades)
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


TIPOS = [("hilado_corriedale", "Hilado corriedale"), ("hilado_merino", "Hilado merino"),
         ("vellon_natural", "Vellón natural"), ("vellon_tenido", "Vellón teñido"), ("otro", "Otro")]
_RELLENO = {"hilado", "hilados", "industrial", "artesanal", "ovillo", "ovillos", "lana", "de", "del", "la", "el",
            "vellon", "natural", "tenido", "100", "50", "gr", "g", "grs", "kg", "un"}


def tipo_normal(t):
    """Compatibilidad con la primera versión (hilado / vellon)."""
    return {"hilado": "hilado_corriedale", "vellon": "vellon_natural"}.get(t or "", t or "otro")


def adivinar_familia(nombre: str) -> str:
    for w in norm(nombre).replace("-", " ").split():
        if w not in _RELLENO and not w.isdigit() and len(w) > 1:
            return w.upper()
    return ""


def _merino(codigo, nombre):
    n = norm(nombre)
    if n:
        return "merino" in n or "sock" in n
    c = codigo.upper().lstrip("C")   # sin nombre: hilados merino de Defontana empiezan con HIM + C/DK/F/S
    return bool(re.match(r"HIM(C|DK|F|SF|SLDK|SOCKS)", c))


def clasificar(codigo: str, nombre: str = ""):
    """Tipo, unidad, kilos por unidad y visibilidad según el código de Defontana (y el nombre si lo hay).

    HI / HA  → hilado por kilo (granel)            CH… / CO…100 → ovillos de 100 g (cordonería)
    COVT50…  → ovillos de vellón teñido de 50 g    VPT… → vellón teñido · VP… / VCN → vellón natural
    El resto (accesorios, tejidos, ofertas…) queda como "otro" y oculto.
    """
    c = (codigo or "").upper()
    n = norm(nombre)
    if c.startswith("COVT50"):
        return "vellon_tenido", "un", 0.05, True
    if c.startswith("CH") or (c.startswith("CO") and c.endswith("100")):
        return ("hilado_merino" if _merino(c, nombre) else "hilado_corriedale"), "un", 0.1, True
    if c.startswith(("HI", "HA")):
        return ("hilado_merino" if _merino(c, nombre) else "hilado_corriedale"), "kg", 1.0, True
    if c.startswith("VPT"):
        return "vellon_tenido", "kg", 1.0, True
    if c.startswith("VP") or c == "VCN":
        return "vellon_natural", "kg", 1.0, True
    if "vell" in n:
        return ("vellon_tenido" if "teni" in n else "vellon_natural"), "kg", 1.0, True
    return "otro", "kg", 1.0, False


LINEAS_DEFECTO = ("HIMDK24.5, HIMDK, HIMSOCKS, HIMSLDK, HIMSF, HIMC, HIMF, HIMA, HIME, HIMMMA, HIMRM, "
                  "HICDK, HICDF, HICF, HILLAIMA, HILONQUIMAY, HIALGODON, HIR, HIL, HIP, HIT, HIA, HIE, "
                  "HAHM, HAC, HAF, HAH, HAP, COVT50, CO")


def familia_codigo(codigo: str, lineas: str = LINEAS_DEFECTO) -> str:
    """Familia (línea) según el comienzo del código. Los ovillos (CH…) usan la misma línea que el granel (H…):
    CHIMANEGRO100 → HIMA (Malal), CHIMDK24.5MOSTAZA100 → HIMDK24.5. Las líneas se editan en Ajustes."""
    c = codigo.upper()
    base = c[1:] if c.startswith("CH") else c
    pref = sorted({x.strip().upper() for x in (lineas or "").split(",") if x.strip()}, key=len, reverse=True)
    for x in pref:
        if base.startswith(x) or c.startswith(x):
            return x
    return re.sub(r"(100|10)$", "", base)


# ---------- lista de precios ----------
def _leer_hoja(contenido: bytes):
    wb = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    filas = list(ws.iter_rows(values_only=True))
    for i, r in enumerate(filas[:30]):
        celdas = [norm(x) for x in (r or ())]
        if any(c.startswith("codigo") or c in ("cod", "sku") for c in celdas):
            return celdas, filas[i + 1:]
    raise ValueError("No encontré la columna 'Código'.")


def leer_lista(contenido: bytes):
    """Lista de Defontana con columnas Código y Precio → {codigo: precio neto}. Ignora precios en 0."""
    enc, filas = _leer_hoja(contenido)
    i_cod = next(i for i, c in enumerate(enc) if c.startswith("codigo") or c in ("cod", "sku"))
    i_pre = next((i for i, c in enumerate(enc) if "precio" in c or c in ("valor", "neto")), None)
    if i_pre is None:
        raise ValueError("No encontré la columna 'Precio'.")
    out = {}
    for r in filas:
        if not r or len(r) <= max(i_cod, i_pre) or not r[i_cod]:
            continue
        precio = _num(r[i_pre])
        if precio:
            out[str(r[i_cod]).strip().upper()] = precio
    return out


def leer_nombres(contenido: bytes):
    """Informe de Inventario u otro Excel de Defontana con código y descripción → {codigo: nombre}."""
    try:
        enc, filas = _leer_hoja(contenido)
    except ValueError:
        return {}
    i_cod = next(i for i, c in enumerate(enc) if c.startswith("codigo") or c in ("cod", "sku"))
    i_nom = next((i for i, c in enumerate(enc) if i != i_cod and
                  (c.startswith("descrip") or c.startswith("nombre") or c.startswith("detalle")
                   or (c.startswith("articulo") and "codigo" not in c))), None)
    if i_nom is None:
        return {}
    out = {}
    for r in filas:
        if r and len(r) > max(i_cod, i_nom) and r[i_cod] and r[i_nom]:
            out[str(r[i_cod]).strip().upper()] = str(r[i_nom]).strip()
    return out


def nombres_equivalencias():
    """Nombres que ya están en la tabla de Equivalencias (código Defontana → nombre web)."""
    out = {}
    with db.Session() as s:
        for e in s.scalars(select(db.Equivalencia)):
            if e.codigo_defontana and (e.nombre_web or e.nombre_produccion):
                out.setdefault(e.codigo_defontana.strip().upper(), e.nombre_web or e.nombre_produccion)
    return out


def guardar_precios(base: dict | None, detalle: dict | None):
    """Actualiza la lista Base (por mayor) y/o Base detalle. Una lista que no se sube no se toca."""
    ahora = datetime.now()
    nuevos = 0
    nombres = nombres_equivalencias()
    with db.Session() as s:
        actuales = {p.codigo: p for p in s.scalars(select(Producto))}
        codigos = set(base or {}) | set(detalle or {})
        for cod in codigos:
            p = actuales.get(cod)
            if p is None:
                p = Producto(codigo=cod, editado=False)
                s.add(p)
                actuales[cod] = p
                nuevos += 1
            if base is not None:
                p.precio_mayor = base.get(cod)
            if detalle is not None:
                p.precio_detalle = detalle.get(cod)
            if not p.nombre and cod in nombres:
                p.nombre = nombres[cod]
            p.actualizado = ahora
        if base is not None:
            for cod, p in actuales.items():
                if cod not in codigos:
                    p.precio_mayor = None
        if detalle is not None:
            for cod, p in actuales.items():
                if cod not in codigos:
                    p.precio_detalle = None
        s.flush()
        _reclasificar(s, actuales.values())
        s.commit()
    return nuevos, len(codigos)


def _reclasificar(s, prods):
    """Tipo, unidad, visibilidad y familia para los productos que no se han editado a mano."""
    prods = list(prods)
    lineas = db.ajustes().get("cot_lineas", LINEAS_DEFECTO)
    for p in prods:
        if p.editado:
            continue
        tipo, unidad, peso, visible = clasificar(p.codigo, p.nombre or "")
        p.tipo, p.unidad, p.peso_kg = tipo, unidad, peso
        p.visible = visible and bool(p.precio_mayor or p.precio_detalle)
        p.familia = familia_codigo(p.codigo, lineas)


def guardar_nombres(nombres: dict):
    """Pone nombre a los productos del cotizador (desde el Informe de Inventario)."""
    if not nombres:
        return 0
    n = 0
    with db.Session() as s:
        prods = list(s.scalars(select(Producto)))
        for p in prods:
            nom = nombres.get(p.codigo)
            if nom and nom != p.nombre:
                p.nombre = nom
                n += 1
        if n:
            _reclasificar(s, prods)
        s.commit()
    return n


def nombre_visible(p):
    return p.nombre or p.codigo


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
REGLAS = {   # clave en Ajustes → (valor por defecto, descripción)
    "cot_kg_corriedale": (10, "Hilado corriedale: kg del mismo hilado"),
    "cot_kg_merino": (5, "Hilado merino: kg del mismo hilado"),
    "cot_kg_hilado_surtido": (10, "Hilados surtidos (industriales y artesanales): kg en total"),
    "cot_kg_vellon_natural": (10, "Vellón natural: kg del mismo vellón"),
    "cot_kg_tenido": (5, "Vellón teñido: kg de un mismo color"),
    "cot_kg_tenido_surtido": (10, "Vellón teñido surtido: kg en total"),
    "cot_un_bolsa": (10, "Unidades: de a cuántas por color (bolsa cerrada)"),
    "cot_un_familia": (50, "Unidades: de una misma familia"),
    "cot_un_surtido": (100, "Unidades surtidas: en total"),
}


def ajustes_cot(aj):
    out = {}
    for k, (d, _) in REGLAS.items():
        try:
            out[k] = float(str(aj.get(k, d)).replace(",", "."))
        except ValueError:
            out[k] = float(d)
    return out


def catalogo_publico():
    """Lo que ve la clienta: sin cantidades de stock."""
    with db.Session() as s:
        prods = sorted(s.scalars(select(Producto).where(Producto.visible.is_(True))), key=lambda p: nombre_visible(p))
    return [{"codigo": p.codigo, "nombre": nombre_visible(p), "tipo": tipo_normal(p.tipo), "unidad": p.unidad,
             "familia": (p.familia or "").upper(), "peso_kg": p.peso_kg or 1,
             "detalle": round(p.precio_detalle or p.precio_mayor or 0),
             "mayor": round(p.precio_mayor or p.precio_detalle or 0)}
            for p in prods if (p.precio_detalle or p.precio_mayor)]


def _fmt(n):
    return f"{n:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def calcular(pedido, aj):
    """pedido: [{codigo, cantidad}] → líneas con precio aplicado, disponibilidad, totales y consejos."""
    R = ajustes_cot(aj)
    E = 1e-9
    codigos = [str(x.get("codigo", "")).upper() for x in pedido]
    with db.Session() as s:
        prods = {p.codigo: p for p in s.scalars(select(Producto).where(Producto.codigo.in_(codigos)))}
        stock = {x.codigo: x.saldo for x in s.scalars(select(Stock).where(Stock.codigo.in_(codigos)))}
        hay_stock = s.scalars(select(Stock).limit(1)).first() is not None
    juntas = {}
    for x in pedido:
        p = prods.get(str(x.get("codigo", "")).upper())
        cant = _num(x.get("cantidad")) or 0
        if not p or not p.visible or cant <= 0:
            continue
        cant = float(int(round(cant))) if p.unidad == "un" else round(cant, 2)
        if cant > 0:
            juntas[p.codigo] = juntas.get(p.codigo, 0) + cant
    lineas = []
    for cod, cant in juntas.items():
        p = prods[cod]
        lineas.append({"codigo": p.codigo, "nombre": nombre_visible(p), "tipo": tipo_normal(p.tipo), "unidad": p.unidad,
                       "familia": (p.familia or p.codigo).upper(), "cantidad": cant,
                       "_mayor": p.precio_mayor or p.precio_detalle or 0,
                       "_detalle": p.precio_detalle or p.precio_mayor or 0, "por_mayor": False,
                       "disponible": (stock.get(p.codigo, 0) >= cant - E) if hay_stock else None})
    consejos = []
    granel = [l for l in lineas if l["unidad"] == "kg"]
    unid = [l for l in lineas if l["unidad"] == "un" and l["tipo"] != "otro"]

    # --- granel: hilados ---
    hil = [l for l in granel if l["tipo"].startswith("hilado")]
    kg_hil = sum(l["cantidad"] for l in hil)
    surtido_hil = kg_hil >= R["cot_kg_hilado_surtido"] - E
    for l in hil:
        minimo = R["cot_kg_merino"] if l["tipo"] == "hilado_merino" else R["cot_kg_corriedale"]
        l["por_mayor"] = surtido_hil or l["cantidad"] >= minimo - E
    if hil and not surtido_hil:
        falta = R["cot_kg_hilado_surtido"] - kg_hil
        consejos.append(f"Llevas {_fmt(kg_hil)} kg de hilado. Con {_fmt(falta)} kg más (puedes mezclar colores y "
                        "líneas), todo tu hilado pasa a precio por mayor.")

    # --- granel: vellón ---
    for l in granel:
        if l["tipo"] == "vellon_natural":
            l["por_mayor"] = l["cantidad"] >= R["cot_kg_vellon_natural"] - E
    ten = [l for l in granel if l["tipo"] == "vellon_tenido"]
    kg_ten = sum(l["cantidad"] for l in ten)
    surtido_ten = kg_ten >= R["cot_kg_tenido_surtido"] - E
    for l in ten:
        l["por_mayor"] = surtido_ten or l["cantidad"] >= R["cot_kg_tenido"] - E
    if ten and not surtido_ten and not all(l["por_mayor"] for l in ten):
        consejos.append(f"Llevas {_fmt(kg_ten)} kg de vellón teñido. Con {_fmt(R['cot_kg_tenido_surtido'] - kg_ten)} kg "
                        "más surtidos entre colores, todo pasa a precio por mayor.")

    # --- unidades (cordonería) ---
    bolsa = int(R["cot_un_bolsa"]) or 1
    validas = [l for l in unid if int(l["cantidad"]) % bolsa == 0]
    sueltas = [l for l in unid if int(l["cantidad"]) % bolsa != 0]
    un_total = sum(l["cantidad"] for l in validas)
    surtido_un = un_total >= R["cot_un_surtido"] - E
    por_fam = {}
    for l in validas:
        por_fam[l["familia"]] = por_fam.get(l["familia"], 0) + l["cantidad"]
    for l in validas:
        l["por_mayor"] = surtido_un or por_fam[l["familia"]] >= R["cot_un_familia"] - E
    if sueltas:
        consejos.append(f"Para precio por mayor en unidades, cada color va de a {bolsa} (bolsas cerradas). "
                        "Los que no son múltiplo de " + str(bolsa) + " quedan a precio detalle.")
    if validas and not surtido_un:
        cerca = [(f, n) for f, n in por_fam.items() if n < R["cot_un_familia"]]
        if cerca and not all(l["por_mayor"] for l in validas):
            f, n = max(cerca, key=lambda x: x[1])
            ej = next(l for l in validas if l["familia"] == f)
            etiqueta = adivinar_familia(ej["nombre"]).title() if ej["nombre"] != ej["codigo"] else f"la línea de {ej['nombre']}"
            consejos.append(f"Llevas {int(n)} unidades de {etiqueta}: con {int(R['cot_un_familia'] - n)} más de la misma "
                            f"familia, o llegando a {int(R['cot_un_surtido'])} surtidas, pasan a precio por mayor.")

    neto = 0
    for l in lineas:
        l["precio"] = round(l["_mayor"] if l["por_mayor"] else l["_detalle"])
        l["total"] = round(l["precio"] * l["cantidad"])
        neto += l["total"]
        del l["_mayor"], l["_detalle"]
    iva = round(neto * IVA)
    return {"lineas": lineas, "neto": neto, "iva": iva, "total": neto + iva, "consejos": consejos}


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
