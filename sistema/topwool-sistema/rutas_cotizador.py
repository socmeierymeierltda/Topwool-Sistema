"""Cotizador: página pública para clientas (/cotizar) y administración (/cotizaciones)."""
import json
import re
from datetime import datetime

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import func, select

import cotizador as cz
import db
import inventario

router = APIRouter()
_ui = {}   # render, volver y tpl se inyectan desde main

CAMPOS = ("tipo_cliente", "nombre", "rut", "razon_social", "giro", "direccion", "comuna", "ciudad", "telefono", "correo")
OBLIGATORIOS = {"nombre": "nombre", "rut": "RUT", "razon_social": "razón social", "giro": "giro",
                "direccion": "dirección", "comuna": "comuna"}


# ---------- público ----------
@router.get("/cotizar", response_class=HTMLResponse)
def cotizar(request: Request, v: str = ""):
    aj = db.ajustes()
    vend = cz.vendedores(aj).get(v.strip().lower(), "")
    return _ui["tpl"].TemplateResponse(request, "t_cotizar.html", {
        "request": request, "vendedor": vend, "v": v.strip().lower() if vend else "",
        "productos": cz.catalogo_publico(), "reglas": cz.ajustes_cot(aj), "comentario": cz.COMENTARIO})


@router.post("/cotizar/calcular")
async def calcular(request: Request):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "datos inválidos"}, status_code=400)
    pedido = (body or {}).get("items") or []
    if not isinstance(pedido, list) or len(pedido) > 80:
        return JSONResponse({"error": "pedido inválido"}, status_code=400)
    return cz.calcular(pedido, db.ajustes())


@router.post("/cotizar/enviar")
async def enviar(request: Request):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "datos inválidos"}, status_code=400)
    datos = {k: re.sub(r"\s+", " ", str((body.get("cliente") or {}).get(k) or "")).strip()[:240] for k in CAMPOS}
    if datos["tipo_cliente"] not in ("natural", "empresa"):
        datos["tipo_cliente"] = "natural"
    faltan = [et for k, et in OBLIGATORIOS.items() if not datos[k]]
    if faltan:
        return JSONResponse({"error": "Falta completar: " + ", ".join(faltan)}, status_code=400)
    if not cz.rut_valido(datos["rut"]):
        return JSONResponse({"error": "El RUT no es válido. Revísalo, por favor."}, status_code=400)
    datos["rut"] = cz.rut_formato(datos["rut"])
    pedido = body.get("items") or []
    if not isinstance(pedido, list) or len(pedido) > 80:
        return JSONResponse({"error": "pedido inválido"}, status_code=400)
    aj = db.ajustes()
    calculo = cz.calcular(pedido, aj)
    if not calculo["lineas"]:
        return JSONResponse({"error": "Agrega al menos un producto a tu cotización."}, status_code=400)
    vend = cz.vendedores(aj).get(str(body.get("v") or "").strip().lower(), "")
    c = cz.guardar_cotizacion(datos, calculo, vend)
    return {"id": c.id, "pdf": f"/cotizar/c/{c.token}.pdf"}


@router.get("/cotizar/c/{token}.pdf")
def pdf_publico(token: str):
    with db.Session() as s:
        c = s.scalars(select(cz.Cotizacion).where(cz.Cotizacion.token == token)).first()
    if not c:
        return HTMLResponse("Cotización no encontrada", status_code=404)
    return _pdf(c)


def _pdf(c, descargar=True):
    nombre = re.sub(r"[^\w\- ]", "", (c.razon_social or c.nombre or "cliente"))[:40].strip()
    return Response(cz.pdf(c), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="Cotizacion {c.id} - {nombre}.pdf"'})


# ---------- administración (requiere clave) ----------
@router.get("/cotizaciones", response_class=HTMLResponse)
def lista(request: Request, estado: str = "nueva", vendedor: str = ""):
    aj = db.ajustes()
    with db.Session() as s:
        st = select(cz.Cotizacion).order_by(cz.Cotizacion.creada.desc())
        if estado:
            st = st.where(cz.Cotizacion.estado == estado)
        if vendedor:
            st = st.where(cz.Cotizacion.vendedor == vendedor)
        cots = list(s.scalars(st.limit(300)))
        conteo = dict(s.execute(select(cz.Cotizacion.estado, func.count()).group_by(cz.Cotizacion.estado)).all())
        n_prod = s.scalar(select(func.count()).select_from(cz.Producto)) or 0
        n_vis = s.scalar(select(func.count()).select_from(cz.Producto).where(cz.Producto.visible.is_(True))) or 0
        ult_precios = s.scalar(select(func.max(cz.Producto.actualizado)))
    base = str(request.base_url).rstrip("/").replace("http://", "https://")
    links = [(nombre, f"{base}/cotizar?v={cod}") for cod, nombre in cz.vendedores(aj).items()]
    return _ui["render"](request, "cotizaciones.html", cots=cots, estado=estado, vendedor=vendedor, conteo=conteo,
                         n_prod=n_prod, n_vis=n_vis, ult_precios=ult_precios, fecha_stock=cz.fecha_stock(),
                         links=[("General (sin vendedora)", f"{base}/cotizar")] + links,
                         vendedores=sorted(set(cz.vendedores(aj).values())))


@router.post("/cotizaciones/precios")
async def subir_precios(request: Request, archivo: UploadFile = File(...)):
    try:
        items = cz.leer_lista_precios(await archivo.read())
    except Exception as e:  # noqa: BLE001
        return _ui["volver"](request, "/cotizaciones", error=f"No pude leer la lista de precios: {e}")
    nuevos, cambiados = cz.guardar_lista(items)
    sin_det = sum(1 for i in items if i["detalle"] is None)
    sin_may = sum(1 for i in items if i["mayor"] is None)
    extra = ""
    if sin_det or sin_may:
        extra = f" Ojo: {sin_det} sin precio Base detalle y {sin_may} sin precio Base (se usa el otro)."
    return _ui["volver"](request, "/cotizaciones/productos",
                         aviso=f"Lista cargada: {len(items)} productos ({nuevos} nuevos, {cambiados} actualizados).{extra} "
                               "Revisa que el tipo (merino, corriedale, vellón natural o teñido), la familia y la unidad estén bien.")


@router.post("/cotizaciones/stock")
async def subir_stock(request: Request, archivo: UploadFile = File(...)):
    try:
        saldos = inventario.leer_inventario_defontana(await archivo.read())
    except Exception as e:  # noqa: BLE001
        return _ui["volver"](request, "/cotizaciones", error=f"No pude leer el inventario: {e}")
    cz.guardar_stock(saldos)
    return _ui["volver"](request, "/cotizaciones", aviso=f"Stock del cotizador actualizado: {len(saldos)} artículos.")


@router.get("/cotizaciones/productos", response_class=HTMLResponse)
def productos(request: Request, q: str = "", tipo: str = ""):
    with db.Session() as s:
        prods = list(s.scalars(select(cz.Producto).order_by(cz.Producto.nombre)))
        stock = {x.codigo: x.saldo for x in s.scalars(select(cz.Stock))}
    qn = cz.norm(q)
    if qn:
        prods = [p for p in prods if qn in cz.norm(p.nombre) or qn in cz.norm(p.codigo)]
    if tipo:
        prods = [p for p in prods if cz.tipo_normal(p.tipo) == tipo]
    return _ui["render"](request, "cot_productos.html", prods=prods, stock=stock, q=q, tipo=tipo,
                         tipos=cz.TIPOS, tipo_normal=cz.tipo_normal)


@router.post("/cotizaciones/productos")
async def guardar_productos(request: Request):
    form = await request.form()
    cods = form.getlist("cod")
    with db.Session() as s:
        for cod in cods:
            p = s.get(cz.Producto, cod)
            if not p:
                continue
            tipo = form.get(f"tipo_{cod}") or p.tipo
            unidad = form.get(f"unidad_{cod}") or p.unidad
            peso = cz._num(form.get(f"peso_{cod}")) or p.peso_kg or 1
            visible = form.get(f"vis_{cod}") == "1"
            fam = (form.get(f"fam_{cod}") or "").strip().upper()[:80]
            antes = (cz.tipo_normal(p.tipo), p.unidad, round(p.peso_kg or 1, 4), bool(p.visible), (p.familia or "").upper())
            if (tipo, unidad, round(peso, 4), visible, fam) != antes:
                p.tipo, p.unidad, p.peso_kg, p.visible, p.familia, p.editado = tipo, unidad, peso, visible, fam, True
        s.commit()
    vuelta = form.get("_volver") or "/cotizaciones/productos"
    return _ui["volver"](request, vuelta if vuelta.startswith("/cotizaciones") else "/cotizaciones/productos",
                         aviso="Productos guardados.")


@router.get("/cotizaciones/{cid}", response_class=HTMLResponse)
def ver(request: Request, cid: int):
    with db.Session() as s:
        c = s.get(cz.Cotizacion, cid)
    if not c:
        return _ui["volver"](request, "/cotizaciones", error="No encontré esa cotización.")
    lineas = json.loads(c.items or "[]")
    actual = cz.calcular([{"codigo": l["codigo"], "cantidad": l["cantidad"]} for l in lineas], db.ajustes())
    disp = {l["codigo"]: l["disponible"] for l in actual["lineas"]}
    texto = ", ".join(f"{cz._cant(l).lower().replace(' kg', 'k')} {l['nombre'].lower()}" for l in lineas)
    return _ui["render"](request, "cotizacion.html", c=c, lineas=lineas, disp=disp, texto=texto,
                         fecha_stock=cz.fecha_stock())


@router.get("/cotizaciones/{cid}/pdf")
def ver_pdf(cid: int):
    with db.Session() as s:
        c = s.get(cz.Cotizacion, cid)
    if not c:
        return HTMLResponse("No encontrada", status_code=404)
    return _pdf(c)


@router.post("/cotizaciones/{cid}")
async def actualizar(request: Request, cid: int):
    form = await request.form()
    with db.Session() as s:
        c = s.get(cz.Cotizacion, cid)
        if not c:
            return _ui["volver"](request, "/cotizaciones", error="No encontré esa cotización.")
        estado = form.get("estado")
        if estado in ("nueva", "en_defontana", "descartada"):
            c.estado = estado
        c.folio_defontana = (form.get("folio_defontana") or "").strip()[:40]
        c.nota = (form.get("nota") or "").strip()
        s.commit()
    return _ui["volver"](request, f"/cotizaciones/{cid}", aviso=f"Cotización {cid} guardada ({datetime.now():%H:%M}).")
