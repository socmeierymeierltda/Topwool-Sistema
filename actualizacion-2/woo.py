"""Conexión con WooCommerce (API REST v3)."""
import httpx

import config
from demo import PEDIDOS_DEMO


def _client():
    return httpx.Client(
        base_url=config.WOO_URL.rstrip("/") + "/wp-json/wc/v3/",
        auth=(config.WOO_KEY, config.WOO_SECRET),
        timeout=40,
        headers={"User-Agent": "topwool-sistema"},
    )


def pedidos(estados):
    """Pedidos en los estados indicados (más recientes primero)."""
    if config.DEMO:
        return [p for p in PEDIDOS_DEMO if p["status"] in estados]
    out = []
    with _client() as c:
        for page in range(1, 6):
            r = c.get("orders", params={"status": ",".join(estados), "per_page": 50,
                                        "page": page, "orderby": "date", "order": "desc"})
            r.raise_for_status()
            data = r.json()
            out += data
            if len(data) < 50:
                break
    return out


def agregar_nota(order_id, texto):
    if config.DEMO:
        return
    with _client() as c:
        c.post(f"orders/{order_id}/notes", json={"note": texto, "customer_note": False})


def cambiar_estado(order_id, estado):
    if config.DEMO:
        return
    with _client() as c:
        c.put(f"orders/{order_id}", json={"status": estado}).raise_for_status()


def cancelar(order_id, nota):
    """Cambia el pedido a Cancelado (WooCommerce devuelve el stock solo)."""
    if config.DEMO:
        return
    with _client() as c:
        c.put(f"orders/{order_id}", json={"status": "cancelled"}).raise_for_status()
        c.post(f"orders/{order_id}/notes", json={"note": nota, "customer_note": False})


def catalogo():
    """Todos los productos y variaciones con su stock: {id: {...}}."""
    if config.DEMO:
        return {}
    items = {}
    with _client() as c:
        page = 1
        while True:
            r = c.get("products", params={"per_page": 100, "page": page})
            r.raise_for_status()
            data = r.json()
            for p in data:
                items[p["id"]] = {"id": p["id"], "parent": 0, "nombre": p["name"], "sku": p.get("sku"),
                                  "stock": p.get("stock_quantity"), "maneja": p.get("manage_stock")}
                if p.get("type") == "variable":
                    vp = 1
                    while True:
                        rv = c.get(f"products/{p['id']}/variations", params={"per_page": 100, "page": vp})
                        rv.raise_for_status()
                        vs = rv.json()
                        for v in vs:
                            items[v["id"]] = {"id": v["id"], "parent": p["id"],
                                              "nombre": p["name"] + " - " + ", ".join(a.get("option", "") for a in v.get("attributes", [])),
                                              "sku": v.get("sku"), "stock": v.get("stock_quantity"),
                                              "maneja": v.get("manage_stock")}
                        if len(vs) < 100:
                            break
                        vp += 1
            if len(data) < 100:
                break
            page += 1
    return items


def actualizar_stock(cambios):
    """cambios: lista de dicts {id, parent, stock}. Usa los endpoints batch."""
    if config.DEMO:
        return len(cambios)
    simples = [c for c in cambios if not c["parent"]]
    por_padre = {}
    for c in cambios:
        if c["parent"]:
            por_padre.setdefault(c["parent"], []).append(c)
    n = 0
    with _client() as cl:
        for i in range(0, len(simples), 100):
            lote = [{"id": c["id"], "manage_stock": True, "stock_quantity": c["stock"]} for c in simples[i:i + 100]]
            cl.post("products/batch", json={"update": lote}).raise_for_status()
            n += len(lote)
        for padre, vs in por_padre.items():
            lote = [{"id": c["id"], "manage_stock": True, "stock_quantity": c["stock"]} for c in vs]
            cl.post(f"products/{padre}/variations/batch", json={"update": lote}).raise_for_status()
            n += len(lote)
    return n
