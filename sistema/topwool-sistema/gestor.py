"""Gestor web: base de clientes de la tienda (compras, montos, periodicidad), segmentos
y mini campañas por correo con cupón opcional de WooCommerce."""
import hashlib
import hmac
import json
import re
import smtplib
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from email.message import EmailMessage

from sqlalchemy import Column, Date, DateTime, Integer, String, Text, select

import cobranza as cb
import config
import db
import informes as inf

SEGMENTOS = {
    "mejores": "Mejores clientes",
    "en_riesgo": "Por dormirse",
    "dormidos": "Dormidos",
    "repiten": "Repiten el mismo producto",
    "nuevos": "Nuevos (1 compra reciente)",
    "contactos": "Lista subida (mayoristas, etc.)",
    "todos": "Todos los clientes web",
}

PLANTILLAS = {
    "mejores": ("🎁 {nombre}, un regalo por ser de las mejores clientas de Topwool",
                "Hola {nombre}:\n\nQueríamos darte las gracias: con {compras} compras eres parte de las personas que más "
                "tejen con Topwool. Por eso te regalamos un {descuento} de descuento en tu próxima compra en topwool.cl.\n\n"
                "Tu código: {cupon}  (válido hasta el {vence}).\n\n¡Gracias por elegir lana chilena!\nEquipo Topwool"),
    "en_riesgo": ("{nombre}, ¿se te está acabando la lana? 🧶",
                  "Hola {nombre}:\n\nSueles comprar cada {cada} días y ya van {dias} desde tu último pedido. "
                  "Si estás por empezar un proyecto nuevo, tenemos tus favoritos listos: {producto}.\n\n"
                  "Con el código {cupon} tienes {descuento} de descuento hasta el {vence}.\n\nEquipo Topwool\nwww.topwool.cl"),
    "dormidos": ("Te echamos de menos en Topwool, {nombre}",
                 "Hola {nombre}:\n\nHace tiempo que no nos visitas (tu última compra fue el {ultima}). "
                 "Hemos sumado colores y productos nuevos que te pueden gustar.\n\n"
                 "Para volver a tejer juntas te regalamos {descuento} de descuento con el código {cupon} (hasta el {vence}).\n\n"
                 "Un abrazo,\nEquipo Topwool\nwww.topwool.cl"),
    "repiten": ("{nombre}, tu {producto} favorito te espera",
                "Hola {nombre}:\n\nVimos que {producto} es de tus favoritos: lo has comprado {veces} veces. "
                "Para que no te quedes sin él, esta semana tienes {descuento} de descuento con el código {cupon} "
                "(hasta el {vence}).\n\nEquipo Topwool\nwww.topwool.cl"),
    "nuevos": ("¡Gracias por tu primera compra, {nombre}!",
               "Hola {nombre}:\n\nGracias por estrenarte en Topwool. ¿Cómo te fue con tu lana? Nos encantaría ver lo que tejiste.\n\n"
               "Para tu segunda compra te dejamos {descuento} de descuento con el código {cupon} (hasta el {vence}).\n\n"
               "Equipo Topwool\nwww.topwool.cl"),
    "contactos": ("Cotiza con Topwool: lana chilena para tu proyecto",
                  "Hola {nombre}:\n\nTe escribimos de Topwool (Curacautín). Si estás postulando a un fondo o preparando un proyecto "
                  "textil, te preparamos una cotización formal de lana, hilados y vellón, con factura.\n\n"
                  "Responde este correo con lo que necesitas y te la enviamos en el día.\n\nEquipo Topwool\nwww.topwool.cl"),
    "todos": ("Novedades de Topwool",
              "Hola {nombre}:\n\n…\n\nEquipo Topwool\nwww.topwool.cl"),
}


# Mensajes que sirven para cualquier grupo (se eligen en «Tipo de mensaje»)
MENSAJES = {
    "stock": ("Volvió {producto} a Topwool 🧶",
              "Hola {nombre}:\n\nTe avisamos porque ya compraste {producto}: ¡volvió a estar disponible en topwool.cl!\n\n"
              "Las reposiciones se agotan rápido, así que si lo necesitas para tu próximo proyecto, este es el momento.\n\n"
              "Equipo Topwool\nwww.topwool.cl"),
    "nuevo": ("Nuevo en Topwool: …",
              "Hola {nombre}:\n\nComo te gustan nuestras lanas, quisimos contarte primero: llegó … a topwool.cl.\n\n"
              "…\n\nEquipo Topwool\nwww.topwool.cl"),
    "promo": ("{nombre}, esta semana {descuento} en Topwool",
              "Hola {nombre}:\n\nHasta el {vence} tienes {descuento} de descuento en topwool.cl con el código {cupon}.\n\n"
              "Equipo Topwool\nwww.topwool.cl"),
}
MENSAJES_NOMBRE = {"": "Según el grupo", "stock": "Aviso de llegada de stock", "nuevo": "Producto nuevo", "promo": "Promoción"}


class NoContactar(db.Base):
    __tablename__ = "gestor_no_contactar"
    email = Column(String(200), primary_key=True)
    fecha = Column(DateTime, default=datetime.now)
    motivo = Column(String(120))


class Contacto(db.Base):
    """Contactos que no vienen de la web (mayoristas, artesanas, agrupaciones), subidos desde Excel."""
    __tablename__ = "gestor_contactos"
    email = Column(String(200), primary_key=True)
    nombre = Column(String(200))
    tipo = Column(String(60))
    telefono = Column(String(40))
    comuna = Column(String(120))


class Campana(db.Base):
    __tablename__ = "gestor_campanas"
    id = Column(Integer, primary_key=True)
    fecha = Column(DateTime, default=datetime.now)
    nombre = Column(String(150))
    segmento = Column(String(40))
    filtro = Column(String(150))
    asunto = Column(String(250))
    cuerpo = Column(Text)
    cupon = Column(String(60))
    descuento = Column(String(20))
    vence = Column(Date)
    destinatarios = Column(Integer, default=0)
    enviados = Column(Integer, default=0)
    errores = Column(Integer, default=0)
    estado = Column(String(20), default="borrador")   # enviando | enviada | error
    nota = Column(Text)


class CampanaEnvio(db.Base):
    __tablename__ = "gestor_envios"
    id = Column(Integer, primary_key=True)
    campana_id = Column(Integer, index=True)
    email = Column(String(200), index=True)
    fecha = Column(DateTime, default=datetime.now)
    ok = Column(Integer, default=1)
    error = Column(String(250))


# ---------------- base de clientes ----------------
def _primer_nombre(n):
    n = (n or "").strip()
    return n.split()[0].title() if n else "hola"


def clientes(s, hoy: date | None = None, dias_dormido=180):
    """Una fila por cliente web (por correo) con su historial de compras."""
    hoy = hoy or date.today()
    por = defaultdict(list)
    for p in s.scalars(select(inf.PedidoCache).where(inf.PedidoCache.estado.in_(inf.VENTA))):
        k = (p.email or "").strip().lower() or "sin-correo:" + cb.norm(p.nombre)
        por[k].append(p)
    no = {e for (e,) in s.execute(select(NoContactar.email))}
    hace_un_anio = hoy - timedelta(days=365)
    out = []
    for k, ps in por.items():
        ps.sort(key=lambda p: p.fecha)
        fechas = sorted({p.fecha for p in ps})
        prod = Counter()
        for p in ps:
            for nombre in {i[0] for i in json.loads(p.items or "[]") if "envio" not in cb.norm(i[0] or "")}:
                prod[nombre] += 1     # en cuántos pedidos distintos aparece
        repetidos = [(n, c) for n, c in prod.most_common() if c >= 2]
        gaps = [(b - a).days for a, b in zip(fechas, fechas[1:])]
        cada = round(sum(gaps) / len(gaps)) if gaps else None
        ult = ps[-1]
        dias = (hoy - fechas[-1]).days
        total = sum(p.total for p in ps)
        out.append({
            "clave": k, "email": "" if k.startswith("sin-correo:") else k, "nombre": ult.nombre or k,
            "ciudad": ult.ciudad, "region": ult.region, "compras": len(ps), "total": total,
            "ticket": total / len(ps), "primera": fechas[0], "ultima": fechas[-1], "dias": dias, "cada": cada,
            "ultimo_anio": sum(p.total for p in ps if p.fecha >= hace_un_anio),
            "favorito": (repetidos or prod.most_common(1) or [("", 0)])[0][0],
            "veces": (repetidos or prod.most_common(1) or [("", 0)])[0][1],
            "repetidos": repetidos[:5], "productos": [n for n, _ in prod.most_common()], "familias": Counter(inf.familia(n) for n in prod).most_common(3),
            "pedidos": [(p.fecha, p.numero, p.total) for p in reversed(ps)],
            "no_contactar": k in no,
            "dormido": dias > dias_dormido,
            "en_riesgo": bool(cada) and len(ps) >= 2 and dias > max(30, cada * 1.5) and dias <= dias_dormido,
        })
    # mejores: el 10 % que más compró en los últimos 12 meses (mínimo 2 compras en total)
    activos = sorted((c for c in out if c["ultimo_anio"] > 0 and c["compras"] >= 2), key=lambda c: -c["ultimo_anio"])
    corte = max(10, len(activos) // 10)
    for i, c in enumerate(activos):
        c["mejor"] = i < corte
    for c in out:
        c.setdefault("mejor", False)
        c["nuevo"] = c["compras"] == 1 and c["dias"] <= 60
    out.sort(key=lambda c: -c["total"])
    return out


def contactos(s):
    no = {e for (e,) in s.execute(select(NoContactar.email))}
    return [{"clave": c.email, "email": "" if c.email.startswith("tel:") else c.email,
             "nombre": c.nombre or c.telefono or c.email, "ciudad": c.comuna, "tipo": c.tipo,
             "telefono": c.telefono,
             "compras": 0, "total": 0, "favorito": "", "veces": 0, "dias": None, "cada": None, "ultima": None,
             "no_contactar": c.email in no} for c in s.scalars(select(Contacto).order_by(Contacto.nombre))]


def segmento(lista, seg, filtro=""):
    palabras = cb.norm(filtro).split()

    def calza(t):   # todas las palabras del filtro, en cualquier orden ("malal rojo" ↔ "Malal Teñido - Rojo")
        t = cb.norm(t)
        return bool(palabras) and all(w in t for w in palabras)
    pasa = {
        "mejores": lambda c: c["mejor"], "dormidos": lambda c: c["dormido"], "en_riesgo": lambda c: c["en_riesgo"],
        "repiten": lambda c: bool(c.get("repetidos")), "nuevos": lambda c: c.get("nuevo"),
    }.get(seg, lambda c: True)
    out = [c for c in lista if pasa(c)]
    if palabras:   # clientes que compraron un producto con esas palabras (o de ese tipo/ciudad)
        out = [c for c in out if any(calza(n) for n in c.get("productos") or [])
               or calza(c.get("tipo") or "") or calza(c.get("ciudad") or "")]
        nuevos = []
        for c in out:   # que {producto} y {veces} sean los del producto filtrado
            hit = next(((n, v) for n, v in c.get("repetidos") or [] if calza(n)), None) or \
                next(((n, 1) for n in c.get("productos") or [] if calza(n)), None)
            nuevos.append({**c, "producto_campana": hit[0], "veces": hit[1]} if hit else c)
        out = nuevos
    return out


def resumen(lista):
    return {k: len(segmento(lista, k)) for k in SEGMENTOS if k != "contactos"}


def importar_contactos(s, contenido: bytes, nombre: str, tipo_def: str) -> int:
    from conciliacion import _filas_excel, _txt
    if nombre.lower().endswith(".csv"):
        import csv
        import io
        texto = contenido.decode("utf-8-sig", errors="ignore")
        filas = list(csv.reader(io.StringIO(texto), delimiter=";" if texto.count(";") > texto.count(",") else ","))
    else:
        filas = _filas_excel(contenido, nombre)
    cab_i, cab = 0, []
    for i, r in enumerate(filas[:15]):
        celdas = [cb.norm(_txt(c)) for c in r]
        if any("mail" in c or "correo" in c for c in celdas):
            cab_i, cab = i, celdas
            break
    if not cab:
        for i, r in enumerate(filas[:15]):
            celdas = [cb.norm(_txt(c)) for c in r]
            if any("fono" in c or "telef" in c or "celular" in c or "whatsapp" in c for c in celdas):
                cab_i, cab = i, celdas
                break
    if not cab:
        raise ValueError("No encontré una columna 'correo', 'email' o 'teléfono' en el archivo.")

    def col(*p):
        return next((j for j, c in enumerate(cab) if any(x in c for x in p)), None)
    ie, inn, it, itel, ico = col("mail", "correo"), col("nombre", "cliente", "razon"), col("tipo", "segmento"), col("fono", "telef", "celular", "whatsapp"), col("comuna", "ciudad")
    n = 0
    for r in filas[cab_i + 1:]:
        g = lambda j: _txt(r[j]) if j is not None and j < len(r) else ""   # noqa: E731
        email = g(ie).lower().strip()
        if not re.match(r"[^@\s]+@[^@\s]+\.[a-z]{2,}$", email):
            tel = re.sub(r"\D", "", g(itel))
            if len(tel) < 8:
                continue
            email = "tel:" + tel[-9:]     # contacto solo con WhatsApp
        s.merge(Contacto(email=email[:200], nombre=g(inn)[:200], tipo=(g(it) or tipo_def)[:60],
                         telefono=g(itel)[:40], comuna=g(ico)[:120]))
        n += 1
    return n


# ---------------- correos ----------------
def token_baja(email):
    return hmac.new(config.SECRET_KEY.encode(), email.lower().encode(), hashlib.sha256).hexdigest()[:20]


def link_baja(base_url, email):
    from urllib.parse import quote
    return f"{base_url.rstrip('/')}/baja?e={quote(email)}&t={token_baja(email)}"


def _fmt_fecha(d):
    return d.strftime("%d-%m-%Y") if d else ""


def rellenar(texto, c, camp):
    datos = {
        "nombre": _primer_nombre(c.get("nombre")), "nombre_completo": c.get("nombre") or "",
        "compras": c.get("compras") or "", "total": "$" + f"{c.get('total') or 0:,.0f}".replace(",", "."),
        "producto": c.get("producto_campana") or c.get("favorito") or "tus lanas favoritas",
        "veces": c.get("veces") or "", "dias": c.get("dias") if c.get("dias") is not None else "",
        "cada": c.get("cada") or "", "ultima": _fmt_fecha(c.get("ultima")),
        "cupon": camp.get("cupon") or "", "descuento": camp.get("descuento_txt") or "", "vence": _fmt_fecha(camp.get("vence")),
    }
    return re.sub(r"\{(\w+)\}", lambda m: str(datos.get(m.group(1), m.group(0))), texto or "")


def armar_correo(c, camp, base_url):
    msg = EmailMessage()
    msg["From"] = f"Topwool <{config.SMTP_USER}>"
    msg["To"] = c["email"]
    msg["Subject"] = rellenar(camp["asunto"], c, camp)
    if camp.get("responder_a"):
        msg["Reply-To"] = camp["responder_a"]
    baja = link_baja(base_url, c["email"])
    msg["List-Unsubscribe"] = f"<{baja}>"
    cuerpo = rellenar(camp["cuerpo"], c, camp)
    msg.set_content(cuerpo + f"\n\n—\nSi no quieres recibir más correos de Topwool, haz clic aquí: {baja}")
    return msg


def enviar(destinos, camp, base_url, avance=None, pausa=1.5):
    """Envía con una sola conexión SMTP. Devuelve [(email, ok, error)]."""
    if not (config.SMTP_USER and config.SMTP_PASSWORD):
        raise RuntimeError("falta configurar el correo (SMTP_USER y SMTP_PASSWORD en Render)")
    res = []
    smtp = None
    try:
        for i, c in enumerate(destinos, 1):
            try:
                if smtp is None:
                    from produccion import smtp_abrir
                    smtp = smtp_abrir()
                smtp.send_message(armar_correo(c, camp, base_url))
                res.append((c["email"], True, ""))
            except smtplib.SMTPServerDisconnected as e:
                smtp = None
                res.append((c["email"], False, str(e)[:250]))
            except Exception as e:  # noqa: BLE001
                res.append((c["email"], False, str(e)[:250]))
                if "Daily user sending limit" in str(e) or "5.4.5" in str(e):
                    break   # Gmail llegó a su límite diario: parar
            if avance:
                avance(f"Enviando {i} de {len(destinos)}…")
            time.sleep(pausa)
    finally:
        if smtp:
            try:
                smtp.quit()
            except Exception:  # noqa: BLE001
                pass
    return res


def codigo_cupon(prefijo="TW"):
    import secrets
    return f"{prefijo}{date.today():%m%y}-{secrets.token_hex(2).upper()}"


# ---------------- fondos públicos ----------------
class Fondo(db.Base):
    """Convocatorias de fondos públicos que sirven a clientes (artesanas, agrupaciones, emprendedoras)."""
    __tablename__ = "gestor_fondos"
    id = Column(Integer, primary_key=True)
    nombre = Column(String(250))
    institucion = Column(String(80))
    region = Column(String(80))
    abre = Column(Date)
    cierra = Column(Date, index=True)
    url = Column(String(400))
    nota = Column(Text)
    estado = Column(String(20), default="vigente")   # por_revisar | vigente | descartado
    origen = Column(String(80))
    creado = Column(DateTime, default=datetime.now)


FUENTES = [
    ("Sercotec", "Capital Semilla, Capital Abeja, Crece, Fondo de Desarrollo de Negocios, Ferias Libres", "https://www.sercotec.cl/convocatorias/"),
    ("Corfo", "Semilla, Activa, programas regionales", "https://www.corfo.cl/sites/cpp/convocatorias_y_programas"),
    ("INDAP", "Programas para artesanas/os rurales, PDTI, Prodesal, SAT", "https://www.indap.gob.cl/"),
    ("CONADI", "Fondo de Desarrollo Indígena: emprendimiento y fomento productivo", "https://www.conadi.gob.cl/"),
    ("FOSIS", "Yo Emprendo, Yo Emprendo Semilla", "https://www.fosis.gob.cl/"),
    ("Ministerio de las Culturas", "Fondart / Fondo de Fomento y Desarrollo de la Artesanía", "https://www.fondosdecultura.cl/"),
    ("Gobierno Regional Araucanía", "FNDR 8 % (cultura, social) y fondos regionales", "https://www.gorearaucania.cl/"),
    ("Fondos.gob.cl", "Buscador de todos los fondos del Estado", "https://www.fondos.gob.cl/"),
]
