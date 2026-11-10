"""Informes de la tienda web: se guarda una copia de los pedidos de WooCommerce y se calculan
ventas por mes, productos, clientes, regiones, medios de pago y estacionalidad."""
import json
from collections import Counter, defaultdict
from datetime import date, datetime

from sqlalchemy import Column, Date, DateTime, Float, Integer, String, Text, func, select

import cobranza as cb
import db
import woo

VENTA = {"completed", "processing"}
NO_CONCRETADO = {"pending", "cancelled", "failed"}
REGIONES = {"CL-AI": "Aysén", "CL-AN": "Antofagasta", "CL-AP": "Arica y Parinacota", "CL-AR": "Araucanía",
            "CL-AT": "Atacama", "CL-BI": "Biobío", "CL-CO": "Coquimbo", "CL-LI": "O'Higgins", "CL-LL": "Los Lagos",
            "CL-LR": "Los Ríos", "CL-MA": "Magallanes", "CL-ML": "Maule", "CL-NB": "Ñuble", "CL-RM": "Metropolitana",
            "CL-TA": "Tarapacá", "CL-VS": "Valparaíso"}
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


class PedidoCache(db.Base):
    __tablename__ = "informes_pedidos"
    woo_id = Column(Integer, primary_key=True)
    numero = Column(String(30))
    fecha = Column(Date, index=True)
    modificado = Column(DateTime)
    estado = Column(String(20), index=True)
    total = Column(Float)
    envio = Column(Float)
    descuento = Column(Float)
    pago = Column(String(80))
    email = Column(String(200), index=True)
    nombre = Column(String(200))
    ciudad = Column(String(120))
    region = Column(String(60))
    cupones = Column(String(200))
    items = Column(Text)            # JSON: [[nombre, cantidad, total], ...]


def _f(t):
    try:
        return datetime.fromisoformat((t or "")[:19])
    except ValueError:
        return None


def sincronizar(s) -> int:
    ult = s.scalar(select(func.max(PedidoCache.modificado)))
    n = 0
    for o in woo.historial(ult):
        bi, sh = o.get("billing") or {}, o.get("shipping") or {}
        creado = _f(o.get("date_created"))
        if not creado:
            continue
        reg = sh.get("state") or bi.get("state") or ""
        s.merge(PedidoCache(
            woo_id=o["id"], numero=o.get("number"), fecha=creado.date(), modificado=_f(o.get("date_modified")) or creado,
            estado=o.get("status"), total=float(o.get("total") or 0), envio=float(o.get("shipping_total") or 0),
            descuento=float(o.get("discount_total") or 0), pago=(o.get("payment_method_title") or o.get("payment_method") or "")[:80],
            email=(bi.get("email") or "").lower(), nombre=f"{bi.get('first_name', '')} {bi.get('last_name', '')}".strip(),
            ciudad=(sh.get("city") or bi.get("city") or "").strip().title(), region=REGIONES.get(reg, reg),
            cupones=", ".join(c.get("code", "") for c in o.get("coupon_lines", []))[:200],
            items=json.dumps([[i.get("name"), float(i.get("quantity") or 0), float(i.get("total") or 0)]
                              for i in o.get("line_items", [])], ensure_ascii=False)))
        n += 1
        if n % 200 == 0:
            s.flush()
    return n


def ultima_sync(s):
    return s.scalar(select(func.max(PedidoCache.modificado)))


def familia(nombre):
    return (nombre or "").split(" - ")[0].strip()


def calcular(s, desde: date, hasta: date):
    todos = list(s.scalars(select(PedidoCache)))
    periodo = [p for p in todos if desde <= p.fecha <= hasta]
    ventas = [p for p in periodo if p.estado in VENTA]
    total = sum(p.total for p in ventas)
    n = len(ventas)

    # ventas por mes del período
    por_mes = defaultdict(lambda: [0.0, 0])
    for p in ventas:
        k = p.fecha.strftime("%Y-%m")
        por_mes[k][0] += p.total
        por_mes[k][1] += 1
    meses = sorted(por_mes)

    # estacionalidad: promedio por mes del año con TODO el historial
    hist = defaultdict(list)
    por_anio_mes = defaultdict(float)
    for p in todos:
        if p.estado in VENTA:
            por_anio_mes[(p.fecha.year, p.fecha.month)] += p.total
    for (y, m), v in por_anio_mes.items():
        hist[m].append(v)
    estacionalidad = [round(sum(hist[m]) / len(hist[m])) if hist[m] else 0 for m in range(1, 13)]

    # productos
    prod, fam = defaultdict(lambda: [0.0, 0.0, 0]), defaultdict(lambda: [0.0, 0.0])
    juntos = Counter()
    for p in ventas:
        its = json.loads(p.items or "[]")
        fams = sorted({familia(i[0]) for i in its if "envio" not in cb.norm(i[0])})
        for a in range(len(fams)):
            for b in range(a + 1, len(fams)):
                juntos[(fams[a], fams[b])] += 1
        for nombre, q, t in its:
            prod[nombre][0] += q
            prod[nombre][1] += t
            prod[nombre][2] += 1
            fam[familia(nombre)][0] += q
            fam[familia(nombre)][1] += t
    top_prod = sorted(prod.items(), key=lambda x: -x[1][1])[:25]
    top_fam = sorted(fam.items(), key=lambda x: -x[1][1])

    # clientes
    primera = {}
    for p in sorted((p for p in todos if p.estado in VENTA), key=lambda p: p.fecha):
        primera.setdefault(p.email or p.nombre, p.fecha)
    por_cliente = defaultdict(lambda: [0.0, 0, "", None])
    for p in ventas:
        k = p.email or p.nombre
        c = por_cliente[k]
        c[0] += p.total
        c[1] += 1
        c[2] = p.nombre
        c[3] = max(c[3], p.fecha) if c[3] else p.fecha
    nuevos = sum(1 for k in por_cliente if primera.get(k) and primera[k] >= desde)
    recurrentes = len(por_cliente) - nuevos
    repiten = sum(1 for c in por_cliente.values() if c[1] > 1)
    top_clientes = sorted(por_cliente.items(), key=lambda x: -x[1][0])[:20]

    # dormidos: compraron antes y no en los últimos 180 días (para email marketing)
    ultima_compra = {}
    for p in todos:
        if p.estado in VENTA:
            k = p.email or p.nombre
            ultima_compra[k] = max(ultima_compra.get(k, p.fecha), p.fecha)
    dormidos = sum(1 for f in ultima_compra.values() if (hasta - f).days > 180)

    def agrupar(attr):
        g = defaultdict(lambda: [0.0, 0])
        for p in ventas:
            k = getattr(p, attr) or "Sin dato"
            g[k][0] += p.total
            g[k][1] += 1
        return sorted(g.items(), key=lambda x: -x[1][0])

    sin_concretar = [p for p in periodo if p.estado in NO_CONCRETADO]
    dias = Counter(p.fecha.weekday() for p in ventas)
    cupones = Counter(c.strip() for p in ventas for c in (p.cupones or "").split(",") if c.strip())

    return {
        "total": total, "pedidos": n, "ticket": total / n if n else 0,
        "envio": sum(p.envio for p in ventas), "descuentos": sum(p.descuento for p in ventas),
        "clientes": len(por_cliente), "nuevos": nuevos, "recurrentes": recurrentes,
        "repiten_pct": 100 * repiten / len(por_cliente) if por_cliente else 0, "dormidos": dormidos,
        "meses": [{"mes": f"{MESES[int(k[5:]) - 1]} {k[2:4]}", "total": round(por_mes[k][0]), "n": por_mes[k][1]} for k in meses],
        "estacionalidad": estacionalidad, "anios_historial": len({y for (y, _) in por_anio_mes}),
        "top_prod": top_prod, "top_fam": top_fam, "juntos": juntos.most_common(10),
        "top_clientes": top_clientes, "regiones": agrupar("region"), "ciudades": agrupar("ciudad")[:15],
        "pagos": agrupar("pago"),
        "sin_concretar": len(sin_concretar), "sin_concretar_monto": sum(p.total for p in sin_concretar),
        "tasa_no_concreta": 100 * len(sin_concretar) / len(periodo) if periodo else 0,
        "dias": [dias.get(i, 0) for i in range(7)], "cupones": cupones.most_common(10),
        "n_historial": len(todos),
    }
