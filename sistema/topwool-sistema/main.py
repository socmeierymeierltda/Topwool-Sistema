"""Sistema Topwool — etapa 1: pedidos web → Drive de pedidos, equivalencias e inventario."""
import io
import traceback
from datetime import datetime
from pathlib import Path

import openpyxl
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, func, select
from starlette.middleware.sessions import SessionMiddleware

import cobranza  # noqa: F401  (registra sus tablas)
import seguimiento  # noqa: F401
import produccion  # noqa: F401
import informes  # noqa: F401
import config, db, inventario, logica, sheets, woo
import rutas_cobranza

BASE = Path(__file__).parent
app = FastAPI(title="Sistema Topwool")
tpl = Jinja2Templates(directory=BASE)
tpl.env.globals["zip"] = zip
tpl.env.filters["clp"] = lambda v: "$" + f"{float(v or 0):,.0f}".replace(",", ".")
tpl.env.filters["num"] = lambda v: "" if v is None else (f"{v:,.2f}".rstrip("0").rstrip(".").replace(",", "X").replace(".", ",").replace("X", ".") if isinstance(v, float) else v)

ULTIMA_COMPARACION = {"filas": [], "fecha": None, "archivo": "", "trabajo": {}}


@app.on_event("startup")
def _inicio():
    db.init()


def _logueado(request: Request):
    return request.session.get("ok") is True


def render(request, nombre, **ctx):
    nombre = "t_" + nombre
    ctx.update(request=request, demo=config.DEMO, seccion=nombre[2:].split(".")[0],
               aviso=request.session.pop("aviso", None), error=request.session.pop("error", None))
    return tpl.TemplateResponse(request, nombre, ctx)


def volver(request, url, aviso=None, error=None):
    if aviso:
        request.session["aviso"] = aviso
    if error:
        request.session["error"] = error
    return RedirectResponse(url, status_code=303)


@app.middleware("http")
async def proteger(request: Request, call_next):
    libre = request.url.path.startswith(("/login", "/static", "/salud"))
    if not libre and request.session.get("ok") is not True:
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)


# Se agrega después para que quede por fuera y la sesión esté disponible
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, max_age=60 * 60 * 24 * 14)


@app.get("/static/estilo.css")
def estilo():
    return FileResponse(BASE / "estilo.css", media_type="text/css")


@app.get("/static/{nombre}")
def estaticos(nombre: str):
    """Ícono y manifiesto para instalar el sistema en el teléfono."""
    tipos = {"icono-192.png": "image/png", "icono-512.png": "image/png", "icono-180.png": "image/png",
             "manifest.json": "application/manifest+json", "chart.umd.js": "text/javascript"}
    if nombre not in tipos:
        return HTMLResponse("No encontrado", status_code=404)
    return FileResponse(BASE / nombre, media_type=tipos[nombre])


# ---------- acceso ----------
@app.get("/salud")
def salud():
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return render(request, "login.html")


@app.post("/login")
def login(request: Request, clave: str = Form(...)):
    if clave == config.APP_PASSWORD:
        request.session["ok"] = True
        return RedirectResponse("/", status_code=303)
    return volver(request, "/login", error="Clave incorrecta")


@app.get("/salir")
def salir(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/")
def inicio():
    return RedirectResponse("/pedidos", status_code=303)


# ---------- pedidos web ----------
@app.get("/pedidos", response_class=HTMLResponse)
def pedidos(request: Request):
    aj = db.ajustes()
    error_woo = None
    try:
        ordenes = woo.pedidos(db.lista(aj["estados_woo"]))
    except Exception as e:  # noqa: BLE001
        ordenes, error_woo = [], f"No pude leer los pedidos de la web: {e}"
    por_id, por_sku = logica.mapa_equivalencias()
    with db.Session() as s:
        guardados = {p.woo_id: p for p in s.scalars(select(db.PedidoWeb))}
        por_confirmar = list(s.scalars(select(db.PedidoWeb).where(db.PedidoWeb.estado == "por_confirmar")
                                       .order_by(db.PedidoWeb.enviado.desc())))
        recientes = list(s.scalars(select(db.PedidoWeb).where(db.PedidoWeb.estado.in_(["en_drive", "descartado", "anulado"]))
                                   .order_by(db.PedidoWeb.enviado.desc()).limit(15)))
        n_eq = s.scalar(select(func.count()).select_from(db.Equivalencia))
    nuevos = [logica.preparar(o, aj, por_id, por_sku) for o in ordenes
              if o["id"] not in guardados or guardados[o["id"]].estado == "nuevo"]
    return render(request, "pedidos.html", nuevos=nuevos, por_confirmar=por_confirmar, recientes=recientes,
                  empresas=db.lista(aj["empresas"]), error_woo=error_woo, n_eq=n_eq,
                  columnas=logica.COLUMNAS, encabezados=logica.ENCABEZADOS)


@app.post("/pedidos/{woo_id}/enviar")
async def enviar(request: Request, woo_id: int):
    form = await request.form()
    fila = {c: (form.get(c) or "").strip() for c in logica.COLUMNAS}
    pago = form.get("_pago", "otro")
    try:
        n = sheets.agregar_pedido(fila)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return volver(request, "/pedidos", error=f"No se pudo escribir en el Drive. {sheets.error_claro(e)}")
    # Las transferencias solo llegan aquí cuando el pago ya fue confirmado
    estado = "en_drive"
    with db.Session() as s:
        p = s.scalar(select(db.PedidoWeb).where(db.PedidoWeb.woo_id == woo_id)) or db.PedidoWeb(woo_id=woo_id)
        p.numero = form.get("_numero")
        p.cliente = fila["Cliente"]
        p.pago = pago
        p.total = float(form.get("_total") or 0)
        p.estado = estado
        p.empresa = fila["Empresa"]
        p.pedido_texto = fila["Pedido"]
        p.fila_drive = n
        p.enviado = db.ahora()
        s.add(p)
        s.commit()
    try:
        woo.agregar_nota(woo_id, f"Ingresado al Drive de pedidos (fila {n})" + (" — transferencia confirmada" if pago == "transferencia" else ""))
    except Exception:  # noqa: BLE001
        pass
    if pago == "transferencia":
        try:   # pasa de "En espera" a "Procesando" (WooCommerce avisa al cliente)
            woo.cambiar_estado(woo_id, "processing")
        except Exception:  # noqa: BLE001
            pass
    msg = f"Pedido #{form.get('_numero')} ingresado al Drive" + (f" en la fila {n}" if n else " (modo demo: no se escribió)")
    if pago == "transferencia":
        msg += " con la transferencia confirmada."
    return volver(request, "/pedidos", aviso=msg)


@app.post("/pedidos/{woo_id}/confirmar")
def confirmar(request: Request, woo_id: int):
    aj = db.ajustes()
    with db.Session() as s:
        p = s.scalar(select(db.PedidoWeb).where(db.PedidoWeb.woo_id == woo_id))
        if not p:
            return volver(request, "/pedidos", error="No encontré ese pedido")
        try:
            ok = sheets.quitar_comentario(p.fila_drive, p.cliente, aj["comentario_transferencia"])
        except Exception as e:  # noqa: BLE001
            return volver(request, "/pedidos", error=f"No se pudo actualizar el Drive. {sheets.error_claro(e)}")
        p.estado = "en_drive"
        p.confirmado = db.ahora()
        s.commit()
    try:
        woo.agregar_nota(woo_id, "Transferencia confirmada por finanzas")
    except Exception:  # noqa: BLE001
        pass
    if not ok:
        return volver(request, "/pedidos", aviso=f"Pago de #{p.numero} confirmado. Ojo: no encontré la fila en el Drive para borrar el comentario; bórralo a mano.")
    return volver(request, "/pedidos", aviso=f"Pago de #{p.numero} confirmado y comentario quitado del Drive.")


@app.post("/pedidos/{woo_id}/anular")
async def anular(request: Request, woo_id: int):
    """Pedido que nunca se pagó: se borra del Drive (si estaba) y se cancela en la web."""
    form = await request.form()
    destino = form.get("_volver") or "/pedidos"
    aj = db.ajustes()
    borrada = None
    with db.Session() as s:
        p = s.scalar(select(db.PedidoWeb).where(db.PedidoWeb.woo_id == woo_id)) or db.PedidoWeb(woo_id=woo_id)
        if p.estado == "por_confirmar":
            try:
                borrada = sheets.borrar_pedido(p.fila_drive, p.cliente, aj["comentario_transferencia"])
            except Exception as e:  # noqa: BLE001
                return volver(request, destino, error=f"No pude borrar la fila del Drive. {sheets.error_claro(e)}")
        p.numero = p.numero or form.get("_numero")
        p.cliente = p.cliente or form.get("_cliente") or form.get("Cliente")
        p.estado = "anulado"
        p.enviado = p.enviado or db.ahora()
        s.add(p)
        s.commit()
        numero = p.numero
    msg = f"Pedido #{numero} anulado"
    if form.get("cancelar_web"):
        try:
            woo.cancelar(woo_id, "Anulado desde el sistema: no se recibió el pago")
            msg += " y cancelado en la web (el stock vuelve solo)"
        except Exception as e:  # noqa: BLE001
            return volver(request, destino, error=f"{msg}, pero la web no aceptó cancelarlo: {e}")
    if borrada is False:
        msg += ". Ojo: no encontré su fila en el Drive, bórrala a mano si sigue ahí"
    elif borrada:
        msg += " y su fila se borró del Drive"
    return volver(request, destino, aviso=msg + ".")


@app.post("/pedidos/{woo_id}/contactado")
async def marcar_contactado(request: Request, woo_id: int):
    form = await request.form()
    with db.Session() as s:
        p = s.scalar(select(db.PedidoWeb).where(db.PedidoWeb.woo_id == woo_id))
        if not p:
            p = db.PedidoWeb(woo_id=woo_id, numero=form.get("_numero"), cliente=form.get("_cliente"), estado="nuevo")
        p.contactado = db.ahora()
        s.add(p)
        s.commit()
    return volver(request, form.get("_volver") or "/sin-pagar", aviso=f"Pedido #{form.get('_numero')} marcado como contactado.")


# ---------- pedidos sin pagar ----------
def _correo(order, aj, por_id, por_sku):
    from urllib.parse import quote
    prep = logica.preparar(order, aj, por_id, por_sku)
    bi = order.get("billing") or {}
    datos = {"nombre": (bi.get("first_name") or "").strip().title() or "hola",
             "numero": order.get("number"), "total": tpl.env.filters["clp"](order.get("total")),
             "productos": ", ".join(f"{int(float(i.get('quantity', 1)))} x {i.get('name')}" for i in order.get("line_items", []))}
    try:
        asunto = aj["correo_asunto"].format(**datos)
        cuerpo = aj["correo_cuerpo"].replace("\\n", "\n").format(**datos)
    except (KeyError, IndexError, ValueError):
        asunto, cuerpo = aj["correo_asunto"], aj["correo_cuerpo"]
    para = bi.get("email", "")
    return {
        "gmail": "https://mail.google.com/mail/?" + (f"authuser={quote(aj['correo_remitente'])}&" if aj.get("correo_remitente") else "")
                 + f"view=cm&fs=1&to={quote(para)}&su={quote(asunto)}&body={quote(cuerpo)}",
        "mailto": f"mailto:{para}?subject={quote(asunto)}&body={quote(cuerpo)}",
        "para": para, "prep": prep,
    }


@app.get("/sin-pagar", response_class=HTMLResponse)
def sin_pagar(request: Request):
    aj = db.ajustes()
    error_woo = None
    try:
        ordenes = woo.pedidos(db.lista(aj["estados_sin_pagar"]))
    except Exception as e:  # noqa: BLE001
        ordenes, error_woo = [], f"No pude leer los pedidos de la web: {e}"
    if config.DEMO:
        ordenes = [o for o in ordenes if o.get("status") in ("on-hold", "pending")]
    por_id, por_sku = logica.mapa_equivalencias()
    with db.Session() as s:
        guardados = {p.woo_id: p for p in s.scalars(select(db.PedidoWeb))}
    hoy = datetime.now()
    filas = []
    for o in ordenes:
        g = guardados.get(o["id"])
        if g and g.estado in ("anulado", "descartado", "en_drive"):
            continue
        try:
            creado = datetime.fromisoformat((o.get("date_created") or "")[:19])
            dias = (hoy - creado).days
        except ValueError:
            dias = None
        c = _correo(o, aj, por_id, por_sku)
        filas.append({"o": o, "dias": dias, "g": g, **c,
                      "pago": logica.tipo_pago(o, aj),
                      "cliente": c["prep"]["fila"]["Cliente"]})
    filas.sort(key=lambda f: -(f["dias"] or 0))
    return render(request, "sinpagar.html", filas=filas, error_woo=error_woo)


@app.post("/pedidos/{woo_id}/descartar")
async def descartar(request: Request, woo_id: int):
    form = await request.form()
    with db.Session() as s:
        p = s.scalar(select(db.PedidoWeb).where(db.PedidoWeb.woo_id == woo_id)) or db.PedidoWeb(woo_id=woo_id)
        p.numero = form.get("_numero")
        p.cliente = form.get("Cliente")
        p.estado = "descartado"
        p.enviado = db.ahora()
        s.add(p)
        s.commit()
    return volver(request, "/pedidos", aviso="Pedido ocultado (no se envió al Drive).")


@app.post("/pedidos/{woo_id}/reabrir")
def reabrir(request: Request, woo_id: int):
    with db.Session() as s:
        s.execute(delete(db.PedidoWeb).where(db.PedidoWeb.woo_id == woo_id))
        s.commit()
    return volver(request, "/pedidos", aviso="El pedido vuelve a aparecer en 'Nuevos'.")


# ---------- equivalencias ----------
@app.get("/equivalencias", response_class=HTMLResponse)
def equivalencias(request: Request, q: str = "", estado: str = ""):
    with db.Session() as s:
        stmt = select(db.Equivalencia).order_by(db.Equivalencia.nombre_produccion)
        filas = list(s.scalars(stmt))
    if q:
        ql = q.lower()
        filas = [f for f in filas if ql in " ".join(str(x or "") for x in
                 (f.sku, f.nombre_web, f.nombre_produccion, f.codigo_defontana)).lower()]
    if estado:
        filas = [f for f in filas if (f.estado or "").startswith(estado)]
    return render(request, "equivalencias.html", filas=filas, q=q, estado=estado)


ALIAS = {"id web": "woo_id", "sku web": "sku", "nombre en la web": "nombre_web", "formato": "formato",
         "nombre producción": "nombre_produccion", "nombre produccion": "nombre_produccion", "unidad": "unidad",
         "código defontana": "codigo_defontana", "codigo defontana": "codigo_defontana", "factor": "factor",
         "estado": "estado"}


@app.post("/equivalencias/subir")
async def subir_equivalencias(request: Request, archivo: UploadFile = File(...)):
    try:
        wb = openpyxl.load_workbook(io.BytesIO(await archivo.read()), data_only=True, read_only=True)
        ws = wb["Equivalencias"] if "Equivalencias" in wb.sheetnames else wb.worksheets[0]
        filas = list(ws.iter_rows(values_only=True))
        cab = [ALIAS.get(str(c or "").strip().lower()) for c in filas[0]]
        if "woo_id" not in cab or "nombre_produccion" not in cab:
            raise ValueError("El archivo debe tener las columnas 'ID web' y 'Nombre producción'")
        nuevas = []
        for r in filas[1:]:
            d = {k: v for k, v in zip(cab, r) if k}
            if not d.get("woo_id"):
                continue
            d["woo_id"] = int(d["woo_id"])
            d["factor"] = float(d.get("factor") or 1)
            for k in ("sku", "nombre_web", "formato", "nombre_produccion", "unidad", "codigo_defontana", "estado"):
                d[k] = str(d.get(k) or "").strip()
            nuevas.append(db.Equivalencia(**d))
        with db.Session() as s:
            s.execute(delete(db.Equivalencia))
            s.add_all(nuevas)
            s.commit()
    except Exception as e:  # noqa: BLE001
        return volver(request, "/equivalencias", error=f"No pude leer el archivo: {e}")
    return volver(request, "/equivalencias", aviso=f"Se cargaron {len(nuevas)} equivalencias.")


@app.post("/equivalencias/{eid}")
async def editar_equivalencia(request: Request, eid: int):
    form = await request.form()
    with db.Session() as s:
        e = s.get(db.Equivalencia, eid)
        if e:
            e.nombre_produccion = form.get("nombre_produccion", e.nombre_produccion).strip()
            e.unidad = form.get("unidad", e.unidad).strip()
            e.codigo_defontana = form.get("codigo_defontana", e.codigo_defontana).strip().upper()
            e.estado = "Revisado"
            s.commit()
    return volver(request, f"/equivalencias?q={form.get('_q', '')}", aviso="Guardado.")


@app.get("/equivalencias/descargar")
def descargar_equivalencias():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Equivalencias"
    ws.append(["ID web", "SKU web", "Nombre en la web", "Formato", "Nombre producción", "Unidad",
               "Código Defontana", "Factor", "Estado"])
    with db.Session() as s:
        for e in s.scalars(select(db.Equivalencia).order_by(db.Equivalencia.nombre_produccion)):
            ws.append([e.woo_id, e.sku, e.nombre_web, e.formato, e.nombre_produccion, e.unidad,
                       e.codigo_defontana, e.factor, e.estado])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    nombre = f"equivalencias_{datetime.now():%Y-%m-%d}.xlsx"
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


# ---------- inventario ----------
@app.get("/inventario", response_class=HTMLResponse)
def ver_inventario(request: Request, solo: str = "dif"):
    filas = ULTIMA_COMPARACION["filas"]
    vista = [f for f in filas if f["difiere"]] if solo == "dif" else filas
    trabajo = dict(ULTIMA_COMPARACION.get("trabajo") or {})
    if trabajo.get("estado") in ("listo", "error") and not trabajo.get("mostrado"):
        ULTIMA_COMPARACION["trabajo"]["mostrado"] = True
        if trabajo["estado"] == "listo":
            request.session["aviso"] = trabajo.get("mensaje")
        else:
            request.session["error"] = trabajo.get("mensaje")
    return render(request, "inventario.html", filas=vista, total=len(filas),
                  n_dif=sum(1 for f in filas if f["difiere"]), solo=solo, trabajo=trabajo,
                  fecha=ULTIMA_COMPARACION["fecha"], archivo=ULTIMA_COMPARACION["archivo"])


def _en_segundo_plano(funcion):
    """Corre una tarea larga sin hacer esperar a la página (así Render no corta con error 502)."""
    import threading
    ULTIMA_COMPARACION["trabajo"] = {"estado": "procesando", "mensaje": "Empezando…", "inicio": datetime.now()}

    def avance(txt):
        ULTIMA_COMPARACION["trabajo"]["mensaje"] = txt

    def correr():
        try:
            msg = funcion(avance)
            ULTIMA_COMPARACION["trabajo"].update(estado="listo", mensaje=msg)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            ULTIMA_COMPARACION["trabajo"].update(estado="error", mensaje=f"No se pudo terminar: {str(e)[:250]}")
    threading.Thread(target=correr, daemon=True).start()


def _ocupado():
    return (ULTIMA_COMPARACION.get("trabajo") or {}).get("estado") == "procesando"


@app.post("/inventario/comparar")
async def comparar_inventario(request: Request, archivo: UploadFile = File(...)):
    if _ocupado():
        return volver(request, "/inventario", error="Ya hay una tarea en curso; espera a que termine.")
    try:
        saldos = inventario.leer_inventario_defontana(await archivo.read())
    except Exception as e:  # noqa: BLE001
        return volver(request, "/inventario", error=f"No pude leer el archivo de Defontana: {e}")
    nombre = archivo.filename

    def tarea(avance):
        avance(f"Leí {len(saldos)} artículos de Defontana. Leyendo el stock de la web…")
        catalogo = woo.catalogo(avance)
        ULTIMA_COMPARACION.update(filas=inventario.comparar(saldos, catalogo), fecha=datetime.now(), archivo=nombre)
        n_dif = sum(1 for f in ULTIMA_COMPARACION["filas"] if f["difiere"])
        return f"Comparación lista: {len(saldos)} artículos de Defontana, {n_dif} productos con diferencia."
    _en_segundo_plano(tarea)
    return RedirectResponse("/inventario", status_code=303)


@app.post("/inventario/actualizar")
async def actualizar_inventario(request: Request):
    if _ocupado():
        return volver(request, "/inventario", error="Ya hay una tarea en curso; espera a que termine.")
    form = await request.form()
    elegidos = {int(x) for x in form.getlist("ids")}
    cambios = [{"id": f["woo_id"], "parent": f["parent"], "stock": f["deberia"]}
               for f in ULTIMA_COMPARACION["filas"] if f["woo_id"] in elegidos and f["en_web"]]
    if not cambios:
        return volver(request, "/inventario", error="No elegiste productos para actualizar.")

    def tarea(avance):
        avance(f"Actualizando {len(cambios)} productos en la web…")
        n = woo.actualizar_stock(cambios)
        for f in ULTIMA_COMPARACION["filas"]:
            if f["woo_id"] in elegidos:
                f["actual"], f["difiere"] = f["deberia"], False
        extra = " (modo demo: no se cambió la web)" if config.DEMO else ""
        return f"Stock actualizado en {n} productos de la web{extra}."
    _en_segundo_plano(tarea)
    return RedirectResponse("/inventario", status_code=303)


# ---------- cobranza ----------
rutas_cobranza._ui.update(render=render, volver=volver)
app.include_router(rutas_cobranza.router)


# ---------- ajustes ----------
@app.get("/ajustes", response_class=HTMLResponse)
def ver_ajustes(request: Request):
    estado = {
        "WooCommerce": bool(config.WOO_KEY and config.WOO_SECRET),
        "Google (cuenta de servicio)": bool(config.GOOGLE_CREDENTIALS_JSON),
        "Drive de pedidos": bool(config.PEDIDOS_SHEET_ID),
        "Base de datos permanente": not config.DATABASE_URL.startswith("sqlite"),
        "Correo (envío a producción)": bool(config.SMTP_USER and config.SMTP_PASSWORD),
    }
    return render(request, "ajustes.html", aj=db.ajustes(), estado=estado, correo=sheets.correo_servicio())


@app.post("/ajustes")
async def guardar_ajustes(request: Request):
    form = await request.form()
    db.guardar_ajustes({k: v.replace("\r\n", "\n") for k, v in form.items()})
    return volver(request, "/ajustes", aviso="Ajustes guardados.")
