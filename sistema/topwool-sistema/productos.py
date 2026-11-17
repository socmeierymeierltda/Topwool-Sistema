"""Matriz de productos de Topwool: código Defontana → producto y color oficiales, catálogo, unidad,
costos y condiciones de venta. De aquí sale el nombre que va en los pedidos a producción."""
import io
import re

from sqlalchemy import Column, Float, String, Text, select

import cobranza as cb
import db


class Producto(db.Base):
    __tablename__ = "productos"
    codigo = Column(String(120), primary_key=True)
    descripcion = Column(String(250))
    catalogo = Column(String(60), index=True)      # Granel, Cordonería, Tienda
    tipo = Column(String(60))
    producto = Column(String(120), index=True)
    color = Column(String(120))
    unidad = Column(String(10))                    # Un, KG, ML
    cond_mayorista = Column(Text)
    cond_minorista = Column(String(120))
    familia = Column(String(60))
    costo_lana = Column(Float)
    costo_insumos = Column(Float)
    costo_mo = Column(Float)
    costo_flete = Column(Float)
    costo_total = Column(Float)
    stock = Column(Float)                          # último informe de inventario subido


def _txt(v):
    return "" if v is None else str(v).strip()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def importar_matriz(s, contenido: bytes) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(contenido), data_only=True, read_only=True)
    if "Equivalencias" not in wb.sheetnames:
        raise ValueError("No encontré la hoja 'Equivalencias' en el archivo.")
    datos = {}
    # Equivalencias y Condiciones vienen en el mismo orden (una fila por artículo)
    eq = [r for r in wb["Equivalencias"].iter_rows(min_row=2, values_only=True)]
    co = [r for r in wb["Condiciones"].iter_rows(min_row=2, values_only=True)] if "Condiciones" in wb.sheetnames else []
    for i, r in enumerate(eq):
        cod = _txt(r[0]).lstrip("'").upper()
        if not cod:
            continue
        c = co[i] if i < len(co) and _txt(co[i][0]) == _txt(r[1]) else None
        d = datos.setdefault(cod, {"codigo": cod})
        # si el código se repite, gana la primera fila con producto y color
        if d.get("producto") and d.get("color"):
            continue
        d.update(descripcion=_txt(r[1]), catalogo=_txt(r[2]), tipo=_txt(r[3]),
                 producto=_txt(r[4]), color=_txt(r[5]))
        if c:
            d.update(unidad=_txt(c[4]), cond_mayorista=_txt(c[5]), cond_minorista=_txt(c[6]))
    if "Matriz Productos" in wb.sheetnames:
        filas = list(wb["Matriz Productos"].iter_rows(values_only=True))
        cab_i = next((i for i, r in enumerate(filas) if r and _txt(r[0]) == "CodArticulo"), 1)
        for r in filas[cab_i + 1:]:
            cod = _txt(r[0]).lstrip("'").upper()
            if not cod:
                continue
            d = datos.setdefault(cod, {"codigo": cod, "descripcion": _txt(r[1])})
            d.update(familia=_txt(r[2]), costo_lana=_num(r[5]), costo_insumos=_num(r[7]), costo_mo=_num(r[8]),
                     costo_flete=_num(r[9]), costo_total=_num(r[10]))
    antes = {p.codigo: p.stock for p in s.scalars(select(Producto))}
    for d in datos.values():
        d["stock"] = antes.get(d["codigo"])
        s.merge(Producto(**d))
    return {"productos": len(datos), "con_nombre": sum(1 for d in datos.values() if d.get("producto"))}


_VOCALES = {"a": "[aá]", "e": "[eé]", "i": "[ií]", "o": "[oó]", "u": "[uúü]", "n": "[nñ]"}


def _patron(clave):
    """'teñido' o 'tenido' → reconoce ambas formas (con y sin tilde)."""
    base = cb.norm(clave)
    return r"(?<!\w)" + "".join(_VOCALES.get(ch, re.escape(ch)) for ch in base) + r"(?!\w)"


def abreviar(texto, aj):
    t = re.sub(r"\s+", " ", (texto or "").lower()).strip()      # minúsculas, conserva tildes
    for par in db.lista(aj.get("abreviaturas", "")):
        if "=" in par:
            k, v = [x.strip().lower() for x in par.split("=", 1)]
            t = re.sub(_patron(k), v, t)
    return re.sub(r"\s+", " ", t).strip()


def nombre(p: "Producto", aj) -> str:
    if not p or not p.producto:
        return ""
    color = p.color or ""
    base = p.producto
    if cb.norm(color) and cb.norm(color) not in cb.norm(base):   # "Relmu" + "crudo"
        base = f"{base} {color}"
    return abreviar(base, aj)


def unidad_web(p: "Producto", formato_actual: str, unidad_actual: str) -> str:
    """Bobinas y bolsas de 500 g mantienen su unidad; lo demás sale de la matriz (Un → un, KG → k)."""
    if formato_actual in ("bobina", "500g") or not p or not p.unidad:
        return unidad_actual
    u = p.unidad.strip().upper()
    return "k" if u == "KG" else "un" if u == "UN" else unidad_actual


def aplicar_a_equivalencias(s, aj) -> int:
    """Pone el nombre oficial (producto + color de la matriz) a cada producto de la web con código Defontana.
    No toca los que corregiste a mano (estado 'Revisado')."""
    prods = {p.codigo: p for p in s.scalars(select(Producto))}
    n = 0
    for e in s.scalars(select(db.Equivalencia)):
        if (e.estado or "").startswith("Revisado"):
            continue
        p = prods.get((e.codigo_defontana or "").upper())
        if not p:
            continue
        nuevo = nombre(p, aj)
        if nuevo:
            e.nombre_produccion = nuevo
            e.unidad = unidad_web(p, e.formato or "", e.unidad or "")
            n += 1
    return n


def guardar_stock(s, saldos: dict):
    for p in s.scalars(select(Producto)):
        p.stock = saldos.get(p.codigo)
