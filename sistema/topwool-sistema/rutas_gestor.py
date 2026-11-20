"""Pantallas del Gestor web: clientes, campañas, Mercado Público y fondos."""
import csv
import io
import threading
import time
import traceback
from datetime import date, datetime, timedelta

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import select

import cobranza as cb
import config
import db
import gestor as g
import mercadopublico as mp

router = APIRouter()
_ui = {}
TRABAJO = {"estado": "", "mensaje": "", "tipo": ""}


def _render(*a, **k):
    return _ui["render"](*a, **k)


def _volver(*a, **k):
    return _ui["volver"](*a, **k)


def _ocupado():
    return TRABAJO.get("estado") == "procesando"


def _en_fondo(tipo, funcion):
    TRABAJO.update(estado="procesando", mensaje="Empezando…", tipo=tipo, inicio=datetime.now())

    def avance(t):
        TRABAJO["mensaje"] = t

    def correr():
        try:
            TRABAJO.update(estado="listo", mensaje=funcion(avance))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            TRABAJO.update(estado="error", mensaje=f"No se pudo terminar: {str(e)[:250]}")
    threading.Thread(target=correr, daemon=True).start()


def _base_url(request, aj):
    u = (aj.get("gestor_url_publica") or "").strip() or str(request.base_url)
    if u.startswith("http://") and "localhost" not in u and "127.0.0.1" not in u:
        u = "https://" + u[7:]
    return u


def _catalogo():
    import woo
    try:
        return woo.productos_y_categorias()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return [], []


def _lista(s, seg, aj):
    if seg == "contactos":
        return g.contactos(s)
    return g.clientes(s, dias_dormido=int(aj.get("gestor_dias_dormido") or 180))


# ---------------- clientes ----------------
@router.get("/gestor", response_class=HTMLResponse)
def clientes(request: Request, seg: str = "todos", q: str = "", orden: str = "total"):
    import informes as inf
    aj = db.ajustes()
    with db.Session() as s:
        ult = inf.ultima_sync(s)
        todos = g.clientes(s, dias_dormido=int(aj.get("gestor_dias_dormido") or 180))
        cont = g.contactos(s)
        lista = cont if seg == "contactos" else g.segmento(todos, seg)
        n_contactos = len(cont)
        n_no = len(list(s.scalars(select(g.NoContactar))))
    if q:
        ql = cb.norm(q)
        lista = [c for c in lista if ql in cb.norm(" ".join(str(c.get(k) or "") for k in ("nombre", "email", "ciudad", "favorito", "tipo")))]
    claves = {"total": lambda c: -(c["total"] or 0), "compras": lambda c: -(c["compras"] or 0),
              "ultima": lambda c: -(c["ultima"].toordinal() if c.get("ultima") else 0),
              "dias": lambda c: -(c["dias"] or 0)}
    lista.sort(key=claves.get(orden, claves["total"]))
    res = g.resumen(todos)
    res["contactos"] = n_contactos
    return _render(request, "gestor.html", lista=lista[:400], n=len(lista), seg=seg, q=q, orden=orden, res=res,
                   segmentos=g.SEGMENTOS, ult=ult, n_no=n_no, pest="clientes", dd=aj.get("gestor_dias_dormido") or "180",
                   total_clientes=len(todos), con_correo=sum(1 for c in todos if c["email"]))


@router.get("/gestor/exportar")
def exportar(seg: str = "todos", filtro: str = ""):
    aj = db.ajustes()
    with db.Session() as s:
        lista = g.segmento(_lista(s, seg, aj), seg, filtro)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["nombre", "email", "ciudad", "region", "compras", "total", "ticket_promedio", "primera_compra",
                "ultima_compra", "dias_sin_comprar", "compra_cada_dias", "producto_favorito", "veces", "no_contactar"])
    for c in lista:
        w.writerow([c["nombre"], c["email"], c.get("ciudad") or "", c.get("region") or "", c["compras"], round(c["total"] or 0),
                    round(c.get("ticket") or 0), c.get("primera") or "", c.get("ultima") or "", c.get("dias") or "",
                    c.get("cada") or "", c.get("favorito") or "", c.get("veces") or "", "sí" if c["no_contactar"] else ""])
    nombre = f"clientes_{seg}_{date.today():%Y%m%d}.csv"
    return StreamingResponse(io.BytesIO(buf.getvalue().encode("utf-8-sig")), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@router.get("/gestor/cliente", response_class=HTMLResponse)
def cliente(request: Request, k: str):
    with db.Session() as s:
        c = next((x for x in g.clientes(s) if x["clave"] == k), None)
        envios = list(s.execute(select(g.CampanaEnvio, g.Campana).join(g.Campana, g.Campana.id == g.CampanaEnvio.campana_id)
                                .where(g.CampanaEnvio.email == k).order_by(g.CampanaEnvio.fecha.desc())))
    if not c:
        return _volver(request, "/gestor", error="No encontré ese cliente.")
    return _render(request, "gestor_cliente.html", c=c, envios=envios, pest="clientes")


@router.post("/gestor/no-contactar")
def no_contactar(request: Request, email: str = Form(...), volver: str = Form("/gestor"), quitar: str = Form("")):
    with db.Session() as s:
        ya = s.get(g.NoContactar, email.lower())
        if quitar and ya:
            s.delete(ya)
        elif not quitar and not ya:
            s.add(g.NoContactar(email=email.lower(), motivo="marcado a mano"))
        s.commit()
    return _volver(request, volver, aviso="Listo: " + ("se puede volver a contactar." if quitar else "no se le enviarán campañas."))


@router.post("/gestor/contactos")
async def subir_contactos(request: Request, archivo: UploadFile = File(...), tipo: str = Form("mayorista")):
    try:
        with db.Session() as s:
            n = g.importar_contactos(s, await archivo.read(), archivo.filename or "", tipo.strip() or "mayorista")
            s.commit()
    except Exception as e:  # noqa: BLE001
        return _volver(request, "/gestor?seg=contactos", error=f"No pude leer el archivo: {str(e)[:200]}")
    return _volver(request, "/gestor?seg=contactos", aviso=f"{n} contactos guardados (los repetidos se actualizan).")


# ---------------- campañas ----------------
@router.get("/gestor/campana", response_class=HTMLResponse)
def nueva_campana(request: Request, seg: str = "mejores", filtro: str = "", fondo: int = 0, tipo: str = ""):
    aj = db.ajustes()
    asunto, cuerpo = g.MENSAJES.get(tipo) or g.PLANTILLAS.get(seg, g.PLANTILLAS["todos"])
    with db.Session() as s:
        lista = [c for c in g.segmento(_lista(s, seg, aj), seg, filtro) if c["email"] and not c["no_contactar"]]
        f = s.get(g.Fondo, fondo) if fondo else None
        if f:
            asunto = f"Fondo abierto: {f.nombre} — te preparamos la cotización"
            cuerpo = ("Hola {nombre}:\n\nSe abrió la postulación a " + f.nombre +
                      (f" ({f.institucion})" if f.institucion else "") +
                      (f", que cierra el {f.cierra:%d-%m-%Y}" if f.cierra else "") +
                      ".\n\nSi vas a postular con un proyecto de tejido, telar o artesanía, en Topwool te preparamos "
                      "una cotización formal (con factura) de lana, hilados y vellón para adjuntarla a tu postulación.\n\n"
                      "Respóndenos con lo que necesitas y te la enviamos en el día.\n\n"
                      + (f"Más información del fondo: {f.url}\n\n" if f.url else "") + "Equipo Topwool\nwww.topwool.cl")
        campanas = list(s.scalars(select(g.Campana).order_by(g.Campana.fecha.desc()).limit(30)))
        ya_enviados = {e for (e,) in s.execute(select(g.CampanaEnvio.email).where(
            g.CampanaEnvio.fecha >= datetime.now() - timedelta(days=14), g.CampanaEnvio.ok == 1))}
    muestra = lista[:3]
    camp_demo = {"cupon": "TW1126-AB12", "descuento_txt": "10 %", "vence": date.today() + timedelta(days=15)}
    previas = [(c, g.rellenar(asunto, c, camp_demo), g.rellenar(cuerpo, c, camp_demo)) for c in muestra]
    return _render(request, "gestor_campana.html", seg=seg, filtro=filtro, asunto=asunto, cuerpo=cuerpo, n=len(lista),
                   recientes=sum(1 for c in lista if c["email"] in ya_enviados), previas=previas, campanas=campanas,
                   segmentos=g.SEGMENTOS, plantillas=g.PLANTILLAS, aj=aj, fondo=fondo, pest="campanas",
                   tipo=tipo, tipos=g.MENSAJES_NOMBRE, prods=_catalogo()[0], cats=_catalogo()[1],
                   smtp=bool(config.SMTP_USER and config.SMTP_PASSWORD), trabajo=TRABAJO, smtp_user=config.SMTP_USER)


@router.post("/gestor/campana")
def enviar_campana(request: Request, prod: list[int] = Form([]), cat: list[int] = Form([]), donde: str = Form("solo"), seg: str = Form(...), filtro: str = Form(""), nombre: str = Form(""),
                   asunto: str = Form(...), cuerpo: str = Form(...), cupon: str = Form(""), porcentaje: int = Form(10),
                   dias: int = Form(15), prueba: str = Form(""), saltar_recientes: str = Form("")):
    aj = db.ajustes()
    if _ocupado():
        return _volver(request, "/gestor/campana", error="Ya hay un envío o búsqueda en curso; espera a que termine.")
    vence = date.today() + timedelta(days=max(1, dias))
    camp = {"asunto": asunto.replace("\r\n", "\n"), "cuerpo": cuerpo.replace("\r\n", "\n"),
            "responder_a": aj.get("gestor_responder_a"), "vence": vence if cupon else None,
            "descuento_txt": f"{porcentaje} %" if cupon else "", "cupon": ""}
    base = _base_url(request, aj)
    with db.Session() as s:
        lista = [c for c in g.segmento(_lista(s, seg, aj), seg, filtro) if c["email"] and not c["no_contactar"]]
        if saltar_recientes:
            recientes = {e for (e,) in s.execute(select(g.CampanaEnvio.email).where(
                g.CampanaEnvio.fecha >= datetime.now() - timedelta(days=14), g.CampanaEnvio.ok == 1))}
            lista = [c for c in lista if c["email"] not in recientes]
    if prueba:   # a mí misma, con los datos del primer cliente del segmento
        yo = aj.get("gestor_responder_a") or config.SMTP_USER
        muestra = dict(lista[0]) if lista else {"nombre": "María Jesús", "compras": 3, "favorito": "Malal Teñido - Rojo"}
        muestra["email"] = yo
        if cupon:
            camp["cupon"] = "PRUEBA-" + g.codigo_cupon()
        try:
            res = g.enviar([muestra], {**camp, "asunto": "[PRUEBA] " + camp["asunto"]}, base, pausa=0)
        except Exception as e:  # noqa: BLE001
            return _volver(request, f"/gestor/campana?seg={seg}&filtro={filtro}", error=str(e))
        ok = res and res[0][1]
        return _volver(request, f"/gestor/campana?seg={seg}&filtro={filtro}",
                       **({"aviso": f"Prueba enviada a {yo}. Revisa cómo se ve antes de enviar a todos."} if ok
                          else {"error": f"No se pudo enviar la prueba: {res[0][2] if res else ''}"}))
    if not lista:
        return _volver(request, f"/gestor/campana?seg={seg}&filtro={filtro}", error="Ese segmento no tiene clientes con correo para contactar.")
    tope = int(aj.get("gestor_max_envios") or 300)
    lista = lista[:tope]
    if cupon:
        camp["cupon"] = g.codigo_cupon()
        try:
            import woo
            pr, ct, ex = prod, cat, donde == "excepto"
            woo.crear_cupon(camp["cupon"], porcentaje, vence, [c["email"] for c in lista], f"Campaña {nombre or seg}",
                            productos=pr, categorias=ct, excluir=ex)
        except Exception as e:  # noqa: BLE001
            return _volver(request, f"/gestor/campana?seg={seg}&filtro={filtro}",
                           error=f"No pude crear el cupón en la web (no se envió nada): {str(e)[:200]}")
    with db.Session() as s:
        c_db = g.Campana(nombre=nombre or g.SEGMENTOS.get(seg, seg), segmento=seg, filtro=filtro, asunto=camp["asunto"],
                         cuerpo=camp["cuerpo"], cupon=camp["cupon"], descuento=camp["descuento_txt"], vence=camp["vence"],
                         destinatarios=len(lista), estado="enviando")
        s.add(c_db)
        s.commit()
        cid = c_db.id

    def trabajo(avance):
        if config.DEMO and not config.SMTP_PASSWORD:
            res = [(c["email"], True, "") for c in lista]      # modo demo: no se envía nada
        else:
            res = g.enviar(lista, camp, base, avance)
        with db.Session() as s:
            for email, ok, err in res:
                s.add(g.CampanaEnvio(campana_id=cid, email=email, ok=1 if ok else 0, error=err))
            c = s.get(g.Campana, cid)
            c.enviados = sum(1 for r in res if r[1])
            c.errores = sum(1 for r in res if not r[1])
            c.estado = "enviada" if c.enviados else "error"
            if len(res) < len(lista):
                c.nota = f"Se detuvo en {len(res)} de {len(lista)} (límite diario de Gmail). Vuelve a enviar mañana con «saltar los ya contactados»."
            s.commit()
            return f"Campaña enviada: {c.enviados} correos" + (f", {c.errores} con error" if c.errores else "") + (f". {c.nota}" if c.nota else ".")
    _en_fondo("campana", trabajo)
    return _volver(request, "/gestor/campanas", aviso=f"Enviando la campaña a {len(lista)} clientes" +
                   (f" con el cupón {camp['cupon']}" if camp["cupon"] else "") + ". Puedes seguir trabajando.")


@router.get("/gestor/campanas", response_class=HTMLResponse)
def campanas(request: Request):
    with db.Session() as s:
        lista = list(s.scalars(select(g.Campana).order_by(g.Campana.fecha.desc()).limit(100)))
    return _render(request, "gestor_campanas.html", lista=lista, trabajo=TRABAJO, pest="campanas")


# ---------------- cupones ----------------
@router.get("/gestor/cupones", response_class=HTMLResponse)
def cupones(request: Request, email: str = "", q: str = ""):
    import woo
    try:
        lista = woo.cupones(q)
        err = None
    except Exception as e:  # noqa: BLE001
        lista, err = [], f"No pude leer los cupones de la web: {str(e)[:200]}"
    hoy = date.today().isoformat()
    for c in lista:
        vence = (c.get("date_expires") or "")[:10]
        c["vence"] = vence
        c["vigente"] = c.get("status") == "publish" and (not vence or vence >= hoy)
    nombre = ""
    if email:
        with db.Session() as s:
            c = next((x for x in g.clientes(s) if x["email"] == email.lower()), None)
            nombre = c["nombre"] if c else ""
    return _render(request, "gestor_cupones.html", lista=lista, email=email, cliente_nombre=nombre, q=q, pest="cupones",
                   error_lectura=err, sugerido=g.codigo_cupon(), hoy=date.today(),
                   prods=_catalogo()[0], cats=_catalogo()[1],
                   smtp=bool(config.SMTP_USER and config.SMTP_PASSWORD))


@router.post("/gestor/cupones")
def cupon_nuevo(request: Request, prod: list[int] = Form([]), cat: list[int] = Form([]), donde: str = Form("solo"), codigo: str = Form(...), tipo: str = Form("percent"), monto: float = Form(...),
                vence: str = Form(""), correos: str = Form(""), minimo: float = Form(0), usos: int = Form(1),
                envio_gratis: str = Form(""), nota: str = Form(""), avisar: str = Form(""), mensaje: str = Form("")):
    import re as _re
    import woo
    lista = [e.strip().lower() for e in _re.split(r"[,;\s]+", correos) if "@" in e]
    codigo = _re.sub(r"[^A-Za-z0-9_-]", "", codigo).upper()
    vence_d = date.fromisoformat(vence) if vence else None
    try:
        pr, ct, ex = prod, cat, donde == "excepto"
        woo.crear_cupon(codigo, monto, vence_d, lista, nota or "Creado desde el sistema", tipo, minimo, usos, bool(envio_gratis),
                        productos=pr, categorias=ct, excluir=ex)
    except Exception as e:  # noqa: BLE001
        return _volver(request, "/gestor/cupones", error=f"La web no aceptó el cupón: {str(e)[:200]}")
    aviso = f"Cupón {codigo} creado en la web" + (f" solo para {', '.join(lista)}" if lista else " (para cualquier cliente)") + "."
    if avisar and lista and mensaje.strip():
        aj = db.ajustes()
        desc = f"{monto:g} %" if tipo == "percent" else "$" + f"{monto:,.0f}".replace(",", ".")
        camp = {"asunto": "Un regalo de Topwool para ti 🎁", "cuerpo": mensaje.replace("\r\n", "\n"), "cupon": codigo,
                "descuento_txt": desc, "vence": vence_d, "responder_a": aj.get("gestor_responder_a")}
        with db.Session() as s:
            todos = {c["email"]: c for c in g.clientes(s)}
        dest = [todos.get(e) or {"email": e, "nombre": ""} for e in lista]
        try:
            res = g.enviar(dest, camp, _base_url(request, aj), pausa=0.5)
            ok = sum(1 for r in res if r[1])
            aviso += f" Correo enviado a {ok} de {len(dest)}."
        except Exception as e:  # noqa: BLE001
            return _volver(request, "/gestor/cupones", aviso=aviso, error=f"No pude enviar el correo: {str(e)[:150]}")
    return _volver(request, "/gestor/cupones", aviso=aviso)


@router.post("/gestor/cupones/{cid}")
def cupon_cambiar(request: Request, cid: int, prod: list[int] = Form([]), cat: list[int] = Form([]), donde: str = Form("solo"), accion: str = Form(...), vence: str = Form(""), correos: str = Form("")):
    import re as _re
    import woo
    if accion == "productos":
        pr, ct, ex = prod, cat, donde == "excepto"
        try:
            woo.actualizar_cupon(cid, woo.restriccion_productos(pr, ct, ex))
        except Exception as e:  # noqa: BLE001
            return _volver(request, "/gestor/cupones", error=f"No pude cambiar el cupón: {str(e)[:200]}")
        return _volver(request, "/gestor/cupones", aviso="Productos del cupón actualizados.")
    datos = {"pausar": {"status": "draft"}, "activar": {"status": "publish"},
             "vence": {"date_expires": (vence + "T23:59:59") if vence else None},
             "correos": {"email_restrictions": [e.strip().lower() for e in _re.split(r"[,;\s]+", correos) if "@" in e]}}.get(accion)
    if not datos:
        return _volver(request, "/gestor/cupones")
    try:
        woo.actualizar_cupon(cid, datos)
    except Exception as e:  # noqa: BLE001
        return _volver(request, "/gestor/cupones", error=f"No pude cambiar el cupón: {str(e)[:200]}")
    return _volver(request, "/gestor/cupones", aviso="Cupón actualizado en la web.")


# ---------------- baja (pública, sin clave) ----------------
@router.api_route("/baja", methods=["GET", "POST"], response_class=HTMLResponse)
def baja(e: str = "", t: str = ""):
    import hmac
    ok = bool(e) and hmac.compare_digest(t or "", g.token_baja(e))
    if ok:
        with db.Session() as s:
            if not s.get(g.NoContactar, e.lower()):
                s.add(g.NoContactar(email=e.lower()[:200], motivo="se dio de baja"))
                s.commit()
    msg = ("Listo. No te enviaremos más correos de promociones de Topwool. "
           "Seguirás recibiendo los avisos de tus pedidos." if ok else "El enlace no es válido.")
    return HTMLResponse(f"""<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Topwool</title><body style="font-family:system-ui,sans-serif;max-width:520px;margin:60px auto;padding:0 16px;color:#3b2f26;background:#faf7f2">
<h2>Topwool</h2><p>{msg}</p><p><a href="https://www.topwool.cl">www.topwool.cl</a></p></body></html>""")


# ---------------- Mercado Público ----------------
def _buscar_mp(avance):
    r = mp.buscar(avance)
    db.guardar_ajustes({"mp_ultima_busqueda": datetime.now().isoformat(timespec="minutes")})
    aj = db.ajustes()
    destino = (aj.get("mp_avisar_correo") or "").strip()
    if r["nuevas"] and destino and config.SMTP_USER and config.SMTP_PASSWORD:
        from produccion import enviar_correo
        try:
            enviar_correo(destino, f"Mercado Público: {len(r['nuevas'])} oportunidades nuevas para Topwool",
                          mp.texto_aviso(r["nuevas"]), [])
        except Exception:  # noqa: BLE001
            traceback.print_exc()
    return f"Búsqueda lista: {r['total']} oportunidades coinciden, {len(r['nuevas'])} nuevas."


@router.get("/gestor/mercado", response_class=HTMLResponse)
def mercado(request: Request, ver: str = "abiertas", tipo: str = ""):
    ahora = datetime.now()
    with db.Session() as s:
        st = select(mp.Oportunidad)
        if ver == "abiertas":
            st = st.where(mp.Oportunidad.estado.in_(["nueva", "interesa"]),
                          (mp.Oportunidad.cierre >= ahora) | (mp.Oportunidad.cierre.is_(None)))
        elif ver in ("interesa", "postulada", "descartada"):
            st = st.where(mp.Oportunidad.estado == ver)
        if tipo:
            st = st.where(mp.Oportunidad.tipo == tipo)
        lista = sorted(s.scalars(st), key=lambda o: (o.cierre or datetime.max) if ver == "abiertas" else -(o.encontrada or ahora).timestamp())
    aj = db.ajustes()
    return _render(request, "gestor_mercado.html", lista=lista[:300], ver=ver, tipo=tipo, aj=aj, ahora=ahora,
                   ticket=mp.hay_ticket(), trabajo=TRABAJO, link_lic=mp.LINK_LICITACION, pest="mercado",
                   smtp=bool(config.SMTP_USER and config.SMTP_PASSWORD))


@router.post("/gestor/mercado/buscar")
def mercado_buscar(request: Request):
    if _ocupado():
        return _volver(request, "/gestor/mercado", error="Ya hay una tarea en curso; espera a que termine.")
    if not mp.hay_ticket() and not config.DEMO:
        return _volver(request, "/gestor/mercado", error="Falta el ticket de Mercado Público en Render (MERCADOPUBLICO_TICKET).")
    _en_fondo("mercado", _buscar_mp)
    return _volver(request, "/gestor/mercado", aviso="Buscando en Mercado Público… tarda 1 a 3 minutos.")


@router.post("/gestor/mercado/{codigo}/estado")
def mercado_estado(request: Request, codigo: str, estado: str = Form(...), nota: str = Form(""), ver: str = Form("abiertas")):
    with db.Session() as s:
        o = s.get(mp.Oportunidad, codigo)
        if o:
            o.estado = estado
            if nota:
                o.nota = nota
            s.commit()
    return _volver(request, f"/gestor/mercado?ver={ver}")


@router.post("/gestor/mercado/ajustes")
async def mercado_ajustes(request: Request):
    form = await request.form()
    db.guardar_ajustes({k: v for k, v in form.items() if k.startswith(("mp_", "gestor_"))})
    return _volver(request, "/gestor/mercado", aviso="Ajustes guardados.")


@router.get("/gestor/trabajo", response_class=HTMLResponse)
def trabajo_estado():
    return HTMLResponse(f"{TRABAJO.get('estado', '')}|{TRABAJO.get('mensaje', '')}")


# ---------------- fondos ----------------
@router.get("/gestor/fondos", response_class=HTMLResponse)
def fondos(request: Request):
    hoy = date.today()
    with db.Session() as s:
        lista = list(s.scalars(select(g.Fondo).order_by(g.Fondo.cierra.is_(None), g.Fondo.cierra)))
    revisar = [f for f in lista if f.estado == "por_revisar" and (not f.cierra or f.cierra >= hoy)]
    vig = [f for f in lista if f.estado not in ("por_revisar", "descartado") and (not f.cierra or f.cierra >= hoy)]
    cerr = [f for f in lista if f.estado not in ("por_revisar", "descartado") and f.cierra and f.cierra < hoy]
    return _render(request, "gestor_fondos.html", vigentes=vig, revisar=revisar, cerrados=cerr[-20:], fuentes=g.FUENTES,
                   hoy=hoy, pest="fondos", aj=db.ajustes(), trabajo=TRABAJO)


def _buscar_fondos(avance):
    import fondos as fd
    r = fd.buscar(avance)
    return f"Fondos: {r['nuevos']} nuevos para revisar ({r['revisados']} enlaces encontrados)."


@router.post("/gestor/fondos/buscar")
def fondos_buscar(request: Request):
    if _ocupado():
        return _volver(request, "/gestor/fondos", error="Ya hay una tarea en curso; espera a que termine.")
    _en_fondo("fondos", _buscar_fondos)
    return _volver(request, "/gestor/fondos", aviso="Buscando fondos en las páginas oficiales y noticias… tarda 1 a 2 minutos.")


@router.post("/gestor/fondos/{fid}/revisar")
def fondo_revisar(request: Request, fid: int, accion: str = Form(...), cierra: str = Form("")):
    with db.Session() as s:
        f = s.get(g.Fondo, fid)
        if f:
            f.estado = "vigente" if accion == "sirve" else "descartado"
            if cierra:
                f.cierra = date.fromisoformat(cierra)
            s.commit()
    return _volver(request, "/gestor/fondos", aviso="Agregado a la lista de fondos." if accion == "sirve" else "Descartado.")


@router.post("/gestor/fondos/ajustes")
async def fondos_ajustes(request: Request):
    form = await request.form()
    db.guardar_ajustes({k: v.replace("\r\n", "\n") for k, v in form.items() if k.startswith("fondos_")})
    return _volver(request, "/gestor/fondos", aviso="Fuentes guardadas.")


@router.post("/gestor/fondos")
def fondo_nuevo(request: Request, nombre: str = Form(...), institucion: str = Form(""), region: str = Form(""),
                abre: str = Form(""), cierra: str = Form(""), url: str = Form(""), nota: str = Form(""), fid: int = Form(0)):
    with db.Session() as s:
        f = s.get(g.Fondo, fid) if fid else g.Fondo()
        f.nombre, f.institucion, f.region, f.url, f.nota = nombre[:250], institucion[:80], region[:80], url[:400], nota
        f.estado = "vigente"
        f.abre = date.fromisoformat(abre) if abre else None
        f.cierra = date.fromisoformat(cierra) if cierra else None
        if not fid:
            s.add(f)
        s.commit()
    return _volver(request, "/gestor/fondos", aviso="Fondo guardado.")


@router.post("/gestor/fondos/{fid}/borrar")
def fondo_borrar(request: Request, fid: int):
    with db.Session() as s:
        f = s.get(g.Fondo, fid)
        if f:
            s.delete(f)
            s.commit()
    return _volver(request, "/gestor/fondos", aviso="Fondo borrado.")


# ---------------- búsqueda automática diaria ----------------
def iniciar_automatico():
    """Revisa Mercado Público dos veces al día (si hay ticket) y los fondos una vez al día."""
    def bucle():
        time.sleep(90)
        while True:
            try:
                ult = db.ajustes().get("mp_ultima_busqueda") or ""
                vieja = not ult or datetime.fromisoformat(ult) < datetime.now() - timedelta(hours=12)
                if mp.hay_ticket() and vieja and not _ocupado():
                    _en_fondo("mercado", _buscar_mp)
                    time.sleep(300)
                ultf = db.ajustes().get("fondos_ultima_busqueda") or ""
                if (not ultf or datetime.fromisoformat(ultf) < datetime.now() - timedelta(hours=24)) and not _ocupado() \
                        and not config.DEMO:
                    _en_fondo("fondos", _buscar_fondos)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(1800)
    threading.Thread(target=bucle, daemon=True).start()
