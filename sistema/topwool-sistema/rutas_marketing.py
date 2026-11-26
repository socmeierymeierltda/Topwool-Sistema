"""Pantallas de Marketing: panel de meta, calendario y grilla de contenidos."""
import calendar
import io
from datetime import date, timedelta

import threading

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from sqlalchemy import select

import db
import marketing as mk

router = APIRouter()
_ui = {}


def _render(*a, **k):
    return _ui["render"](*a, **k)


def _volver(*a, **k):
    return _ui["volver"](*a, **k)


def base_publica(request=None):
    """Dirección pública del sistema (Instagram descarga desde ahí las fotos y videos)."""
    import config
    u = (db.ajustes().get("gestor_url_publica") or "").strip() or config.URL_PUBLICA or (str(request.base_url) if request else "")
    if u.startswith("http://") and "localhost" not in u and "127.0.0.1" not in u:
        u = "https://" + u[7:]
    return u.rstrip("/")


def _mes(mes):
    try:
        y, m = map(int, (mes or "").split("-"))
        return y, m
    except ValueError:
        h = date.today()
        return h.year, h.month


@router.get("/marketing", response_class=HTMLResponse)
def panel(request: Request):
    import informes as inf
    aj = db.ajustes()
    hoy = date.today()
    meta = float(aj.get("marketing_meta") or 0)
    with db.Session() as s:
        mk.cargar_eventos(s, [hoy.year, hoy.year + 1])
        s.commit()
        ult = inf.ultima_sync(s)
        pm = mk.panel_meta(s, meta)
        emp = mk.ventas_empresa(s)
        may, may_atras, may_n = mk.mayoristas_a_contactar(s)
        recs = mk.recomendaciones(s, pm, aj)
        prox = mk.eventos_entre(s, hoy, hoy + timedelta(days=75))
        idx, prod_mes = mk.estacionalidad(s)
        semana = list(s.scalars(select(mk.Pieza).where(mk.Pieza.fecha >= hoy - timedelta(days=hoy.weekday()),
                                                      mk.Pieza.fecha <= hoy + timedelta(days=6 - hoy.weekday()))
                                .order_by(mk.Pieza.fecha)))
    return _render(request, "marketing.html", pm=pm, recs=recs, prox=prox, idx=idx, meses=mk.MESES, hoy=hoy, ult=ult,
                   prod_mes={m: [f for f, _ in c.most_common(3)] for m, c in prod_mes.items()}, semana=semana, aj=aj,
                   dias=mk.DIAS, emp=emp, may=may, may_atras=may_atras, may_n=may_n,
                   meta_total=float(aj.get("marketing_meta_total") or 0))


@router.post("/marketing/meta")
def guardar_meta(request: Request, marketing_meta: str = Form(""), marketing_meta_total: str = Form(None)):
    datos = {}
    if marketing_meta:
        datos["marketing_meta"] = "".join(ch for ch in marketing_meta if ch.isdigit()) or "0"
    if marketing_meta_total is not None:
        datos["marketing_meta_total"] = "".join(ch for ch in marketing_meta_total if ch.isdigit())
    db.guardar_ajustes(datos)
    return _volver(request, "/marketing", aviso="Meta guardada.")


@router.get("/marketing/calendario", response_class=HTMLResponse)
def ver_calendario(request: Request, mes: str = "", canal: str = "", publico: str = ""):
    y, m = _mes(mes)
    ini = date(y, m, 1)
    fin = date(y, m, calendar.monthrange(y, m)[1])
    aj = db.ajustes()
    with db.Session() as s:
        mk.cargar_eventos(s, [y, y + 1])
        s.commit()
        evs = mk.eventos_entre(s, ini - timedelta(days=7), fin + timedelta(days=7))
        st = select(mk.Pieza).where(mk.Pieza.fecha >= ini - timedelta(days=7), mk.Pieza.fecha <= fin + timedelta(days=7))
        if canal:
            st = st.where(mk.Pieza.canal == canal)
        if publico:
            st = st.where(mk.Pieza.publico.in_([publico, "Todos"]))
        piezas = list(s.scalars(st.order_by(mk.Pieza.fecha, mk.Pieza.id)))
        idx, prod_mes = mk.estacionalidad(s)
    semanas = calendar.Calendar(0).monthdatescalendar(y, m)
    por_dia_ev, por_dia_p = {}, {}
    for e in evs:   # los eventos largos se muestran el primer día, cada lunes y el último día
        d = e.fecha
        while d <= (e.hasta or e.fecha):
            if d == e.fecha or d == e.hasta or d.weekday() == 0 or (e.hasta and (e.hasta - e.fecha).days <= 4):
                por_dia_ev.setdefault(d, []).append((e, d != e.fecha))
            d += timedelta(days=1)
    for p in piezas:
        por_dia_p.setdefault(p.fecha, []).append(p)
    ant = (ini - timedelta(days=1)).strftime("%Y-%m")
    sig = (fin + timedelta(days=1)).strftime("%Y-%m")
    del_mes = [p for p in piezas if ini <= p.fecha <= fin]
    return _render(request, "marketing_calendario.html", semanas=semanas, y=y, m=m, mes_nombre=mk.MESES[m - 1],
                   ev=por_dia_ev, pz=por_dia_p, ant=ant, sig=sig, canal=canal, canales=mk.CANALES, publico=publico, publicos=mk.PUBLICOS, hoy=date.today(),
                   n_piezas=len(del_mes), n_listas=sum(1 for p in del_mes if p.estado in ("listo", "publicado")),
                   idx=idx.get(m, 0), top=[f for f, _ in prod_mes.get(m, {}).most_common(4)] if prod_mes.get(m) else [],
                   eventos_mes=[e for e in evs if e.fecha <= fin and (e.hasta or e.fecha) >= ini], aj=aj)


@router.post("/marketing/generar")
def generar(request: Request, mes: str = Form(...), reemplazar: str = Form("")):
    y, m = _mes(mes)
    with db.Session() as s:
        mk.cargar_eventos(s, [y])
        n = mk.generar_mes(s, y, m, db.ajustes(), reemplazar=bool(reemplazar))
        s.commit()
    return _volver(request, f"/marketing/calendario?mes={y}-{m:02d}",
                   aviso=f"Grilla de {mk.MESES[m - 1]} armada: {n} piezas propuestas. Revísalas, cámbialas y márcalas a medida que avanzas.")


@router.post("/marketing/ritmo")
def guardar_ritmo(request: Request, marketing_ritmo: str = Form(""), mes: str = Form(""), marketing_auto: str = Form("")):
    db.guardar_ajustes({"marketing_ritmo": marketing_ritmo, "marketing_auto": "1" if marketing_auto else ""})
    return _volver(request, f"/marketing/calendario?mes={mes}", aviso="Ritmo semanal guardado.")


@router.get("/marketing/pieza", response_class=HTMLResponse)
def ver_pieza(request: Request, pid: int = 0, fecha: str = ""):
    with db.Session() as s:
        p = s.get(mk.Pieza, pid) if pid else mk.Pieza(fecha=date.fromisoformat(fecha) if fecha else date.today(),
                                                       canal="Instagram", estado="idea")
        evs = mk.eventos_entre(s, p.fecha - timedelta(days=3), p.fecha + timedelta(days=14)) if p else []
        import instagram as ig
        archivos = list(s.scalars(select(ig.IgArchivo).where(ig.IgArchivo.pieza_id == p.id)
                                  .order_by(ig.IgArchivo.orden, ig.IgArchivo.id))) if p and p.id else []
    if not p:
        return _volver(request, "/marketing/calendario", error="No encontré esa pieza.")
    return _render(request, "marketing_pieza.html", p=p, canales=mk.CANALES, estados=mk.ESTADOS, evs=evs, publicos=mk.PUBLICOS,
                   formatos=list(mk.GUIAS), link=mk.utm("https://www.topwool.cl/", p.canal, p.campana),
                   archivos=archivos, ig_ok=ig.conectado(), ig_usuario=db.ajustes().get("ig_usuario"),
                   ig_tipo=ig.tipo_publicacion(p, archivos) if archivos else "",
                   metricas=__import__("json").loads(p.ig_metricas) if p.ig_metricas else {})


@router.post("/marketing/pieza")
def guardar_pieza(request: Request, pid: int = Form(0), fecha: str = Form(...), canal: str = Form(""), formato: str = Form(""),
                  publico: str = Form("Todos"),
                  titulo: str = Form(""), producto: str = Form(""), campana: str = Form(""), texto: str = Form(""),
                  guia: str = Form(""), cta: str = Form(""), cupon: str = Form(""), estado: str = Form("idea"),
                  resultado: str = Form(""), borrar: str = Form(""), hora: str = Form("10:00"), ig_auto: str = Form(""),
                  archivos: list[UploadFile] = File([])):
    f = date.fromisoformat(fecha)
    with db.Session() as s:
        p = s.get(mk.Pieza, pid) if pid else None
        if borrar and p:
            s.delete(p)
            s.commit()
            return _volver(request, f"/marketing/calendario?mes={f:%Y-%m}", aviso="Pieza borrada.")
        if not p:
            p = mk.Pieza(generada=False)
            s.add(p)
        p.fecha, p.canal, p.formato, p.titulo, p.producto = f, canal, formato[:60], titulo[:200], producto[:150]
        p.campana, p.copy, p.guia, p.cta, p.cupon = campana[:120], texto.replace("\r\n", "\n"), guia.replace("\r\n", "\n"), cta[:200], cupon[:60]
        p.estado, p.resultado, p.publico = estado, resultado, publico
        p.hora = hora[:5] if hora else "10:00"
        p.ig_auto = bool(ig_auto)
        if p.ig_auto and p.estado not in ("publicado",):
            p.estado = "programado"
        elif not p.ig_auto and p.estado == "programado":
            p.estado = "listo"
        s.flush()
        import instagram as ig
        subidos = 0
        for a in archivos or []:
            if a and a.filename:
                try:
                    ig.guardar_archivo(s, p.id, a.file.read(), a.filename, a.content_type or "")
                    subidos += 1
                except Exception as e:  # noqa: BLE001
                    s.rollback()
                    return _volver(request, f"/marketing/pieza?pid={p.id}", error=f"No pude leer {a.filename}: {str(e)[:150]}")
        s.commit()
        pid = p.id
        aviso = "Pieza guardada" + (f" con {subidos} archivo(s)" if subidos else "") + \
            (f". Se publicará sola en Instagram el {f:%d-%m} a las {p.hora}." if p.ig_auto else ".")
    if subidos or ig_auto:
        return _volver(request, f"/marketing/pieza?pid={pid}", aviso=aviso)
    return _volver(request, f"/marketing/calendario?mes={f:%Y-%m}", aviso=aviso)


@router.post("/marketing/pieza/{pid}/archivo/{aid}/borrar")
def borrar_archivo(request: Request, pid: int, aid: int):
    import instagram as ig
    with db.Session() as s:
        a = s.get(ig.IgArchivo, aid)
        if a and a.pieza_id == pid:
            s.delete(a)
            s.commit()
    return _volver(request, f"/marketing/pieza?pid={pid}", aviso="Archivo quitado.")


@router.post("/marketing/pieza/{pid}/publicar")
def publicar_ahora(request: Request, pid: int):
    import instagram as ig
    if not ig.conectado():
        return _volver(request, f"/marketing/pieza?pid={pid}", error="Instagram no está conectado (falta IG_TOKEN en Render).")
    base = base_publica(request)
    threading.Thread(target=ig.publicar_pieza, args=(pid, base), daemon=True).start()
    return _volver(request, f"/marketing/pieza?pid={pid}",
                   aviso="Publicando en Instagram… las fotos tardan unos segundos y los videos hasta 2 minutos. Recarga la página para ver el resultado.")


@router.get("/media/{nombre}")
def media_publica(nombre: str):
    """Archivo de una pieza en URL pública (nombre largo imposible de adivinar) para que Instagram lo descargue."""
    import instagram as ig
    clave = nombre.rsplit(".", 1)[0]
    with db.Session() as s:
        a = s.scalars(select(ig.IgArchivo).where(ig.IgArchivo.clave == clave)).first()
        if not a:
            return Response(status_code=404)
        return Response(a.datos, media_type=a.tipo, headers={"Cache-Control": "public, max-age=3600"})


@router.get("/marketing/miniatura/{aid}")
def miniatura(aid: int):
    import instagram as ig
    with db.Session() as s:
        a = s.get(ig.IgArchivo, aid)
        if not a:
            return Response(status_code=404)
        return Response(a.datos, media_type=a.tipo)


@router.get("/marketing/instagram", response_class=HTMLResponse)
def ver_instagram(request: Request):
    import json

    import instagram as ig
    aj = db.ajustes()
    info, err = {}, ""
    if ig.conectado():
        try:
            info = ig.cuenta()
        except Exception as e:  # noqa: BLE001
            err = str(e)[:300]
    with db.Session() as s:
        medias = list(s.scalars(select(ig.IgMedia).order_by(ig.IgMedia.fecha.desc()).limit(60)))
        for m in medias:
            m.met = json.loads(m.metricas or "{}")
            r = m.met.get("reach") or 0
            m.tasa = 100 * ((m.met.get("likes") or 0) + (m.met.get("comments") or 0) + (m.met.get("saved") or 0)
                            + (m.met.get("shares") or 0)) / r if r else None
        por_tipo, por_dia = ig.resumen([m for m in medias if m.producto != "STORY"])
        programadas = list(s.scalars(select(mk.Pieza).where(mk.Pieza.estado == "programado").order_by(mk.Pieza.fecha)))
    mejores = sorted([m for m in medias if m.tasa is not None], key=lambda m: -m.tasa)[:5]
    return _render(request, "marketing_instagram.html", pest="instagram", ok=ig.conectado(), info=info, err=err, aj=aj,
                   medias=medias, por_tipo=por_tipo, por_dia=por_dia, mejores=mejores, programadas=programadas,
                   base=base_publica(request))


@router.post("/marketing/instagram/sincronizar")
def ig_sincronizar(request: Request):
    import instagram as ig
    try:
        ig.renovar_token()
        nuevos, total = ig.sincronizar()
    except Exception as e:  # noqa: BLE001
        return _volver(request, "/marketing/instagram", error=f"Instagram respondió: {str(e)[:250]}")
    return _volver(request, "/marketing/instagram", aviso=f"Resultados actualizados: {total} publicaciones ({nuevos} nuevas).")


@router.post("/marketing/pieza/{pid}/estado")
def estado_pieza(request: Request, pid: int, estado: str = Form(...), volver: str = Form("/marketing")):
    with db.Session() as s:
        p = s.get(mk.Pieza, pid)
        if p:
            p.estado = estado
            s.commit()
    return _volver(request, volver)


@router.post("/marketing/evento")
def nuevo_evento(request: Request, nombre: str = Form(...), fecha: str = Form(...), hasta: str = Form(""), nota: str = Form(""),
                 mes: str = Form("")):
    with db.Session() as s:
        s.add(mk.Evento(nombre=nombre[:150], fecha=date.fromisoformat(fecha), hasta=date.fromisoformat(hasta) if hasta else None,
                        tipo="propio", nota=nota[:300]))
        s.commit()
    return _volver(request, f"/marketing/calendario?mes={mes}", aviso="Fecha agregada al calendario.")


@router.post("/marketing/evento/{eid}/borrar")
def borrar_evento(request: Request, eid: int, mes: str = Form("")):
    with db.Session() as s:
        e = s.get(mk.Evento, eid)
        if e:
            s.delete(e)
            s.commit()
    return _volver(request, f"/marketing/calendario?mes={mes}", aviso="Fecha borrada del calendario.")


@router.get("/marketing/exportar")
def exportar(mes: str = ""):
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    y, m = _mes(mes)
    ini, fin = date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
    with db.Session() as s:
        piezas = list(s.scalars(select(mk.Pieza).where(mk.Pieza.fecha >= ini, mk.Pieza.fecha <= fin).order_by(mk.Pieza.fecha)))
        evs = mk.eventos_entre(s, ini, fin)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Grilla {mk.MESES[m - 1]}"
    cab = ["Fecha", "Día", "Canal", "Público", "Formato", "Producto", "Campaña", "Texto", "Foto / video: paso a paso", "Enlace", "Estado"]
    ws.append(cab)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF", name="Arial")
        c.fill = PatternFill("solid", fgColor="5B4636")
    for p in piezas:
        ws.append([p.fecha.strftime("%d-%m-%Y"), mk.DIAS[p.fecha.weekday()], p.canal, p.publico or "Todos", p.formato, p.producto, p.campana,
                   p.copy, p.guia, mk.utm("https://www.topwool.cl/", p.canal, p.campana), p.estado])
    for col, w in zip("ABCDEFGHIJK", [11, 10, 11, 12, 20, 22, 18, 50, 60, 40, 12]):
        ws.column_dimensions[col].width = w
    for fila in ws.iter_rows(min_row=2):
        for c in fila:
            c.alignment = Alignment(wrap_text=True, vertical="top")
            c.font = Font(name="Arial", size=10)
    we = wb.create_sheet("Fechas del mes")
    we.append(["Desde", "Hasta", "Fecha", "Tipo", "Idea"])
    for e in evs:
        we.append([e.fecha.strftime("%d-%m-%Y"), e.hasta.strftime("%d-%m-%Y") if e.hasta else "",
                   e.nombre + (" (por confirmar)" if e.por_confirmar else ""), e.tipo, e.nota])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="grilla_{y}-{m:02d}.xlsx"'})


@router.get("/marketing/envio-gratis", response_class=HTMLResponse)
def envio_gratis(request: Request, margen: float = 50, costo: float = 100, empuje: float = 30, nuevos: float = 0):
    with db.Session() as s:
        r = mk.envio_gratis(s, margen=margen / 100, costo_pct=costo / 100, empuje=empuje / 100, nuevos=nuevos / 100)
    return _render(request, "marketing_envio.html", r=r, margen=margen, costo=costo, empuje=empuje, nuevos=nuevos, pest="envio")
