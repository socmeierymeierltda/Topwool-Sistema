"""Reglas para convertir un pedido web en una fila de la planilla de pedidos."""
import re
import unicodedata

from sqlalchemy import select

import db


def _sin_tildes(t):
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode()


def _num(q):
    q = float(q)
    return str(int(q)) if q.is_integer() else str(q).replace(".", ",")


def mapa_equivalencias():
    with db.Session() as s:
        eqs = list(s.scalars(select(db.Equivalencia)))
    por_id = {e.woo_id: e for e in eqs if e.woo_id}
    por_sku = {(e.sku or "").upper(): e for e in eqs if e.sku}
    return por_id, por_sku


def linea(item, por_id, por_sku):
    """Devuelve (texto, encontrado)."""
    e = por_id.get(item.get("variation_id") or -1) or por_id.get(item.get("product_id") or -1) \
        or por_sku.get((item.get("sku") or "").upper())
    q = _num(item.get("quantity", 1))
    if not e or not e.nombre_produccion:
        return f"{q} {item.get('name', '').lower()}", False
    u = (e.unidad or "un").strip()
    if u in ("k", "b"):
        return f"{q}{u} {e.nombre_produccion}", True
    return f"{q} {u} {e.nombre_produccion}", True


def unir(partes):
    if len(partes) <= 1:
        return "".join(partes)
    return ", ".join(partes[:-1]) + " y " + partes[-1]


def tipo_pago(order, aj):
    texto = _sin_tildes((order.get("payment_method", "") + " " + order.get("payment_method_title", "")).lower())
    if any(p.lower() in texto for p in db.lista(aj["pago_flow"])):
        return "flow"
    if any(_sin_tildes(p.lower()) in texto for p in db.lista(aj["pago_transferencia"])):
        return "transferencia"
    return "otro"


def empresa_sugerida(order, aj):
    """Sugerencia: si el cliente nombra una empresa en su nota se usa esa; si no, la por defecto."""
    nota = _sin_tildes((order.get("customer_note") or "").lower())
    for emp in db.lista(aj["empresas"]):
        if re.search(r"\b" + re.escape(_sin_tildes(emp.lower())) + r"\b", nota):
            return emp
    return aj["empresa_defecto"]


def preparar(order, aj, por_id, por_sku):
    """Arma todo lo que se mostrará y escribirá en el Drive."""
    lineas = [linea(i, por_id, por_sku) for i in order.get("line_items", [])]
    sh = order.get("shipping") or {}
    bi = order.get("billing") or {}
    nombre = (f"{sh.get('first_name') or bi.get('first_name', '')} {sh.get('last_name') or bi.get('last_name', '')}").strip().lower()
    direccion = ", ".join(x for x in [sh.get("address_1"), sh.get("address_2")] if x)
    pago = tipo_pago(order, aj)
    doc = aj["doc_flow"] if pago == "flow" else aj["doc_transferencia"] if pago == "transferencia" else ""
    return {
        "woo_id": order["id"],
        "numero": order.get("number"),
        "fecha": (order.get("date_created") or "")[:16].replace("T", " "),
        "estado_woo": order.get("status"),
        "total": float(order.get("total") or 0),
        "pago": pago,
        "pago_titulo": order.get("payment_method_title", ""),
        "nota_cliente": order.get("customer_note", ""),
        "sin_equivalencia": [t for t, ok in lineas if not ok],
        "fila": {
            "Documento": "",
            "Comentario": aj["comentario_transferencia"] if pago == "transferencia" else "",
            "Cliente": aj["prefijo_cliente"] + nombre,
            "Pedido": unir([t for t, _ in lineas]),
            "Telefono": (bi.get("phone") or sh.get("phone") or "").replace(" ", ""),
            "Lugar": aj["lugar"],
            "Direccion": direccion,
            "Comuna": (sh.get("city") or "").lower(),
            "Encargado": aj["encargado"],
            "Doc": doc,
            "Empresa": empresa_sugerida(order, aj),
            "Mail": bi.get("email", ""),
        },
    }


COLUMNAS = ["Documento", "Comentario", "Cliente", "Pedido", "Telefono", "Lugar",
            "Direccion", "Comuna", "Encargado", "Doc", "Empresa", "Mail"]
ENCABEZADOS = {"Lugar": "Lugar de preparación", "Direccion": "Dirección", "Doc": "Documento", "Telefono": "Teléfono"}
