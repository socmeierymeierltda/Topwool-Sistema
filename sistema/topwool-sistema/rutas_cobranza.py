"""Pantallas de Cobranza: documentos, abonos, avisos de despacho y reportes."""
import io
from datetime import date, datetime

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import func, select

import cobranza as cb
import config
import db
import woo

router = APIRouter()
_ui = {}   # render y volver se inyectan desde main


def _render(*a, **k):
    return _ui["render"](*a, **k)


def _volver(*a, **k):
    return _ui["volver"](*a, **k)


def filas(s, estado="", vendedor="", q="", desde="", hasta="", solo_revisar=False):
    pagos = dict(s.execute(select(cb.Abono.documento_id, func.coalesce(func.sum(cb.Abono.monto), 0))
                           .group_by(cb.Abono.documento_id)).all())
    st = select(cb.Documento).order_by(cb.Documento.fecha.desc().nullslast(), cb.Documento.folio.desc())
    if vendedor:
        st = st.where(cb.Documento.vendedor == vendedor)
    if desde:
        st = st.where(cb.Documento.fecha >= date.fromisoformat(desde))
    if hasta:
        st = st.where(cb.Documento.fecha <= date.fromisoformat(hasta))
    out = []
    hoy = date.today()
    ql = cb.norm(q)
    for d in s.scalars(st):
        p = int(pagos.get(d.id, 0))
        e = cb.estado(d.total or 0, p)
        if estado and e != estado:
            continue
        if solo_revisar and not d.revisar:
            continue
        if ql and ql not in cb.norm(" ".join(str(x or "") for x in (d.cliente, d.razon_social, d.rut, d.folio, d.comentario))):
            continue
        out.append({"d": d, "pagado": p, "debe": max(0, (d.total or 0) - p), "estado": e,
                    "dias": (hoy - d.fecha).days if d.fecha else None})
    return out


def _vendedores(s):
    return [v for (v,) in s.execute(select(cb.Documento.vendedor).distinct().order_by(cb.Documento.vendedor)) if v]


@router.get("/cobranza", response_class=HTMLResponse)
def ver(request: Request, estado: str = "", vendedor: str = "", q: str = "", desde: str = "", hasta: str = "",
        revisar: str = ""):
    with db.Session() as s:
        lista = filas(s, estado, vendedor, q, desde, hasta, bool(revisar))
        vendedores = _vendedores(s)
        sin_doc = s.scalar(select(func.count()).select_from(cb.Despacho).where(cb.Despacho.documento_id.is_(None)))
        n_revisar = s.scalar(select(func.count()).select_from(cb.Documento).where(cb.Documento.revisar != "", cb.Documento.revisar.is_not(None)))
        por_avisar = s.scalar(select(func.count()).select_from(cb.Documento).where(
            cb.Documento.cliente.like("web%"), cb.Documento.tracking != "", cb.Documento.tracking.is_not(None),
            cb.Documento.notificado.is_(None)))
    tot = {"total": sum(f["d"].total or 0 for f in lista), "pagado": sum(f["pagado"] for f in lista),
           "debe": sum(f["debe"] for f in lista)}
    por_vendedor = {}
    for f in lista:
        if f["debe"]:
            por_vendedor[f["d"].vendedor or "—"] = por_vendedor.get(f["d"].vendedor or "—", 0) + f["debe"]
    filtros = {"estado": estado, "vendedor": vendedor, "q": q, "desde": desde, "hasta": hasta, "revisar": revisar}
    return _render(request, "cobranza.html", lista=lista[:500], n=len(lista), tot=tot, vendedores=vendedores,
                   por_vendedor=sorted(por_vendedor.items(), key=lambda x: -x[1]), f=filtros, sin_doc=sin_doc,
                   n_revisar=n_revisar, por_avisar=por_avisar,
                   qs="&".join(f"{k}={v}" for k, v in filtros.items() if v))


@router.post("/cobranza/subir")
async def subir(request: Request, archivos: list[UploadFile] = File(...)):
    aj = db.ajustes()
    nuevos, repetidos, despachos, malos = 0, 0, 0, []
    with db.Session() as s:
        datos = []
        for a in archivos:
            try:
                datos.append(cb.leer_pdf(await a.read(), a.filename))
            except Exception:  # noqa: BLE001
                malos.append(a.filename)
        # primero documentos, después etiquetas (así se pueden unir)
        for d in [x for x in datos if x["clase"] == "documento"]:
            _, nuevo = cb.registrar_documento(s, d, aj)
            nuevos += nuevo
            repetidos += not nuevo
        for d in [x for x in datos if x["clase"] == "despacho"]:
            _, nuevo = cb.registrar_despacho(s, d)
            despachos += nuevo
        malos += [x["archivo"] for x in datos if x["clase"] == "desconocido"]
        cb.vincular_pendientes(s)
        s.commit()
    msg = f"Listo: {nuevos} documentos nuevos, {despachos} etiquetas de despacho"
    if repetidos:
        msg += f", {repetidos} ya estaban cargados"
    if malos:
        return _volver(request, "/cobranza", aviso=msg + ".", error="No pude leer: " + ", ".join(malos))
    return _volver(request, "/cobranza", aviso=msg + ".")


@router.post("/cobranza/importar")
async def importar(request: Request, archivo: UploadFile = File(...)):
    try:
        with db.Session() as s:
            n = cb.importar_planilla(s, await archivo.read())
            s.commit()
    except Exception as e:  # noqa: BLE001
        return _volver(request, "/cobranza", error=f"No pude importar la planilla: {e}")
    return _volver(request, "/cobranza", aviso=f"Se importaron {n} ventas de la planilla anterior.")


@router.get("/cobranza/doc/{did}", response_class=HTMLResponse)
def ver_doc(request: Request, did: int):
    aj = db.ajustes()
    with db.Session() as s:
        d = s.get(cb.Documento, did)
        if not d:
            return _volver(request, "/cobranza", error="Documento no encontrado")
        abonos = list(s.scalars(select(cb.Abono).where(cb.Abono.documento_id == did).order_by(cb.Abono.fecha)))
        despachos = list(s.scalars(select(cb.Despacho).where(cb.Despacho.documento_id == did)))
        p = sum(a.monto or 0 for a in abonos)
    return _render(request, "documento.html", d=d, abonos=abonos, despachos=despachos, pagado=p,
                   debe=max(0, (d.total or 0) - p), estado=cb.estado(d.total or 0, p),
                   link=cb.link_seguimiento(d.empresa, d.tracking, aj), hoy=date.today().isoformat())


@router.post("/cobranza/doc/{did}")
async def editar_doc(request: Request, did: int):
    form = await request.form()
    with db.Session() as s:
        d = s.get(cb.Documento, did)
        cliente_antes = d.cliente
        for k in ("cliente", "vendedor", "empresa", "tracking", "comentario"):
            if k in form:
                setattr(d, k, (form.get(k) or "").strip().lower() if k != "comentario" else form.get(k).strip())
        for k in ("monto", "flete"):
            if form.get(k):
                setattr(d, k, int(float(form.get(k))))
        d.total = (d.monto or 0) + (d.flete or 0)
        if d.cliente != cliente_antes or form.get("confirmar"):
            cb.aprender(s, d.rut, d.razon_social, d.cliente)   # el sistema aprende
            d.revisar = ""
        s.commit()
    return _volver(request, f"/cobranza/doc/{did}", aviso="Guardado. El sistema recordará este cliente para su RUT.")


@router.post("/cobranza/doc/{did}/abono")
async def abonar(request: Request, did: int):
    form = await request.form()
    with db.Session() as s:
        d = s.get(cb.Documento, did)
        monto = form.get("monto")
        if form.get("todo"):
            monto = max(0, (d.total or 0) - cb.pagado(d, s))
        monto = int(float(monto or 0))
        if monto <= 0:
            return _volver(request, f"/cobranza/doc/{did}", error="El monto del abono debe ser mayor a 0.")
        s.add(cb.Abono(documento_id=did, monto=monto, medio=form.get("medio") or "",
                       fecha=date.fromisoformat(form.get("fecha")) if form.get("fecha") else date.today(),
                       nota=form.get("nota") or ""))
        s.commit()
    return _volver(request, form.get("_volver") or f"/cobranza/doc/{did}", aviso=f"Abono de ${monto:,.0f} registrado.".replace(",", "."))


@router.post("/cobranza/abono/{aid}/borrar")
def borrar_abono(request: Request, aid: int):
    with db.Session() as s:
        a = s.get(cb.Abono, aid)
        did = a.documento_id
        s.delete(a)
        s.commit()
    return _volver(request, f"/cobranza/doc/{did}", aviso="Abono eliminado.")


@router.post("/cobranza/doc/{did}/borrar")
def borrar_doc(request: Request, did: int):
    with db.Session() as s:
        for a in s.scalars(select(cb.Abono).where(cb.Abono.documento_id == did)):
            s.delete(a)
        for x in s.scalars(select(cb.Despacho).where(cb.Despacho.documento_id == did)):
            x.documento_id = None
        s.delete(s.get(cb.Documento, did))
        s.commit()
    return _volver(request, "/cobranza", aviso="Documento eliminado.")


def _avisar(s, d, aj):
    """Envía el seguimiento al cliente web como nota del pedido (WooCommerce manda el correo)."""
    nombre = (d.cliente or "").replace("web - ", "").strip()
    woo_id = None
    import db as _db
    p = s.scalar(select(_db.PedidoWeb).where(_db.PedidoWeb.cliente == d.cliente).order_by(_db.PedidoWeb.enviado.desc()))
    if p:
        woo_id = p.woo_id
    if not woo_id:
        o = woo.buscar_pedido(nombre)
        woo_id = o["id"] if o else None
    if not woo_id:
        if config.DEMO:
            woo_id = -1
        else:
            return False, f"no encontré en la web un pedido a nombre de '{nombre}'"
    link = cb.link_seguimiento(d.empresa, d.tracking, aj)
    texto = aj["aviso_despacho"].replace("\\n", "\n")
    for k, v in {"nombre": nombre.split(" ")[0].title(), "empresa": (d.empresa or "").title(),
                 "tracking": d.tracking, "link": link or "(consulta con la empresa de transporte)"}.items():
        texto = texto.replace("{" + k + "}", v or "")
    woo.nota_cliente(woo_id, texto)
    try:   # el pedido web queda Completado (despachado)
        woo.cambiar_estado(woo_id, "completed")
    except Exception:  # noqa: BLE001
        pass
    d.notificado = datetime.now()
    return True, ""


@router.post("/cobranza/doc/{did}/avisar")
def avisar(request: Request, did: int):
    aj = db.ajustes()
    with db.Session() as s:
        d = s.get(cb.Documento, did)
        try:
            ok, motivo = _avisar(s, d, aj)
        except Exception as e:  # noqa: BLE001
            ok, motivo = False, str(e)[:200]
        s.commit()
    if not ok:
        return _volver(request, f"/cobranza/doc/{did}", error=f"No se pudo avisar: {motivo}")
    return _volver(request, f"/cobranza/doc/{did}", aviso="Seguimiento enviado al cliente por correo desde la tienda.")


@router.post("/cobranza/avisar-todos")
def avisar_todos(request: Request):
    aj = db.ajustes()
    ok_n, fallas = 0, []
    with db.Session() as s:
        docs = list(s.scalars(select(cb.Documento).where(
            cb.Documento.cliente.like("web%"), cb.Documento.tracking != "", cb.Documento.tracking.is_not(None),
            cb.Documento.notificado.is_(None))))
        for d in docs:
            try:
                ok, motivo = _avisar(s, d, aj)
            except Exception as e:  # noqa: BLE001
                ok, motivo = False, str(e)[:120]
            if ok:
                ok_n += 1
            else:
                fallas.append(f"{d.cliente} ({motivo})")
        s.commit()
    msg = f"Seguimiento enviado a {ok_n} clientes web."
    return _volver(request, "/cobranza", aviso=msg, error=("No se pudo: " + "; ".join(fallas)) if fallas else None)


# ---------------- seguimiento de pedidos ----------------
@router.get("/seguimiento", response_class=HTMLResponse)
def seguimiento(request: Request, etapa: str = "", q: str = ""):
    import seguimiento as sg
    error = None
    filas_ = []
    if config.DEMO or not config.PEDIDOS_SHEET_ID:
        error = "Para ver el seguimiento el sistema tiene que estar conectado al Drive de pedidos."
    else:
        try:
            datos = sg.leer_drive()
            with db.Session() as s:
                filas_ = sg.actualizar(s, datos)
                s.commit()
        except Exception as e:  # noqa: BLE001
            import sheets
            error = f"No pude leer el Drive de pedidos. {sheets.error_claro(e)}"
    conteo = {e: sum(1 for f in filas_ if f["etapa"] == e) for e in sg.ETAPAS}
    vista = [f for f in filas_ if (not etapa or f["etapa"] == etapa)
             and (not q or cb.norm(q) in cb.norm(f["cliente"] + " " + f["pedido"]))]
    return _render(request, "seguimiento.html", filas=vista, conteo=conteo, etapas=sg.ETAPAS, etapa=etapa, q=q,
                   error_drive=error)


# ---------------- exportar y reportes ----------------
CABS = ["Fecha", "Días", "Cliente", "Documento", "Folio", "Vendedor", "Despacho", "Seguimiento",
        "Monto", "Flete", "Total", "Pagado", "Debe", "Estado", "Comentario"]


def _fila_export(f):
    d = f["d"]
    return [d.fecha.strftime("%d-%m-%Y") if d.fecha else "", f["dias"], d.cliente, d.tipo, d.folio, d.vendedor,
            d.empresa, d.tracking, d.monto, d.flete, d.total, f["pagado"], f["debe"], f["estado"], d.comentario]


def _xlsx(titulo, grupos):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Cobranza"
    ws.append([titulo])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([f"Generado {datetime.now():%d-%m-%Y %H:%M}"])
    ws.append([])
    for nombre, lista in grupos:
        if nombre:
            ws.append([nombre.upper()])
            ws.cell(ws.max_row, 1).font = Font(bold=True)
        ws.append(CABS)
        for c in ws[ws.max_row]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="5B4636")
        for f in lista:
            ws.append(_fila_export(f))
        ws.append(["", "", "Subtotal", "", "", "", "", "", "", "", sum(f["d"].total or 0 for f in lista),
                   sum(f["pagado"] for f in lista), sum(f["debe"] for f in lista)])
        ws.cell(ws.max_row, 3).font = Font(bold=True)
        ws.append([])
    for col, w in zip("ABCDEFGHIJKLMNO", [11, 6, 30, 10, 8, 10, 14, 16, 11, 9, 11, 11, 11, 10, 40]):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=4):
        for c in row[8:13]:
            if isinstance(c.value, (int, float)):
                c.number_format = '"$"#,##0'
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _pdf(titulo, grupos):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    clp = lambda v: "$" + f"{int(v or 0):,}".replace(",", ".")  # noqa: E731
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=1.2 * cm, rightMargin=1.2 * cm,
                            topMargin=1.2 * cm, bottomMargin=1.2 * cm, title=titulo)
    st = getSampleStyleSheet()
    el = [Paragraph(titulo, st["Title"]), Paragraph(f"Topwool · generado {datetime.now():%d-%m-%Y %H:%M}", st["Normal"]),
          Spacer(1, 10)]
    total_debe = 0
    for nombre, lista in grupos:
        if nombre:
            el.append(Paragraph(f"<b>{nombre}</b>", st["Heading3"]))
        data = [["Fecha", "Días", "Cliente", "Doc.", "Folio", "Despacho", "Total", "Pagado", "Debe", "Estado"]]
        for f in lista:
            d = f["d"]
            data.append([d.fecha.strftime("%d-%m-%y") if d.fecha else "", f["dias"] if f["dias"] is not None else "",
                         Paragraph((d.cliente or "")[:45], st["BodyText"]), d.tipo, d.folio or "", d.empresa or "",
                         clp(d.total), clp(f["pagado"]), clp(f["debe"]), f["estado"]])
        sub = sum(f["debe"] for f in lista)
        total_debe += sub
        data.append(["", "", "Subtotal", "", "", "", clp(sum(f["d"].total or 0 for f in lista)),
                     clp(sum(f["pagado"] for f in lista)), clp(sub), ""])
        t = Table(data, repeatRows=1, colWidths=[2 * cm, 1.2 * cm, 7 * cm, 1.8 * cm, 1.7 * cm, 3.2 * cm,
                                                 2.5 * cm, 2.5 * cm, 2.5 * cm, 2.2 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#5B4636")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8), ("ALIGN", (6, 0), (8, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E6DCCF")),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        el += [t, Spacer(1, 12)]
    el.append(Paragraph(f"<b>Total por cobrar: {clp(total_debe)}</b>", st["Heading2"]))
    doc.build(el)
    buf.seek(0)
    return buf


def _descarga(buf, nombre, formato):
    tipo = "application/pdf" if formato == "pdf" else \
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return StreamingResponse(buf, media_type=tipo,
                             headers={"Content-Disposition": f'attachment; filename="{nombre}.{formato}"'})


@router.get("/cobranza/exportar")
def exportar(formato: str = "xlsx", estado: str = "", vendedor: str = "", q: str = "", desde: str = "", hasta: str = ""):
    with db.Session() as s:
        lista = filas(s, estado, vendedor, q, desde, hasta)
    titulo = "Cobranza" + (f" · {estado}" if estado else "") + (f" · {vendedor}" if vendedor else "")
    buf = _pdf(titulo, [("", lista)]) if formato == "pdf" else _xlsx(titulo, [("", lista)])
    return _descarga(buf, f"cobranza_{date.today():%Y-%m-%d}", formato)


@router.get("/cobranza/reporte")
def reporte(vendedor: str = "", formato: str = "pdf"):
    """Deudas por cliente para un vendedor (o todos): lo que se le manda a cada vendedor."""
    with db.Session() as s:
        lista = [f for f in filas(s, vendedor=vendedor) if f["debe"] > 0]
    grupos = {}
    for f in sorted(lista, key=lambda f: (f["d"].cliente or "", f["d"].fecha or date.min)):
        grupos.setdefault(f["d"].cliente or "sin cliente", []).append(f)
    orden = sorted(grupos.items(), key=lambda g: -sum(x["debe"] for x in g[1]))
    titulo = f"Deudas por cliente — {vendedor.title() if vendedor else 'todos los vendedores'}"
    buf = _pdf(titulo, orden) if formato == "pdf" else _xlsx(titulo, orden)
    return _descarga(buf, f"deudas_{vendedor or 'todos'}_{date.today():%Y-%m-%d}", formato)
