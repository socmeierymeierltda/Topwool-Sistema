"""Descuentos masivos programados: % por producto con fecha de inicio y término (precio rebajado de WooCommerce)."""
import json
from collections import defaultdict
from datetime import date, datetime

from sqlalchemy import Column, Date, DateTime, Integer, String, Text

import db


class DescuentoProg(db.Base):
    __tablename__ = "descuentos_programados"
    id = Column(Integer, primary_key=True)
    nombre = Column(String(150))
    desde = Column(Date)
    hasta = Column(Date)
    detalle = Column(Text)        # json [{id, nombre, pct, items:[ids]}]
    n_items = Column(Integer)
    estado = Column(String(20), default="programado")   # programado | quitado
    creado = Column(DateTime, default=datetime.now)


def productos(cat):
    """Agrupa el catálogo por producto (padre o simple publicado) con sus colores."""
    hijos = defaultdict(list)
    for w in cat.values():
        if w.get("parent"):
            hijos[w["parent"]].append(w)
    out = []
    for w in cat.values():
        if w.get("parent") or w.get("estado") not in (None, "publish"):
            continue
        items = hijos.get(w["id"]) or ([w] if w.get("tipo") != "variable" else [])
        items = [i for i in items if i.get("precio")]
        if not items:
            continue
        precios = [i["precio"] for i in items]
        rebajas = [i for i in items if i.get("rebaja")]
        pct_act = None
        if rebajas:
            pct_act = round(100 * (1 - sum(i["rebaja"] / i["precio"] for i in rebajas) / len(rebajas)))
        out.append({"id": w["id"], "nombre": w["nombre"], "categoria": (w.get("categoria") or "Sin categoría").split(",")[0].strip(),
                    "n": len(items), "min": min(precios), "max": max(precios), "items": items,
                    "con_rebaja": len(rebajas), "pct_actual": pct_act,
                    "rebaja_desde": next((i["rebaja_desde"] for i in rebajas if i.get("rebaja_desde")), ""),
                    "rebaja_hasta": next((i["rebaja_hasta"] for i in rebajas if i.get("rebaja_hasta")), "")})
    out.sort(key=lambda p: (p["categoria"].lower(), p["nombre"].lower()))
    return out


def redondear(v, paso):
    return int(round(v / paso) * paso) if paso > 1 else int(round(v))


def calcular(prods, pcts, paso, desde, hasta):
    """pcts: {producto_id: %}. Devuelve (cambios para la web, detalle para el historial)."""
    cambios, detalle = [], []
    for p in prods:
        pct = pcts.get(p["id"])
        if not pct:
            continue
        ids = []
        for i in p["items"]:
            nuevo = redondear(i["precio"] * (1 - pct / 100), paso)
            if 0 < nuevo < i["precio"]:
                cambios.append({"id": i["id"], "parent": i.get("parent") or 0, "sale_price": nuevo,
                                "desde": desde.isoformat() if desde else None, "hasta": hasta.isoformat() if hasta else None})
                ids.append([i["id"], i.get("parent") or 0])
        if ids:
            detalle.append({"id": p["id"], "nombre": p["nombre"], "pct": pct, "items": ids})
    return cambios, detalle


def quitar(detalle):
    return [{"id": i, "parent": par, "sale_price": "", "desde": None, "hasta": None}
            for d in detalle for i, par in d["items"]]


def estado_txt(d, hoy=None):
    hoy = hoy or date.today()
    if d.estado == "quitado":
        return "quitado"
    if d.hasta and d.hasta < hoy:
        return "terminado"
    if d.desde and d.desde > hoy:
        return "programado"
    return "activo"


def guardar(s, nombre, desde, hasta, detalle):
    s.add(DescuentoProg(nombre=nombre[:150], desde=desde, hasta=hasta, detalle=json.dumps(detalle),
                        n_items=sum(len(d["items"]) for d in detalle)))
