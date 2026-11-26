"""Pantalla de descuentos masivos programados."""
import io
import json
from datetime import date

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import select

import cache_web
import cobranza as cb
import config
import db
import descuentos as ds

router = APIRouter()
_ui = {}


def _pagina(request, pcts=None, aviso_excel=""):
    cat = cache_web.asegurar()
    prods = ds.productos(cat["datos"] or {})
    with db.Session() as s:
        hist = list(s.scalars(select(ds.DescuentoProg).order_by(ds.DescuentoProg.creado.desc()).limit(30)))
    for h in hist:
        h.estado_txt = ds.estado_txt(h)
        h.prods = json.loads(h.detalle or "[]")
    cats = sorted({p["categoria"] for p in prods}, key=str.lower)
    return _ui["render"](request, "descuentos.html", prods=prods, cats=cats, cat=cat, hist=hist, pcts=pcts or {},
                         hoy=date.today(), aviso_excel=aviso_excel, pest="descuentos",
                         activas=sum(p["con_rebaja"] for p in prods))


@router.get("/descuentos", response_class=HTMLResponse)
def ver(request: Request, recargar: str = ""):
    if recargar:
        cache_web.asegurar(True)
    return _pagina(request)


@router.post("/descuentos/programar")
async def programar(request: Request):
    form = await request.form()
    volver = _ui["volver"]
    try:
        desde = date.fromisoformat(form.get("desde")) if form.get("desde") else None
        hasta = date.fromisoformat(form.get("hasta")) if form.get("hasta") else None
    except ValueError:
        return volver(request, "/descuentos", error="Revisa las fechas.")
    if desde and hasta and hasta < desde:
        return volver(request, "/descuentos", error="La fecha de término es anterior a la de inicio.")
    pcts = {}
    for k, v in form.items():
        if k.startswith("p_") and str(v).strip():
            try:
                pct = float(str(v).replace(",", ".").replace("%", ""))
            except ValueError:
                continue
            if 0 < pct < 95:
                pcts[int(k[2:])] = pct
    if not pcts:
        return volver(request, "/descuentos", error="No escribiste ningún % de descuento.")
    cat = cache_web.CATALOGO["datos"] or {}
    prods = ds.productos(cat)
    paso = int(form.get("redondeo") or 10)
    cambios, detalle = ds.calcular(prods, pcts, paso, desde, hasta)
    if not cambios:
        return volver(request, "/descuentos", error="No hubo precios que cambiar (¿productos sin precio?).")
    import woo
    try:
        n = woo.actualizar_rebajas(cambios)
    except Exception as e:  # noqa: BLE001
        return volver(request, "/descuentos", error=f"La web no aceptó el cambio: {str(e)[:200]}")
    for c in cambios:   # que la pantalla muestre lo nuevo sin volver a leer la web
        if c["id"] in cat:
            cat[c["id"]].update(rebaja=c["sale_price"], rebaja_desde=c["desde"] or "", rebaja_hasta=c["hasta"] or "")
    nombre = (form.get("nombre") or "").strip() or f"Descuento {date.today():%d-%m}"
    with db.Session() as s:
        ds.guardar(s, nombre, desde, hasta, detalle)
        s.commit()
    cuando = (f"del {desde:%d-%m} " if desde else "desde hoy ") + (f"al {hasta:%d-%m}" if hasta else "sin fecha de término")
    return volver(request, "/descuentos", aviso=f"«{nombre}» programado: {len(detalle)} productos ({n} colores) {cuando}."
                  + (" (modo demo: no se cambió la web)" if config.DEMO else ""))


@router.post("/descuentos/{did}/quitar")
def quitar(request: Request, did: int):
    import woo
    with db.Session() as s:
        d = s.get(ds.DescuentoProg, did)
        if not d:
            return _ui["volver"](request, "/descuentos")
        cambios = ds.quitar(json.loads(d.detalle or "[]"))
        try:
            woo.actualizar_rebajas(cambios)
        except Exception as e:  # noqa: BLE001
            return _ui["volver"](request, "/descuentos", error=f"La web no aceptó el cambio: {str(e)[:200]}")
        d.estado = "quitado"
        s.commit()
    cat = cache_web.CATALOGO["datos"] or {}
    for c in cambios:
        if c["id"] in cat:
            cat[c["id"]].update(rebaja=None, rebaja_desde="", rebaja_hasta="")
    return _ui["volver"](request, "/descuentos", aviso=f"Rebaja quitada de {len(cambios)} colores: vuelven a su precio normal.")


@router.post("/descuentos/quitar-todas")
def quitar_todas(request: Request):
    import woo
    cat = cache_web.CATALOGO["datos"] or {}
    cambios = [{"id": w["id"], "parent": w.get("parent") or 0, "sale_price": "", "desde": None, "hasta": None}
               for w in cat.values() if w.get("rebaja") or w.get("rebaja_desde")]
    if not cambios:
        return _ui["volver"](request, "/descuentos", aviso="No hay rebajas en la web.")
    try:
        woo.actualizar_rebajas(cambios)
    except Exception as e:  # noqa: BLE001
        return _ui["volver"](request, "/descuentos", error=f"La web no aceptó el cambio: {str(e)[:200]}")
    for c in cambios:
        cat[c["id"]].update(rebaja=None, rebaja_desde="", rebaja_hasta="")
    with db.Session() as s:
        for d in s.scalars(select(ds.DescuentoProg).where(ds.DescuentoProg.estado == "programado")):
            d.estado = "quitado"
        s.commit()
    return _ui["volver"](request, "/descuentos", aviso=f"Se quitaron todas las rebajas ({len(cambios)} productos/colores).")


@router.get("/descuentos/excel")
def excel():
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    prods = ds.productos(cache_web.CATALOGO["datos"] or {})
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Descuentos"
    ws.append(["ID web", "Categoría", "Producto", "Colores", "Precio desde", "Precio hasta", "% descuento"])
    for c in ws[1]:
        c.font = Font(name="Arial", bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="5B4636")
    amarillo = PatternFill("solid", fgColor="FFF6D6")
    for p in prods:
        ws.append([p["id"], p["categoria"], p["nombre"], p["n"], p["min"], p["max"], None])
        ws.cell(ws.max_row, 7).fill = amarillo
        for col in (5, 6):
            ws.cell(ws.max_row, col).number_format = '"$"#,##0'
    for col, ancho in zip("ABCDEFG", [9, 22, 40, 9, 13, 13, 14]):
        ws.column_dimensions[col].width = ancho
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="descuentos_{date.today():%Y%m%d}.xlsx"'})


@router.post("/descuentos/excel", response_class=HTMLResponse)
async def subir_excel(request: Request, archivo: UploadFile = File(...)):
    import openpyxl
    try:
        ws = openpyxl.load_workbook(io.BytesIO(await archivo.read()), data_only=True).worksheets[0]
    except Exception as e:  # noqa: BLE001
        return _ui["volver"](request, "/descuentos", error=f"No pude leer el Excel: {str(e)[:150]}")
    cab = [cb.norm(str(c.value or "")) for c in ws[1]]
    i_id = next((i for i, c in enumerate(cab) if c.startswith("id")), None)
    i_p = next((i for i, c in enumerate(cab) if "%" in c or c.startswith("descuento") or c == "% descuento"), None)
    cat = cache_web.CATALOGO["datos"] or {}
    if not cat:
        return _ui["volver"](request, "/descuentos", error="Espera a que cargue el catálogo de la web y vuelve a subir el archivo.")
    if i_id is None or i_p is None:
        return _ui["volver"](request, "/descuentos", error="El Excel debe tener las columnas «ID web» y «% descuento».")
    pcts = {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        try:
            pid, v = int(r[i_id]), r[i_p]
            if v in (None, ""):
                continue
            v = float(str(v).replace(",", ".").replace("%", ""))
            pcts[pid] = round(v * 100, 1) if 0 < v < 1 else v      # 0,2 en Excel con formato % = 20 %
        except (TypeError, ValueError):
            continue
    # ¿los ID son de colores (variantes) y no de productos? → descuento color por color
    if pcts and sum(1 for i in pcts if i in cat and (cat[i].get("parent") or cat[i].get("tipo") == "simple")) >= len(pcts) * 0.5 \
            and any(cat.get(i, {}).get("parent") for i in pcts):
        return _variantes(request, pcts)
    return _pagina(request, pcts, aviso_excel=f"Se cargaron {len(pcts)} descuentos desde el Excel. Revisa las fechas y aprieta «Programar descuentos».")


def _variantes(request, pcts, paso=1):
    cat = cache_web.CATALOGO["datos"] or {}
    filas, faltan = [], []
    for i, pct in pcts.items():
        w = cat.get(i)
        if not w or not w.get("precio"):
            faltan.append(i)
            continue
        nuevo = ds.redondear(w["precio"] * (1 - pct / 100), paso)
        filas.append({"id": i, "parent": w.get("parent") or 0, "producto": w.get("producto") or w["nombre"],
                      "color": w.get("color") or "", "precio": w["precio"], "pct": pct, "nuevo": nuevo,
                      "rebaja_actual": w.get("rebaja"), "estado": w.get("estado")})
    filas.sort(key=lambda f: (f["producto"].lower(), f["color"].lower()))
    return _ui["render"](request, "descuentos_variantes.html", filas=filas, faltan=faltan, hoy=date.today(), pest="descuentos",
                         datos=json.dumps({str(f["id"]): f["pct"] for f in filas}))


@router.post("/descuentos/programar-variantes")
async def programar_variantes(request: Request):
    form = await request.form()
    volver = _ui["volver"]
    try:
        desde = date.fromisoformat(form.get("desde")) if form.get("desde") else None
        hasta = date.fromisoformat(form.get("hasta")) if form.get("hasta") else None
    except ValueError:
        return volver(request, "/descuentos", error="Revisa las fechas.")
    if desde and hasta and hasta < desde:
        return volver(request, "/descuentos", error="La fecha de término es anterior a la de inicio.")
    pcts = {int(k): float(v) for k, v in json.loads(form.get("datos") or "{}").items()}
    paso = int(form.get("redondeo") or 1)
    cat = cache_web.CATALOGO["datos"] or {}
    cambios, por_prod = [], {}
    for i, pct in pcts.items():
        w = cat.get(i)
        if not w or not w.get("precio"):
            continue
        nuevo = ds.redondear(w["precio"] * (1 - pct / 100), paso)
        if 0 < nuevo < w["precio"]:
            par = w.get("parent") or 0
            cambios.append({"id": i, "parent": par, "sale_price": nuevo,
                            "desde": desde.isoformat() if desde else None, "hasta": hasta.isoformat() if hasta else None})
            d = por_prod.setdefault(par or i, {"id": par or i, "nombre": w.get("producto") or w["nombre"], "pcts": [], "items": []})
            d["pcts"].append(pct)
            d["items"].append([i, par])
    if not cambios:
        return volver(request, "/descuentos", error="No hubo precios que cambiar.")
    import woo
    try:
        n = woo.actualizar_rebajas(cambios)
    except Exception as e:  # noqa: BLE001
        return volver(request, "/descuentos", error=f"La web no aceptó el cambio: {str(e)[:200]}")
    for c in cambios:
        cat[c["id"]].update(rebaja=c["sale_price"], rebaja_desde=c["desde"] or "", rebaja_hasta=c["hasta"] or "")
    detalle = [{"id": d["id"], "nombre": d["nombre"], "pct": round(sum(d["pcts"]) / len(d["pcts"]), 1), "items": d["items"]}
               for d in por_prod.values()]
    nombre = (form.get("nombre") or "").strip() or f"Descuento {date.today():%d-%m}"
    with db.Session() as s:
        ds.guardar(s, nombre, desde, hasta, detalle)
        s.commit()
    cuando = (f"del {desde:%d-%m} " if desde else "desde hoy ") + (f"al {hasta:%d-%m}" if hasta else "sin fecha de término")
    return volver(request, "/descuentos", aviso=f"«{nombre}» programado: {n} colores de {len(detalle)} productos {cuando}."
                  + (" (modo demo: no se cambió la web)" if config.DEMO else ""))
