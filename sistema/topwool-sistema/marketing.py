"""Marketing: calendario de Chile (feriados, fechas comerciales, temporadas), meta de ventas mensual,
recomendaciones según el historial y grilla de contenidos (Instagram, Reels, Historias, Meta Ads, correo, web)."""
import calendar
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, String, Text, func, select

import cobranza as cb
import db
import informes as inf

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
CANALES = ["Instagram", "Reel", "Historias", "Facebook", "Meta Ads", "Correo", "Web", "WhatsApp", "TikTok"]
ESTADOS = ["idea", "en preparación", "listo", "programado", "publicado"]
PUBLICOS = ["Todos", "Clientes web", "Mayoristas"]


class Evento(db.Base):
    __tablename__ = "mk_eventos"
    id = Column(Integer, primary_key=True)
    fecha = Column(Date, index=True)
    hasta = Column(Date)
    nombre = Column(String(150))
    tipo = Column(String(20))          # feriado | comercial | temporada | propio
    nota = Column(String(300))
    por_confirmar = Column(Boolean, default=False)
    clave = Column(String(80), unique=True)   # para no duplicar los que carga el sistema


class Pieza(db.Base):
    """Una publicación o acción de la grilla."""
    __tablename__ = "mk_piezas"
    id = Column(Integer, primary_key=True)
    fecha = Column(Date, index=True)
    canal = Column(String(30))
    publico = Column(String(30))       # Todos | Clientes web | Mayoristas
    formato = Column(String(60))
    titulo = Column(String(200))
    producto = Column(String(150))
    campana = Column(String(120))
    copy = Column(Text)
    guia = Column(Text)                # paso a paso de la foto o video
    cta = Column(String(200))
    cupon = Column(String(60))
    estado = Column(String(20), default="idea")
    resultado = Column(Text)
    generada = Column(Boolean, default=False)
    creado = Column(DateTime, default=datetime.now)
    hora = Column(String(5))           # hora de publicación (Chile)
    ig_auto = Column(Boolean, default=False)   # publicar sola en Instagram a la hora indicada
    ig_id = Column(String(40), index=True)
    ig_link = Column(String(300))
    ig_error = Column(Text)
    ig_publicada = Column(DateTime)
    ig_metricas = Column(Text)


# ---------------- calendario de Chile ----------------
def _n_domingo(anio, mes, n):
    d = date(anio, mes, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _n_sabado(anio, mes, n):
    d = date(anio, mes, 1)
    d += timedelta(days=(5 - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


FERIADOS = {   # fuentes oficiales publicadas (24horas.cl / calendario oficial)
    2026: [("01-01", "Año Nuevo"), ("04-03", "Viernes Santo"), ("04-04", "Sábado Santo"), ("05-01", "Día del Trabajo"),
           ("05-21", "Glorias Navales"), ("06-21", "Día Nacional de los Pueblos Indígenas"), ("06-29", "San Pedro y San Pablo"),
           ("07-16", "Virgen del Carmen"), ("08-15", "Asunción de la Virgen"), ("09-18", "Independencia Nacional"),
           ("09-19", "Glorias del Ejército"), ("10-12", "Encuentro de Dos Mundos"), ("10-31", "Día de las Iglesias Evangélicas"),
           ("11-01", "Todos los Santos"), ("12-08", "Inmaculada Concepción"), ("12-25", "Navidad")],
    2027: [("01-01", "Año Nuevo"), ("03-26", "Viernes Santo"), ("03-27", "Sábado Santo"), ("05-01", "Día del Trabajo"),
           ("05-21", "Glorias Navales"), ("06-21", "Día Nacional de los Pueblos Indígenas"), ("06-28", "San Pedro y San Pablo"),
           ("07-16", "Virgen del Carmen"), ("08-15", "Asunción de la Virgen"), ("09-17", "Feriado adicional Fiestas Patrias"),
           ("09-18", "Independencia Nacional"), ("09-19", "Glorias del Ejército"), ("10-11", "Encuentro de Dos Mundos"),
           ("10-31", "Día de las Iglesias Evangélicas"), ("11-01", "Todos los Santos"), ("12-08", "Inmaculada Concepción"),
           ("12-25", "Navidad")],
}


def eventos_base(anio):
    """(clave, fecha, hasta, nombre, tipo, nota, por_confirmar)"""
    ev = []
    for md, n in FERIADOS.get(anio, []):
        m, d = map(int, md.split("-"))
        ev.append((f"fer-{anio}-{md}", date(anio, m, d), None, n, "feriado", "Feriado nacional: menos despachos ese día.", False))
    com = [
        ("sanvalentin", date(anio, 2, 14), None, "San Valentín", "Regalos tejidos, kits para parejas, «teje para quien quieres»."),
        ("clases", date(anio, 3, 2), date(anio, 3, 8), "Vuelta a clases", "Talleres y profesoras de tejido; lanas para manualidades escolares."),
        ("mujer", date(anio, 3, 8), None, "Día Internacional de la Mujer", "Historias de tejedoras y artesanas de la comunidad."),
        ("madre", _n_domingo(anio, 5, 2), None, "Día de la Madre", "Una de las fechas más fuertes: kits regalo, gift cards, lana para mamá tejedora."),
        ("knit", _n_sabado(anio, 6, 2), None, "Día Mundial del Tejido en Público", "Invitar a tejer en la plaza y subir fotos con #topwool."),
        ("padre", _n_domingo(anio, 6, 3), None, "Día del Padre", "Gorros, bufandas y calcetines (Socks) para tejerle al papá."),
        ("tripantu", date(anio, 6, 24), None, "We Tripantu (Año Nuevo mapuche)", "Lanas naturales, tradición textil de la Araucanía."),
        ("nino", _n_domingo(anio, 8, 2), None, "Día del Niño", "Proyectos fáciles para tejer con niños, colores vivos."),
        ("patrias", date(anio, 9, 10), date(anio, 9, 19), "Fiestas Patrias", "Mantas, chalecos y colores patrios; aviso de cierre de despachos."),
        ("halloween", date(anio, 10, 31), None, "Halloween", "Amigurumis y tejidos temáticos."),
        ("navidad", date(anio, 12, 1), date(anio, 12, 24), "Temporada de Navidad", "Regalos tejidos, kits y gift cards; avisar fecha límite de despacho."),
    ]
    for k, f, h, n, nota in com:
        ev.append((f"com-{anio}-{k}", f, h, n, "comercial", nota, False))
    if anio == 2026:
        ev.append(("com-2026-blackfriday", date(2026, 11, 27), date(2026, 11, 30), "Black Friday (CCS)", "comercial", "27 al 30 de noviembre según la CCS.", False))
    else:
        ev.append((f"com-{anio}-blackfriday", date(anio, 11, 26), date(anio, 11, 29), "Black Friday", "comercial", "Fecha por confirmar por la CCS (suele ser el fin de semana después del cuarto jueves de noviembre).", True))
        ev.append((f"com-{anio}-cyber", date(anio, 6, 1), date(anio, 6, 3), "CyberDay", "comercial", "Fecha por confirmar por la CCS (suele ser a inicios de junio).", True))
    tem = [
        ("otono", date(anio, 3, 20), None, "Comienza el otoño", "Arranca la temporada alta de lana: lanzar colecciones y colores nuevos."),
        ("invierno", date(anio, 6, 21), None, "Comienza el invierno", "Peak de ventas de lana gruesa, mantas y gorros."),
        ("primavera", date(anio, 9, 22), None, "Comienza la primavera", "Algodón y proyectos livianos; liquidar lana gruesa."),
        ("verano", date(anio, 12, 21), None, "Comienza el verano", "Algodón, crochet de verano, tejer en vacaciones."),
    ]
    for k, f, h, n, nota in tem:
        ev.append((f"tem-{anio}-{k}", f, h, n, "temporada", nota, False))
    return ev


def cargar_eventos(s, anios):
    ya = {c for (c,) in s.execute(select(Evento.clave)) if c}
    for a in anios:
        for clave, f, h, n, t, nota, pc in eventos_base(a):
            if clave not in ya:
                s.add(Evento(clave=clave, fecha=f, hasta=h, nombre=n, tipo=t, nota=nota, por_confirmar=pc))


def eventos_entre(s, desde, hasta):
    return list(s.scalars(select(Evento).where(Evento.fecha <= hasta,
                                               ((Evento.hasta.is_(None)) & (Evento.fecha >= desde)) | (Evento.hasta >= desde))
                          .order_by(Evento.fecha)))


# ---------------- meta y números ----------------
def _ventas(s):
    return [p for p in s.scalars(select(inf.PedidoCache).where(inf.PedidoCache.estado.in_(inf.VENTA)))]


def panel_meta(s, meta, hoy=None):
    hoy = hoy or date.today()
    ventas = _ventas(s)
    ini = hoy.replace(day=1)
    dias_mes = calendar.monthrange(hoy.year, hoy.month)[1]
    mes = [p for p in ventas if ini <= p.fecha <= hoy]
    total = sum(p.total for p in mes)
    n = len(mes)
    ticket_12m = [p.total for p in ventas if p.fecha >= hoy - timedelta(days=365)]
    ticket = (sum(ticket_12m) / len(ticket_12m)) if ticket_12m else (total / n if n else 0)
    dias_pasados = hoy.day
    proyeccion = total / dias_pasados * dias_mes if dias_pasados else 0
    falta = max(0, meta - total)
    dias_quedan = dias_mes - dias_pasados
    anio_pasado = sum(p.total for p in ventas if p.fecha.year == hoy.year - 1 and p.fecha.month == hoy.month)
    # mejor mes de la historia y promedio de los últimos 12 meses completos
    por_mes = defaultdict(float)
    for p in ventas:
        por_mes[(p.fecha.year, p.fecha.month)] += p.total
    ult12 = [v for (y, m), v in por_mes.items() if date(y, m, 1) < ini and date(y, m, 1) >= date(ini.year - 1, ini.month, 1)]
    return {
        "meta": meta, "total": total, "pedidos": n, "pct": 100 * total / meta if meta else 0,
        "proyeccion": proyeccion, "pct_proy": 100 * proyeccion / meta if meta else 0, "falta": falta,
        "dias_quedan": dias_quedan, "por_dia": falta / dias_quedan if dias_quedan else falta,
        "ticket": ticket, "pedidos_faltan": round(falta / ticket) if ticket else 0,
        "pedidos_dia": (falta / ticket / dias_quedan) if ticket and dias_quedan else 0,
        "anio_pasado": anio_pasado, "prom12": sum(ult12) / len(ult12) if ult12 else 0,
        "mejor_mes": max(por_mes.items(), key=lambda x: x[1]) if por_mes else None,
        "mes_nombre": MESES[hoy.month - 1], "hay_datos": bool(ventas),
        "historial": [(f"{MESES[m - 1][:3]} {str(y)[2:]}", round(v)) for (y, m), v in sorted(por_mes.items())][-18:],
    }


def canal_doc(d):
    """web | mesón | mayoristas (venta directa: tiendas, revendedores, clientes de vendedores)."""
    c = cb.norm(d.cliente or "")
    if c.startswith("web") or d.tipo == "cv":
        return "web"
    if cb.meson(d.comentario or "") or c.startswith(("vta cura", "venta cura")):
        return "mesón"
    return "mayoristas"


def ventas_empresa(s, hoy=None):
    """Ventas del mes según Cobranza (documentos de Defontana), por canal."""
    hoy = hoy or date.today()
    ini = hoy.replace(day=1)
    por, ant = Counter(), Counter()
    for d in s.scalars(select(cb.Documento).where(cb.Documento.fecha >= date(ini.year - 1, ini.month, 1))):
        if not d.fecha or (d.tipo or "") == "nc":
            continue
        if ini <= d.fecha <= hoy:
            por[canal_doc(d)] += d.total or 0
        elif d.fecha.year == hoy.year - 1 and d.fecha.month == hoy.month:
            ant[canal_doc(d)] += d.total or 0
    return {"total": sum(por.values()), "canales": por, "anio_pasado": sum(ant.values()), "hay": bool(por or ant)}


def mayoristas_a_contactar(s, hoy=None, n=25):
    """Clientes de venta directa ordenados por urgencia: los que ya pasaron su ritmo habitual de compra."""
    import gestor as g
    import seguimiento as sg
    hoy = hoy or date.today()
    por = defaultdict(list)
    for d in s.scalars(select(cb.Documento).where(cb.Documento.fecha >= hoy - timedelta(days=730))):
        if d.fecha and d.cliente and canal_doc(d) == "mayoristas" and (d.tipo or "") != "nc":
            por[d.cliente].append(d)
    contactos = list(s.scalars(select(g.Contacto)))
    out = []
    for cliente, docs in por.items():
        fechas = sorted({d.fecha for d in docs})
        gaps = [(b - a).days for a, b in zip(fechas, fechas[1:])]
        cada = round(sum(gaps) / len(gaps)) if gaps else None
        dias = (hoy - fechas[-1]).days
        total12 = sum(d.total or 0 for d in docs if d.fecha >= hoy - timedelta(days=365))
        atrasado = (cada and dias > max(30, cada * 1.4)) or (not cada and 60 < dias < 400)
        tel = next((c.telefono for c in contactos if c.telefono and sg.parecido(cliente, c.nombre or "") >= 0.8), "")
        out.append({"cliente": cliente, "ultima": fechas[-1], "dias": dias, "cada": cada, "compras": len(fechas),
                    "total12": total12, "vendedor": docs[-1].vendedor or "", "atrasado": bool(atrasado),
                    "whatsapp": whatsapp(tel)})
    out.sort(key=lambda x: (not x["atrasado"], -x["total12"]))
    return out[:n], sum(1 for x in out if x["atrasado"]), len(out)


def whatsapp(tel, texto=""):
    import re
    from urllib.parse import quote
    n = re.sub(r"\D", "", tel or "")
    if not n:
        return ""
    if len(n) == 9 and n.startswith("9"):
        n = "56" + n
    elif len(n) == 8:
        n = "569" + n
    return f"https://wa.me/{n}" + (f"?text={quote(texto)}" if texto else "")


def estacionalidad(s):
    """Índice por mes (1 = mes promedio) y productos que más se venden cada mes del año."""
    ventas = _ventas(s)
    por = defaultdict(float)
    prod_mes = defaultdict(Counter)
    for p in ventas:
        por[(p.fecha.year, p.fecha.month)] += p.total
        for nombre, q, t in json.loads(p.items or "[]"):
            if "envio" not in cb.norm(nombre or ""):
                prod_mes[p.fecha.month][inf.familia(nombre)] += t
    prom_mes = {m: [v for (y, mm), v in por.items() if mm == m] for m in range(1, 13)}
    prom = {m: sum(v) / len(v) for m, v in prom_mes.items() if v}
    base = sum(prom.values()) / len(prom) if prom else 0
    return {m: (prom.get(m, 0) / base if base else 0) for m in range(1, 13)}, prod_mes


def familias(s, n=40):
    c = Counter()
    for p in _ventas(s):
        if p.fecha >= date.today() - timedelta(days=365):
            for nombre, q, t in json.loads(p.items or "[]"):
                if "envio" not in cb.norm(nombre or ""):
                    c[inf.familia(nombre)] += 1
    return [f for f, _ in c.most_common(n)]


def recomendaciones(s, pm, aj):
    """Lista de (titulo, detalle, enlace) priorizada según cómo va el mes."""
    import gestor as g
    hoy = date.today()
    out = []
    idx, prod_mes = estacionalidad(s)
    clientes = g.clientes(s, dias_dormido=int(aj.get("gestor_dias_dormido") or 180)) if pm["hay_datos"] else []
    res = g.resumen(clientes) if clientes else {}
    if pm["hay_datos"] and pm["falta"] > 0:
        out.append(("Para llegar a la meta",
                    f"Faltan {fmt(pm['falta'])} en {pm['dias_quedan']} días: unos {pm['pedidos_faltan']} pedidos "
                    f"(≈ {pm['pedidos_dia']:.1f} al día) con el ticket promedio de {fmt(pm['ticket'])}. "
                    f"Al ritmo actual cerrarías el mes en {fmt(pm['proyeccion'])} ({pm['pct_proy']:.0f} % de la meta).", ""))
    lista_m, atrasados, n_m = mayoristas_a_contactar(s)
    if atrasados:
        out.insert(1 if out else 0, ("Mayoristas que ya deberían haber comprado",
                    f"{atrasados} de tus {n_m} clientes de venta directa pasaron su ritmo habitual de compra. "
                    "Escríbeles por WhatsApp con novedades y stock disponible (lista abajo, «Mayoristas a contactar»).", "#mayoristas"))
    if res.get("en_riesgo"):
        out.append(("Reactivar a quienes están por dormirse",
                    f"{res['en_riesgo']} clientas ya pasaron su ritmo habitual de compra. Son las más fáciles de convertir: "
                    "correo con su producto favorito y un cupón de 10 % por 7 días.", "/gestor/campana?seg=en_riesgo"))
    if res.get("dormidos"):
        out.append(("Recuperar clientes dormidos",
                    f"{res['dormidos']} personas no compran hace más de {aj.get('gestor_dias_dormido') or 180} días. "
                    "Correo «te echamos de menos» + público personalizado en Meta Ads con esos correos.", "/gestor/campana?seg=dormidos"))
    if res.get("mejores"):
        out.append(("Premiar a las mejores clientas",
                    f"{res['mejores']} clientas concentran gran parte de las ventas. Acceso anticipado a colores nuevos o a "
                    "Black Friday, y pídeles una foto de su tejido para las redes (contenido real).", "/gestor/campana?seg=mejores"))
    sig = (hoy.month % 12) + 1
    if idx.get(sig) and idx[sig] > 1.1:
        top = ", ".join(f for f, _ in prod_mes[sig].most_common(3))
        out.append((f"{MESES[sig - 1].capitalize()} es temporada alta (+{(idx[sig] - 1) * 100:.0f} %)",
                    f"Prepara desde ya fotos y stock de lo que más se vende ese mes: {top}.", "/marketing/calendario"))
    elif idx.get(hoy.month) and idx[hoy.month] < 0.9:
        out.append((f"{MESES[hoy.month - 1].capitalize()} es un mes más bajo ({(idx[hoy.month] - 1) * 100:.0f} %)",
                    "Buen momento para liquidar lo que menos rota, empujar algodón y armar la base de correos para la temporada alta.", ""))
    pend = s.scalar(select(func.count()).select_from(inf.PedidoCache).where(
        inf.PedidoCache.estado.in_(["pending", "failed"]), inf.PedidoCache.fecha >= hoy - timedelta(days=30)))
    if pend:
        out.append(("Pedidos sin terminar", f"{pend} pedidos quedaron sin pagar en los últimos 30 días: escríbeles desde «Sin pagar».", "/sin-pagar"))
    out.append(("Siempre activo en Meta Ads",
                "Una campaña de remarketing a quienes visitaron la web o interactuaron en Instagram en los últimos 30 días, "
                "con los reels que mejor funcionaron. Es la publicidad que más convierte por peso invertido.", ""))
    return out


def fmt(v):
    return "$" + f"{float(v or 0):,.0f}".replace(",", ".")


# ---------------- generador de grilla ----------------
GUIAS = {
    "Carrusel de producto": (
        "1. Luz natural junto a una ventana, sin flash, a media mañana.\n"
        "2. Fondo neutro y real: mesa de madera, lino o lana cruda.\n"
        "3. Foto 1: los ovillos/madejas de {producto} en varios colores, ordenados en degradé.\n"
        "4. Foto 2: primer plano de la textura (acércate hasta ver la fibra).\n"
        "5. Foto 3: una muestra tejida o prenda hecha con {producto}.\n"
        "6. Foto 4: el ovillo en la mano, para que se vea el tamaño.\n"
        "7. Última foto: ficha simple (gramos, metros, palillo recomendado, precio)."),
    "Reel": (
        "1. Graba vertical (9:16) con el celular apoyado, luz natural.\n"
        "2. Toma 1 (3 s): mano sacando {producto} de la bolsa o canasto — en los primeros 2 s pon texto en pantalla con el gancho.\n"
        "3. Toma 2 (5 s): palillos o crochet tejiendo de cerca, sonido real del tejido.\n"
        "4. Toma 3 (4 s): la prenda terminada puesta o doblada.\n"
        "5. Cierra con el logo o «topwool.cl». Total 12–20 s, audio en tendencia bajito."),
    "Historias": (
        "1. 3 a 5 historias seguidas, verticales.\n"
        "2. Historia 1: foto real del producto o del taller con encuesta («¿qué color tejerías?»).\n"
        "3. Historia 2: detalle o proceso (despacho, ovillado, teñido).\n"
        "4. Historia 3: sticker de enlace a topwool.cl{cta_link} o cuenta regresiva si hay fecha."),
    "Comunidad": (
        "1. Pide a una clienta permiso para usar la foto de su tejido (o usa una que les hayan etiquetado).\n"
        "2. Si no hay, fotografía un tejido terminado del taller con la lana a un lado.\n"
        "3. Texto contando quién tejió y con qué lana; etiquétala."),
    "Meta Ads": (
        "1. Usa el reel o carrusel de la semana que tuvo más guardados/compartidos.\n"
        "2. Objetivo: Ventas (con el píxel de la web) o Tráfico si aún no hay píxel.\n"
        "3. Público A: remarketing — visitantes de la web y quienes interactuaron con Instagram (30 días).\n"
        "4. Público B: mujeres 25–65, Chile, intereses tejido, crochet, lana, manualidades.\n"
        "5. Presupuesto de prueba: $5.000–$10.000 diarios por 5–7 días; deja el que tenga menor costo por compra."),
    "Correo": ("1. Crea la campaña en Gestor web → Campañas con el grupo indicado.\n"
               "2. Usa como imagen la foto principal del carrusel de la semana."),
    "WhatsApp mayoristas": (
        "1. Foto de la estantería o mesa con todos los colores de {producto} que hay en stock (luz natural, de frente).\n"
        "2. 2–3 fotos de detalle: madejas/ovillos apilados por color, con etiqueta visible.\n"
        "3. Envíalo por la lista de difusión de mayoristas (no por grupo) desde el WhatsApp Business.\n"
        "4. Adjunta la lista de precios mayoristas en PDF y súbelo también al estado de WhatsApp."),
    "Correo mayoristas": (
        "1. Usa una foto grupal de la línea {producto} (todos los colores, de arriba).\n"
        "2. Créalo en Gestor web → Campañas con el grupo «Lista subida» (tipo mayorista).\n"
        "3. Adjunta o enlaza la lista de precios y el catálogo."),
    "Instagram mayoristas": (
        "1. Foto de un pedido mayorista armado (cajas o bolsas con madejas, sin datos del cliente).\n"
        "2. Segunda foto: la bodega o el taller, para mostrar capacidad.\n"
        "3. Destácalo en las historias destacadas «Mayoristas»."),
    "Banner web": ("1. Foto horizontal (proporción 3:1) con espacio libre a la izquierda para el texto.\n"
                   "2. Texto corto: oferta + fecha límite."),
}

COPIES = {
    "Carrusel de producto": "{producto}: {gancho}\n\nDesliza para ver los colores y la textura de cerca 👉\n\n"
                            "Ideal para {uso}. Tejida y teñida con cariño en la Araucanía.\n\n🛒 topwool.cl · Envíos a todo Chile",
    "Reel": "Así se teje {producto} 🧶 {gancho}\n\n¿La conocías? Cuéntanos qué tejerías con ella 👇\n\ntopwool.cl",
    "Historias": "Encuesta: ¿qué color de {producto} usarías para tu próximo proyecto? → enlace a topwool.cl",
    "Comunidad": "Tejido de nuestra comunidad 💛 Gracias @… por compartir lo que hiciste con {producto}.\n\n"
                 "¿Tejiste con lana Topwool? Etiquétanos y te compartimos.",
    "Meta Ads": "{producto} — {gancho}. Envíos a todo Chile desde la Araucanía. Compra en topwool.cl",
    "Correo": "Correo a clientas: {gancho}",
    "Banner web": "{gancho}",
    "WhatsApp mayoristas": "¡Hola! 👋 Les cuento las novedades de Topwool esta semana:\n\n"
                           "🧶 {producto}: {gancho}. Tenemos stock disponible para despacho inmediato.\n"
                           "📦 Despachos a todo Chile · factura · condiciones mayoristas.\n\n"
                           "Respondan este mensaje con lo que necesitan y les cotizamos hoy mismo.",
    "Correo mayoristas": "Novedades Topwool para tiendas: {producto} — {gancho}.\n\nStock disponible, plazos de "
                         "producción y lista de precios actualizada. Responde este correo para hacer tu pedido.",
    "Instagram mayoristas": "¿Tienes una tienda o vendes lana? 🧶 Trabajamos con tiendas de todo Chile: {producto} y "
                            "toda nuestra línea con precios mayoristas, factura y despacho.\n\nEscríbenos por DM o WhatsApp.",
}


def _uso(producto):
    t = cb.norm(producto)
    if "sock" in t:
        return "calcetines y prendas que se lavan seguido"
    if "algodon" in t:
        return "prendas livianas, amigurumis y proyectos de verano"
    if "mecha" in t or "vellon" in t or "relmu" in t:
        return "telar, fieltro y tejidos XL"
    return "chalecos, gorros y mantas abrigadas"


def ritmo(aj):
    """«lun=Carrusel de producto, mie=Reel, …» → {0: formato}"""
    dias = {"lun": 0, "mar": 1, "mie": 2, "mié": 2, "jue": 3, "vie": 4, "sab": 5, "sáb": 5, "dom": 6}
    out = defaultdict(list)
    for par in db.lista(aj.get("marketing_ritmo", "")):
        if "=" in par:
            d, f = [x.strip() for x in par.split("=", 1)]
            if d[:3].lower() in dias:
                out[dias[d[:3].lower()]].append(f)
    return out


def _canal(formato):
    return {"Reel": "Reel", "Historias": "Historias", "Meta Ads": "Meta Ads", "Correo": "Correo", "Correo mayoristas": "Correo",
            "WhatsApp mayoristas": "WhatsApp", "Banner web": "Web"}.get(formato, "Instagram")


def _publico(formato):
    return "Mayoristas" if "mayorista" in cb.norm(formato) else ("Clientes web" if formato in ("Correo", "Banner web") else "Todos")


def generar_mes(s, anio, mes, aj, reemplazar=False):
    """Arma la grilla del mes: ritmo semanal + campañas por evento. Rota todas las familias por igual."""
    ini = date(anio, mes, 1)
    fin = date(anio, mes, calendar.monthrange(anio, mes)[1])
    if reemplazar:
        for p in s.scalars(select(Pieza).where(Pieza.fecha >= ini, Pieza.fecha <= fin, Pieza.generada.is_(True),
                                               Pieza.estado == "idea")):
            s.delete(p)
    import productos as pr
    matriz = sorted({p for (p,) in s.execute(select(pr.Producto.producto).where(pr.Producto.producto.isnot(None))) if p})
    fams = ([x.strip() for x in db.lista(aj.get("marketing_productos", ""))] or matriz or familias(s) or ["Lana"])
    # rotación pareja: continúa donde quedó el mes anterior
    usados = Counter(p.producto for p in s.scalars(select(Pieza).where(Pieza.producto.isnot(None))))
    orden = sorted(fams, key=lambda f: (usados.get(f, 0), fams.index(f)))
    i = 0
    eventos = eventos_entre(s, ini, fin + timedelta(days=10))
    nuevas = 0
    for e in eventos_entre(s, ini + timedelta(days=30), fin + timedelta(days=35)):
        clave = cb.norm(e.nombre)
        if e.tipo in ("comercial", "temporada") and not any(x in clave for x in ("halloween", "verano", "primavera", "black", "cyber")):
            f = e.fecha - timedelta(days=30)
            if ini <= f <= fin:
                for formato, dd in (("WhatsApp mayoristas", 0), ("Correo mayoristas", 1)):
                    p = _pieza(f + timedelta(days=dd), formato, orden[i % len(orden)], f"prepara tu stock para {e.nombre}", e.nombre)
                    p.titulo = f"Mayoristas: stock para {e.nombre}"
                    s.add(p)
                    nuevas += 1
                i += 1
    # novedades mensuales a mayoristas (primer jueves)
    jueves = ini + timedelta(days=(3 - ini.weekday()) % 7)
    p = _pieza(jueves, "Correo mayoristas", orden[i % len(orden)], "novedades y stock del mes", "Mayoristas")
    p.titulo = f"Correo mayoristas – novedades de {MESES[mes - 1]}"
    s.add(p)
    nuevas += 1
    ev_dia = {}
    for e in eventos:
        if e.tipo != "feriado":
            ev_dia.setdefault(e.fecha, e)
    r = ritmo(aj)
    d = ini
    while d <= fin:
        for formato in r.get(d.weekday(), []):
            producto = orden[i % len(orden)]
            i += 1
            proximo = next((e for e in eventos if e.tipo in ("comercial", "temporada") and 0 <= (e.fecha - d).days <= 10), None)
            gancho = (f"especial {proximo.nombre}" if proximo else "colores de temporada")
            campana = proximo.nombre if proximo else ""
            s.add(_pieza(d, formato, producto, gancho, campana))
            nuevas += 1
        d += timedelta(days=1)
    # publicidad siempre activa del mes (remarketing)
    lunes = ini + timedelta(days=(7 - ini.weekday()) % 7)
    p = _pieza(lunes, "Meta Ads", "Lo más vendido del mes", "remarketing del mes", "Siempre activo")
    p.titulo = f"Meta Ads siempre activo – {MESES[mes - 1]}"
    s.add(p)
    nuevas += 1
    # campañas por evento: aviso 7 días antes, día de lanzamiento, último día
    for e in eventos:
        if e.tipo != "comercial" or not (ini <= e.fecha <= fin):
            continue
        prod = orden[i % len(orden)]
        i += 1
        plan = [(e.fecha - timedelta(days=7), "Historias", f"Se viene {e.nombre}: cuenta regresiva"),
                (e.fecha - timedelta(days=5), "Correo", f"{e.nombre}: ideas y regalo para clientas"),
                (e.fecha - timedelta(days=5), "Meta Ads", f"Campaña {e.nombre}"),
                (e.fecha - timedelta(days=1) if not e.hasta else e.fecha, "Reel", f"{e.nombre}: idea para tejer"),
                ((e.hasta or e.fecha), "Historias", f"Último día {e.nombre}")]
        for f, formato, titulo in plan:
            if ini <= f <= fin:
                p = _pieza(f, formato, prod, e.nombre.lower(), e.nombre)
                p.titulo = titulo
                p.copy = (p.copy or "") + (f"\n\nIdea: {e.nota}" if e.nota else "")
                s.add(p)
                nuevas += 1
    return nuevas


def _pieza(d, formato, producto, gancho, campana):
    ctx = {"producto": producto, "gancho": gancho, "uso": _uso(producto), "cta_link": ""}
    base = formato if formato in GUIAS else ("Carrusel de producto" if "carrusel" in cb.norm(formato) else formato)
    return Pieza(fecha=d, canal=_canal(base), publico=_publico(base), formato=formato, producto=producto, campana=campana,
                 titulo=f"{formato}: {producto}", copy=COPIES.get(base, "{producto}").format(**ctx),
                 guia=GUIAS.get(base, "").format(**ctx), cta="topwool.cl", estado="idea", generada=True)


def utm(url, canal, campana):
    import re
    from urllib.parse import quote
    fuente = {"Instagram": "instagram", "Reel": "instagram", "Historias": "instagram", "Facebook": "facebook",
              "Meta Ads": "meta", "Correo": "correo", "WhatsApp": "whatsapp", "TikTok": "tiktok", "Web": "web"}.get(canal, "otro")
    medio = "paid" if canal == "Meta Ads" else ("email" if canal == "Correo" else "social")
    camp = re.sub(r"[^a-z0-9]+", "-", cb.norm(campana or "siempre")).strip("-") or "siempre"
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}utm_source={fuente}&utm_medium={medio}&utm_campaign={quote(camp)}"


def armar_automatico(s, aj, hoy=None):
    """Arma sola la grilla del mes actual (si está vacía) y la del mes siguiente desde el día 20."""
    hoy = hoy or date.today()
    if not aj.get("marketing_auto"):
        return []
    sig = (hoy.replace(day=28) + timedelta(days=5)).replace(day=1)
    meses = [(hoy.year, hoy.month)] + ([(sig.year, sig.month)] if hoy.day >= 20 else [])
    hechos = []
    for y, m in meses:
        ini = date(y, m, 1)
        fin = date(y, m, calendar.monthrange(y, m)[1])
        hay = s.scalar(select(func.count()).select_from(Pieza).where(Pieza.fecha >= ini, Pieza.fecha <= fin))
        if not hay:
            cargar_eventos(s, [y, y + 1])
            s.flush()
            generar_mes(s, y, m, aj)
            hechos.append(f"{MESES[m - 1]} {y}")
    return hechos


# ---------------- simulador de envío gratis ----------------
def envio_gratis(s, margen=0.5, costo_pct=1.0, empuje=0.3, ventana=0.35, meses=12, hoy=None, nuevos=0.0):
    """Simula umbrales de envío gratis con los pedidos web reales.
    margen: margen bruto sobre la venta (0,5 = 50 %). costo_pct: costo real del despacho / lo que se cobra.
    empuje: fracción de pedidos que estaban hasta un `ventana` bajo el umbral y que agregan productos para alcanzarlo.
    No cuenta el aumento de pedidos nuevos que trae el envío gratis (cálculo conservador)."""
    hoy = hoy or date.today()
    desde = hoy - timedelta(days=30 * meses)
    peds = [p for p in _ventas(s) if p.fecha >= desde]
    con_envio = [p for p in peds if (p.envio or 0) > 0]
    if not con_envio:
        return None
    n_meses = max(1, min(meses, ((hoy - min(p.fecha for p in peds)).days or 1) / 30))
    sub = lambda p: (p.total or 0) - (p.envio or 0)   # noqa: E731
    filas = []
    for T in range(15000, 120001, 5000):
        ya = [p for p in con_envio if sub(p) >= T]
        cerca = [p for p in con_envio if T * (1 - ventana) <= sub(p) < T]
        costo_ya = sum(p.envio * costo_pct for p in ya)
        extra_venta = sum(T - sub(p) for p in cerca) * empuje
        costo_cerca = sum(p.envio * costo_pct for p in cerca) * empuje
        # más pedidos nuevos gracias al envío gratis (opcional): un % extra de los que superan el monto
        n_nuevos = len(ya) * nuevos
        ticket_ya = (sum(sub(p) for p in ya) / len(ya)) if ya else 0
        venta_nuevos = n_nuevos * ticket_ya
        costo_nuevos = n_nuevos * (sum(p.envio for p in ya) / len(ya) if ya else 0) * costo_pct
        extra_venta += venta_nuevos
        neto = extra_venta * margen - costo_ya - costo_cerca - costo_nuevos
        filas.append({"umbral": T, "pct_ya": 100 * len(ya) / len(con_envio), "n_cerca": len(cerca) / n_meses,
                      "extra_venta": extra_venta / n_meses, "costo": (costo_ya + costo_cerca + costo_nuevos) / n_meses,
                      "neto": neto / n_meses})
    subs = sorted(sub(p) for p in con_envio)
    mediana = subs[len(subs) // 2]
    prom_envio = sum(p.envio for p in con_envio) / len(con_envio)
    por_region = defaultdict(list)
    for p in con_envio:
        por_region[p.region or "Sin región"].append(p)
    regiones = sorted(((r, len(v) / n_meses, sum(x.envio for x in v) / len(v), sum(sub(x) for x in v) / len(v))
                       for r, v in por_region.items()), key=lambda x: -x[1])
    tramos = Counter(min(int(sub(p) // 10000), 12) for p in con_envio)
    # recomendado: el mejor resultado entre los montos que alcanza entre el 15 % y el 40 % de los pedidos
    # (más bajo regala despachos a quien igual compraba; más alto casi nadie lo alcanza y no mueve ventas)
    banda = [f for f in filas if 15 <= f["pct_ya"] <= 40] or filas
    mejor = max(banda, key=lambda f: f["neto"])
    return {"filas": filas, "mejor": mejor, "n": len(con_envio) / n_meses, "pct_con_envio": 100 * len(con_envio) / len(peds),
            "ticket": sum(subs) / len(subs), "mediana": mediana, "prom_envio": prom_envio, "regiones": regiones,
            "tramos": [(i, tramos.get(i, 0)) for i in range(13)], "meses": round(n_meses, 1),
            "equilibrio": prom_envio * costo_pct / margen}
