"""Seguimiento de pedidos del Drive: no listo → listo para producción → en producción (fila con color)
→ facturado (documento en Cobranza) → despachado (con número de seguimiento)."""
import difflib
import hashlib
from datetime import datetime, timedelta

from sqlalchemy import Column, Date, DateTime, Integer, String, Text, select

import cobranza as cb
import db


class FilaDrive(db.Base):
    """Recuerda cada pedido del Drive para saber desde cuándo está y en qué etapa va."""
    __tablename__ = "seguimiento_filas"
    clave = Column(String(40), primary_key=True)
    cliente = Column(String(200), index=True)
    pedido = Column(Text)
    lugar = Column(String(60))
    vendedor = Column(String(40))
    empresa = Column(String(60))
    color = Column(String(20))
    visto_desde = Column(DateTime)
    color_desde = Column(DateTime)
    documento_id = Column(Integer)
    ultima_vez = Column(DateTime)


def es_curacautin(lugar: str) -> bool:
    t = cb.norm(lugar).split(" ")[0] if lugar else ""
    return bool(t) and difflib.SequenceMatcher(None, t, "curacautin").ratio() >= 0.8


def _color(cell) -> str:
    bg = ((cell or {}).get("effectiveFormat") or {}).get("backgroundColor") or {}
    r, g, b = bg.get("red", 0), bg.get("green", 0), bg.get("blue", 0)
    if not bg or (r > 0.97 and g > 0.97 and b > 0.97):
        return ""                 # blanco o sin color
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


def filas_desde_grilla(row_data):
    """row_data de la API de Sheets (con formato) → lista de dicts. Fila 1 = encabezados."""
    out = []
    for i, row in enumerate(row_data[1:], start=2):
        vals = row.get("values", [])
        txt = [(v.get("formattedValue") or "").strip() for v in vals] + [""] * 12
        cliente = txt[2]
        if not cliente:
            continue
        colores = [_color(v) for v in vals[:12]]
        color = next((c for c in colores[2:4] if c), "") or next((c for c in colores if c), "")
        out.append({"fila": i, "documento": txt[0], "comentario": txt[1], "cliente": cliente, "pedido": txt[3],
                    "telefono": txt[4], "lugar": txt[5], "direccion": txt[6], "comuna": txt[7], "vendedor": txt[8],
                    "doc": txt[9], "empresa": txt[10], "mail": txt[11], "color": color})
    return out


def leer_drive():
    import config
    import sheets
    ss = sheets._cliente().open_by_key(sheets.id_planilla(config.PEDIDOS_SHEET_ID))
    meta = ss.fetch_sheet_metadata({
        "includeGridData": True, "ranges": [f"'{config.PEDIDOS_TAB}'!A1:L2000"],
        "fields": "sheets.data.rowData.values(formattedValue,effectiveFormat.backgroundColor)"})
    data = meta["sheets"][0].get("data", [{}])[0].get("rowData", [])
    return filas_desde_grilla(data)


def clave(f):
    return hashlib.sha1("|".join(cb.norm(f[k]) for k in ("cliente", "pedido")).encode()).hexdigest()


def actualizar(s, filas):
    """Guarda lo visto y une con documentos. Devuelve la lista con la etapa de cada pedido."""
    ahora = datetime.now()
    out = []
    usados = set()
    for f in filas:
        k = clave(f)
        r = s.get(FilaDrive, k)
        if not r:
            r = FilaDrive(clave=k, visto_desde=ahora)
            s.add(r)
        r.cliente, r.pedido, r.lugar = f["cliente"], f["pedido"], f["lugar"]
        r.vendedor, r.empresa, r.ultima_vez = cb.norm(f["vendedor"]), cb.norm(f["empresa"]), ahora
        if f["color"] and not r.color_desde:
            r.color_desde = ahora
        if not f["color"]:
            r.color_desde = None
        r.color = f["color"]
        doc = s.get(cb.Documento, r.documento_id) if r.documento_id else None
        if not doc and es_curacautin(f["lugar"]):
            nombre = cb.norm(f["cliente"])
            if nombre.startswith("web ") and not nombre.startswith("web - "):
                nombre = "web - " + nombre[4:]
            # documentos emitidos hasta 15 días antes de que el sistema viera el pedido (carga inicial)
            desde = ((r.visto_desde or ahora) - timedelta(days=15)).date()
            cands = s.scalars(select(cb.Documento).where(cb.Documento.cliente == nombre, cb.Documento.fecha >= desde)
                              .order_by(cb.Documento.fecha))
            doc = next((d for d in cands if d.id not in usados), None)
            if doc:
                r.documento_id = doc.id
        if doc:
            usados.add(doc.id)
        if not es_curacautin(f["lugar"]):
            etapa = "no listo"
        elif doc and doc.tracking:
            etapa = "despachado"
        elif doc:
            etapa = "facturado"
        elif f["color"]:
            etapa = "en producción"
        else:
            etapa = "listo para producción"
        desde = r.color_desde if etapa == "en producción" else r.visto_desde
        out.append({**f, "etapa": etapa, "doc": doc, "dias": (ahora - desde).days if desde else None,
                    "desde": desde})
    return out


ETAPAS = ["no listo", "listo para producción", "en producción", "facturado", "despachado"]
