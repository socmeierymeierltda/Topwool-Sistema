"""Conexión con WooCommerce (API REST v3)."""
import httpx

import config
from demo import PEDIDOS_DEMO


class _ClienteConReintentos(httpx.Client):
    """El hosting de topwool.cl a veces responde 503/429 cuando recibe muchas consultas seguidas:
    se espera un poco y se reintenta (hasta 5 veces) en vez de fallar."""
    def send(self, request, **kw):
        import random
        import time
        espera = 2
        for intento in range(6):
            try:
                r = super().send(request, **kw)
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError):
                if intento == 5:
                    raise
            else:
                if r.status_code not in (429, 502, 503, 504) or intento == 5:
                    return r
                r.close()
            time.sleep(espera + random.random())
            espera = min(espera * 2, 30)
        return r


def _client():
    return _ClienteConReintentos(
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


def historial(modificados_desde=None):
    """Todos los pedidos (todos los estados), de a 100, para los informes."""
    if config.DEMO:
        from demo import historial_demo
        yield from historial_demo()
        return
    params = {"per_page": 100, "orderby": "modified", "order": "asc", "status": "any"}
    if modificados_desde:
        params["modified_after"] = modificados_desde.strftime("%Y-%m-%dT%H:%M:%S")
    with _client() as c:
        page = 1
        while True:
            r = c.get("orders", params={**params, "page": page})
            r.raise_for_status()
            data = r.json()
            yield from data
            if len(data) < 100:
                break
            page += 1


def agregar_nota(order_id, texto):
    if config.DEMO:
        return
    with _client() as c:
        c.post(f"orders/{order_id}/notes", json={"note": texto, "customer_note": False})


def buscar_pedido(nombre):
    """Pedido más reciente cuyo nombre de cliente coincide (para avisar el despacho)."""
    if config.DEMO:
        return None
    import unicodedata

    def n(t):
        return " ".join(unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower().split())
    objetivo = n(nombre)
    with _client() as c:
        r = c.get("orders", params={"search": nombre, "per_page": 20, "orderby": "date", "order": "desc"})
        r.raise_for_status()
        for o in r.json():
            for d in (o.get("billing") or {}, o.get("shipping") or {}):
                if n(f"{d.get('first_name', '')} {d.get('last_name', '')}") == objetivo:
                    return o
    return None


def nota_cliente(order_id, texto):
    """Nota visible para el cliente: WooCommerce se la envía por correo."""
    if config.DEMO:
        return
    with _client() as c:
        c.post(f"orders/{order_id}/notes", json={"note": texto, "customer_note": True}).raise_for_status()


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


def catalogo(progreso=None):
    """Todos los productos y variaciones con su stock: {id: {...}}.
    Pide solo los campos necesarios y lee las variaciones en paralelo (antes tardaba minutos)."""
    if config.DEMO:
        return {}
    from concurrent.futures import ThreadPoolExecutor
    campos = "id,name,sku,type,stock_quantity,manage_stock"
    items, variables = {}, []
    with _client() as c:
        page = 1
        while True:
            r = c.get("products", params={"per_page": 100, "page": page, "_fields": campos, "status": "any"})
            r.raise_for_status()
            data = r.json()
            for p in data:
                items[p["id"]] = {"id": p["id"], "parent": 0, "nombre": p["name"], "sku": p.get("sku"),
                                  "stock": p.get("stock_quantity"), "maneja": p.get("manage_stock")}
                if p.get("type") == "variable":
                    variables.append(p)
            if len(data) < 100:
                break
            page += 1

    def variaciones(p):
        out = []
        with _client() as cv:
            vp = 1
            while True:
                rv = cv.get(f"products/{p['id']}/variations",
                            params={"per_page": 100, "page": vp, "_fields": "id,sku,stock_quantity,manage_stock,attributes"})
                rv.raise_for_status()
                vs = rv.json()
                for v in vs:
                    out.append({"id": v["id"], "parent": p["id"],
                                "nombre": p["name"] + " - " + ", ".join(a.get("option", "") for a in v.get("attributes", [])),
                                "sku": v.get("sku"), "stock": v.get("stock_quantity"), "maneja": v.get("manage_stock")})
                if len(vs) < 100:
                    break
                vp += 1
        return out

    hechos = 0
    with ThreadPoolExecutor(max_workers=3) as ex:
        for vs in ex.map(variaciones, variables):
            for v in vs:
                items[v["id"]] = v
            hechos += 1
            if progreso:
                progreso(f"Leyendo la web: {hechos} de {len(variables)} productos con variaciones")
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


def crear_cupon(codigo, monto, vence, correos, descripcion="", tipo="percent", minimo=0, usos=1, envio_gratis=False,
                productos=None, categorias=None, excluir=False):
    """Cupón de WooCommerce. tipo: percent (%) | fixed_cart ($ al carro) | fixed_product ($ por producto).
    correos vacío = cualquiera. productos/categorias: ids donde aplica (o donde NO aplica si excluir=True)."""
    if config.DEMO:
        return {"id": 0, "code": codigo, "demo": True}
    datos = {"code": codigo, "discount_type": tipo, "amount": str(monto), "individual_use": True,
             "description": descripcion[:200], "free_shipping": bool(envio_gratis),
             "date_expires": (vence.isoformat() + "T23:59:59") if vence else None,
             "email_restrictions": list(correos or [])}
    if usos:
        datos["usage_limit_per_user"] = int(usos)
    if minimo:
        datos["minimum_amount"] = str(minimo)
    datos.update(restriccion_productos(productos, categorias, excluir))
    with _client() as c:
        r = c.post("coupons", json=datos)
        if r.status_code >= 400:
            raise RuntimeError((r.json() or {}).get("message", r.text)[:200])
        return r.json()


def restriccion_productos(productos, categorias, excluir):
    p, c = [int(x) for x in productos or []], [int(x) for x in categorias or []]
    if excluir:
        return {"product_ids": [], "product_categories": [], "excluded_product_ids": p, "excluded_product_categories": c}
    return {"product_ids": p, "product_categories": c, "excluded_product_ids": [], "excluded_product_categories": []}


_LISTA_PRODUCTOS = {"t": 0, "datos": None}


def productos_y_categorias():
    """[(id, nombre)] de productos publicados y de categorías, para elegir dónde aplica un cupón (se guarda 1 hora)."""
    import time
    if _LISTA_PRODUCTOS["datos"] and time.time() - _LISTA_PRODUCTOS["t"] < 3600:
        return _LISTA_PRODUCTOS["datos"]
    if config.DEMO:
        datos = ([(101, "Malal Teñido"), (102, "Corriedale DK Teñido"), (103, "Merino Socks"), (104, "Algodón DK"),
                  (105, "Mecha Colores Naturales"), (106, "Relmu")],
                 [(11, "Lanas"), (12, "Algodón"), (13, "Vellón y mechas"), (14, "Kits")])
    else:
        prods, cats = [], []
        with _client() as c:
            for page in range(1, 11):
                r = c.get("products", params={"per_page": 100, "page": page, "status": "publish", "_fields": "id,name"})
                r.raise_for_status()
                prods += [(p["id"], p["name"]) for p in r.json()]
                if len(r.json()) < 100:
                    break
            for page in range(1, 4):
                r = c.get("products/categories", params={"per_page": 100, "page": page, "_fields": "id,name,count"})
                r.raise_for_status()
                cats += [(x["id"], x["name"]) for x in r.json() if x.get("count", 1)]
                if len(r.json()) < 100:
                    break
        import html
        datos = (sorted(((i, html.unescape(n)) for i, n in prods), key=lambda x: x[1].lower()),
                 sorted(((i, html.unescape(n)) for i, n in cats), key=lambda x: x[1].lower()))
    _LISTA_PRODUCTOS.update(t=time.time(), datos=datos)
    return datos


def cupones(buscar=""):
    """Cupones de la web, más recientes primero."""
    if config.DEMO:
        from datetime import date, timedelta
        return [{"id": 1, "code": "TW1126-AB12", "discount_type": "percent", "amount": "15.00", "status": "publish",
                 "date_expires": (date.today() + timedelta(days=10)).isoformat() + "T23:59:59", "usage_count": 3,
                 "email_restrictions": ["cliente1@ejemplo.cl", "cliente2@ejemplo.cl"], "description": "Campaña Mejores clientes",
                 "minimum_amount": "0.00", "free_shipping": False, "usage_limit_per_user": 1,
                 "product_ids": [101], "excluded_product_ids": [], "product_categories": [], "excluded_product_categories": []}]
    out = []
    with _client() as c:
        for page in range(1, 4):
            r = c.get("coupons", params={"per_page": 100, "page": page, "orderby": "date", "order": "desc",
                                         **({"search": buscar} if buscar else {})})
            r.raise_for_status()
            out += r.json()
            if len(r.json()) < 100:
                break
    return out


def actualizar_cupon(cid, datos):
    if config.DEMO:
        return
    with _client() as c:
        r = c.put(f"coupons/{cid}", json=datos)
        if r.status_code >= 400:
            raise RuntimeError((r.json() or {}).get("message", r.text)[:200])
