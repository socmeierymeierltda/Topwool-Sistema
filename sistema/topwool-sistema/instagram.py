"""Conexión con Instagram (API oficial de Meta, «Instagram API with Instagram Login»):
publica las piezas de la grilla (foto, carrusel, reel, historia) y trae los resultados de cada publicación.
El token va en la variable IG_TOKEN de Render; el sistema lo renueva solo antes de que venza (dura 60 días)."""
import hashlib
import io
import json
import secrets
import threading
import time
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import Column, DateTime, Integer, LargeBinary, String, Text, select
from sqlalchemy.orm import deferred

import config
import db

API = "https://graph.instagram.com/v26.0/"
CHILE = ZoneInfo("America/Santiago")
ESTADO = {"sincronizando": False}


class IgArchivo(db.Base):
    """Fotos y videos subidos para una pieza (se sirven en una URL pública para que Instagram los descargue)."""
    __tablename__ = "ig_archivos"
    id = Column(Integer, primary_key=True)
    pieza_id = Column(Integer, index=True)
    orden = Column(Integer, default=0)
    nombre = Column(String(200))
    tipo = Column(String(60))          # image/jpeg | video/mp4
    clave = Column(String(40), unique=True, index=True)
    tamano = Column(Integer)
    datos = deferred(Column(LargeBinary))
    creado = Column(DateTime, default=datetime.now)


class IgMedia(db.Base):
    """Publicaciones de la cuenta (hechas desde el sistema o desde el teléfono) con sus resultados."""
    __tablename__ = "ig_media"
    id = Column(String(40), primary_key=True)
    fecha = Column(DateTime, index=True)
    tipo = Column(String(30))          # IMAGE | CAROUSEL_ALBUM | VIDEO
    producto = Column(String(20))      # FEED | REELS | STORY
    caption = Column(Text)
    permalink = Column(String(300))
    miniatura = Column(Text)
    metricas = Column(Text)            # json
    pieza_id = Column(Integer, index=True)
    actualizado = Column(DateTime)


# ---------------- token ----------------
def _huella(t):
    return hashlib.sha256((t or "").encode()).hexdigest()[:16]


def token():
    """El token renovado (guardado en la base) o, si cambiaron el de Render, el nuevo de Render."""
    aj = db.ajustes()
    env = config.IG_TOKEN
    if env and aj.get("ig_token_origen") != _huella(env):
        return env
    return aj.get("ig_token") or env


def conectado():
    return bool(token())


def _get(ruta, **params):
    with httpx.Client(timeout=40) as c:
        r = c.get(API + ruta, params={**params, "access_token": token()})
    datos = r.json() if r.headers.get("content-type", "").startswith(("application/json", "text/javascript")) else {}
    if r.status_code >= 400:
        raise RuntimeError((datos.get("error") or {}).get("message") or r.text[:200])
    return datos


def _post(ruta, **params):
    with httpx.Client(timeout=60) as c:
        r = c.post(API + ruta, data={**params, "access_token": token()})
    datos = r.json() if r.content else {}
    if r.status_code >= 400:
        raise RuntimeError((datos.get("error") or {}).get("message") or r.text[:200])
    return datos


def renovar_token(forzar=False):
    """Renueva el token de larga duración (60 días). Se hace sola una vez por semana."""
    aj = db.ajustes()
    t = token()
    if not t:
        return False
    ult = aj.get("ig_token_fecha") or ""
    if not forzar and ult and datetime.fromisoformat(ult) > datetime.now() - timedelta(days=7) \
            and aj.get("ig_token_origen") == _huella(config.IG_TOKEN):
        return False
    with httpx.Client(timeout=30) as c:
        r = c.get("https://graph.instagram.com/refresh_access_token",
                  params={"grant_type": "ig_refresh_token", "access_token": t})
    if r.status_code >= 400:
        raise RuntimeError((r.json().get("error") or {}).get("message", r.text[:200]))
    nuevo = r.json().get("access_token")
    if nuevo:
        db.guardar_ajustes({"ig_token": nuevo, "ig_token_origen": _huella(config.IG_TOKEN),
                            "ig_token_fecha": datetime.now().isoformat(timespec="minutes"),
                            "ig_token_vence": (datetime.now() + timedelta(seconds=int(r.json().get("expires_in", 5184000))))
                            .date().isoformat()})
    return True


def cuenta():
    d = _get("me", fields="user_id,username,name,followers_count,media_count,profile_picture_url")
    db.guardar_ajustes({"ig_usuario": d.get("username", ""), "ig_user_id": str(d.get("user_id") or d.get("id") or "")})
    return d


def _user_id():
    uid = db.ajustes().get("ig_user_id")
    return uid or str(cuenta().get("user_id"))


# ---------------- archivos ----------------
def preparar_archivo(contenido: bytes, nombre: str, mime: str):
    """Fotos → JPEG (Instagram solo acepta JPEG), máx. 1440 px. Videos se guardan tal cual (MP4)."""
    if (mime or "").startswith("video") or nombre.lower().endswith((".mp4", ".mov")):
        return contenido, "video/mp4", nombre
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(contenido))).convert("RGB")
    if max(im.size) > 1440:
        im.thumbnail((1440, 1440))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue(), "image/jpeg", nombre.rsplit(".", 1)[0] + ".jpg"


def guardar_archivo(s, pieza_id, contenido, nombre, mime):
    datos, tipo, nombre = preparar_archivo(contenido, nombre, mime)
    n = len(list(s.scalars(select(IgArchivo.id).where(IgArchivo.pieza_id == pieza_id))))
    s.add(IgArchivo(pieza_id=pieza_id, orden=n, nombre=nombre[:200], tipo=tipo, clave=secrets.token_urlsafe(24),
                    tamano=len(datos), datos=datos))


def url_publica(base, a):
    ext = "mp4" if a.tipo.startswith("video") else "jpg"
    return f"{base.rstrip('/')}/media/{a.clave}.{ext}"


# ---------------- publicar ----------------
def _esperar(contenedor, maximo=600):
    """Los videos tardan en procesarse: esperar a que el contenedor quede FINISHED."""
    t0 = time.time()
    while time.time() - t0 < maximo:
        st = _get(contenedor, fields="status_code,status").get("status_code")
        if st == "FINISHED":
            return
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError("Instagram no pudo procesar el archivo (revisa formato: MP4 H.264, 3 a 90 s, vertical 9:16).")
        time.sleep(8)
    raise RuntimeError("Instagram tardó demasiado en procesar el video; vuelve a intentar.")


def tipo_publicacion(pieza, archivos):
    canal = (pieza.canal or "") + " " + (pieza.formato or "")
    videos = [a for a in archivos if a.tipo.startswith("video")]
    if "Historia" in canal:
        return "STORIES"
    if "Reel" in canal or (len(archivos) == 1 and videos):
        return "REELS"
    return "CAROUSEL" if len(archivos) > 1 else "IMAGE"


def publicar(pieza, archivos, base):
    """Publica en Instagram. Devuelve (media_id, permalink)."""
    if not archivos:
        raise RuntimeError("La pieza no tiene fotos ni videos subidos.")
    uid = _user_id()
    caption = (pieza.copy or "").strip()[:2200]
    tipo = tipo_publicacion(pieza, archivos)
    if tipo == "STORIES":
        ultimo = None
        for a in archivos[:10]:
            campo = {"video_url": url_publica(base, a)} if a.tipo.startswith("video") else {"image_url": url_publica(base, a)}
            c = _post(f"{uid}/media", media_type="STORIES", **campo)["id"]
            _esperar(c)
            ultimo = _post(f"{uid}/media_publish", creation_id=c)["id"]
        mid = ultimo
    elif tipo == "REELS":
        v = next((a for a in archivos if a.tipo.startswith("video")), None)
        if not v:
            raise RuntimeError("Para un reel sube un video MP4.")
        c = _post(f"{uid}/media", media_type="REELS", video_url=url_publica(base, v), caption=caption, share_to_feed="true")["id"]
        _esperar(c)
        mid = _post(f"{uid}/media_publish", creation_id=c)["id"]
    elif tipo == "CAROUSEL":
        hijos = []
        for a in archivos[:20]:
            if a.tipo.startswith("video"):
                h = _post(f"{uid}/media", media_type="VIDEO", video_url=url_publica(base, a), is_carousel_item="true")["id"]
            else:
                h = _post(f"{uid}/media", image_url=url_publica(base, a), is_carousel_item="true")["id"]
            _esperar(h)
            hijos.append(h)
        c = _post(f"{uid}/media", media_type="CAROUSEL", children=",".join(hijos), caption=caption)["id"]
        _esperar(c)
        mid = _post(f"{uid}/media_publish", creation_id=c)["id"]
    else:
        c = _post(f"{uid}/media", image_url=url_publica(base, archivos[0]), caption=caption)["id"]
        _esperar(c)
        mid = _post(f"{uid}/media_publish", creation_id=c)["id"]
    link = ""
    try:
        link = _get(mid, fields="permalink").get("permalink", "")
    except Exception:  # noqa: BLE001
        pass
    return mid, link


def publicar_pieza(pid, base):
    """Publica una pieza y deja el resultado guardado en ella (se llama en segundo plano)."""
    import marketing as mk
    with db.Session() as s:
        p = s.get(mk.Pieza, pid)
        archivos = list(s.scalars(select(IgArchivo).where(IgArchivo.pieza_id == pid).order_by(IgArchivo.orden, IgArchivo.id)))
        p.ig_error = ""
        p.estado = "publicando"
        s.commit()
        try:
            mid, link = publicar(p, archivos, base)
            p.ig_id, p.ig_link, p.ig_publicada, p.estado = mid, link, datetime.now(), "publicado"
            p.ig_auto = False
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            p.ig_error = str(e)[:500]
            p.estado = "listo"
            p.ig_auto = False
        s.commit()


def hora_pieza(p):
    h, m = 10, 0
    try:
        h, m = map(int, (p.hora or "10:00").split(":"))
    except ValueError:
        pass
    return datetime(p.fecha.year, p.fecha.month, p.fecha.day, h, m, tzinfo=CHILE)


def pendientes(s):
    import marketing as mk
    ahora = datetime.now(CHILE)
    return [p for p in s.scalars(select(mk.Pieza).where(mk.Pieza.ig_auto.is_(True), mk.Pieza.estado == "programado"))
            if hora_pieza(p) <= ahora]


# ---------------- resultados ----------------
METRICAS = ["views", "reach", "likes", "comments", "saved", "shares", "total_interactions"]


def _insights(mid, producto):
    for lista in (METRICAS, ["reach", "likes", "comments", "saved", "shares"], ["reach"]):
        try:
            d = _get(f"{mid}/insights", metric=",".join(lista))
            return {x["name"]: (x.get("values") or [{}])[0].get("value", x.get("total_value", {}).get("value"))
                    for x in d.get("data", [])}
        except RuntimeError:
            continue
    return {}


def sincronizar(n=60):
    """Trae las últimas publicaciones con sus resultados y las une con las piezas de la grilla."""
    import marketing as mk
    uid = _user_id()
    d = _get(f"{uid}/media", fields="id,caption,media_type,media_product_type,permalink,thumbnail_url,media_url,timestamp,"
                                    "like_count,comments_count", limit=min(n, 100))
    nuevos = 0
    with db.Session() as s:
        for x in d.get("data", []):
            fecha = datetime.fromisoformat(x["timestamp"].replace("+0000", "+00:00")).astimezone(CHILE).replace(tzinfo=None)
            met = {"likes": x.get("like_count"), "comments": x.get("comments_count")}
            if fecha > datetime.now() - timedelta(days=90):
                met.update({k: v for k, v in _insights(x["id"], x.get("media_product_type")).items() if v is not None})
            m = s.get(IgMedia, x["id"])
            if not m:
                m = IgMedia(id=x["id"])
                s.add(m)
                nuevos += 1
            m.fecha, m.tipo, m.producto = fecha, x.get("media_type"), x.get("media_product_type")
            m.caption, m.permalink = (x.get("caption") or "")[:5000], x.get("permalink")
            m.miniatura = x.get("thumbnail_url") or x.get("media_url")
            m.metricas, m.actualizado = json.dumps(met), datetime.now()
            # unir con la pieza: la publicada desde el sistema, o la del mismo día y formato
            p = s.scalars(select(mk.Pieza).where(mk.Pieza.ig_id == x["id"])).first()
            if not p:
                cands = [q for q in s.scalars(select(mk.Pieza).where(mk.Pieza.fecha == fecha.date(), mk.Pieza.ig_id.is_(None)))
                         if (q.canal == "Reel") == (x.get("media_product_type") == "REELS") and q.canal in ("Instagram", "Reel")]
                p = cands[0] if len(cands) == 1 else None
                if p:
                    p.ig_id, p.ig_link = x["id"], x.get("permalink")
                    if p.estado != "publicado":
                        p.estado = "publicado"
            if p:
                m.pieza_id = p.id
                p.ig_metricas = m.metricas
        s.commit()
    db.guardar_ajustes({"ig_ultima_sync": datetime.now().isoformat(timespec="minutes")})
    return nuevos, len(d.get("data", []))


def resumen(medias):
    """Qué funciona mejor: promedio de interacción por formato y por día."""
    from collections import defaultdict
    por_tipo, por_dia = defaultdict(list), defaultdict(list)
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    for m in medias:
        met = json.loads(m.metricas or "{}")
        inter = (met.get("likes") or 0) + (met.get("comments") or 0) + 2 * (met.get("saved") or 0) + 2 * (met.get("shares") or 0)
        alc = met.get("reach") or 0
        nombre = {"REELS": "Reels", "FEED": {"CAROUSEL_ALBUM": "Carruseles", "IMAGE": "Fotos", "VIDEO": "Videos"}.get(m.tipo, "Otros")
                  }.get(m.producto, "Otros")
        if isinstance(nombre, dict):
            nombre = "Otros"
        por_tipo[nombre].append((inter, alc))
        por_dia[dias[m.fecha.weekday()]].append((inter, alc))

    def prom(v):
        return {"n": len(v), "inter": sum(x[0] for x in v) / len(v), "alcance": sum(x[1] for x in v) / len(v)}
    return ({k: prom(v) for k, v in sorted(por_tipo.items(), key=lambda kv: -prom(kv[1])["inter"])},
            {k: prom(v) for k, v in sorted(por_dia.items(), key=lambda kv: -prom(kv[1])["inter"])})


# ---------------- tareas automáticas ----------------
def iniciar_automatico(base_url_fn):
    """Cada 2 minutos publica lo programado; cada 6 horas trae resultados; una vez por semana renueva el token."""
    def bucle():
        time.sleep(60)
        ultima_sync = 0
        while True:
            try:
                if conectado():
                    with db.Session() as s:
                        ids = [p.id for p in pendientes(s)]
                    for pid in ids:
                        publicar_pieza(pid, base_url_fn())
                    if time.time() - ultima_sync > 6 * 3600:
                        ultima_sync = time.time()
                        renovar_token()
                        sincronizar()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(120)
    threading.Thread(target=bucle, daemon=True).start()
