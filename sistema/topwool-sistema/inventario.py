"""Lectura de archivos de Defontana y comparación de stock con la web."""
import io
import math

import openpyxl
from sqlalchemy import select

import db


def leer_inventario_defontana(contenido: bytes) -> dict:
    """Informe de Inventario de Defontana → {codigo: saldo total (todas las bodegas)}."""
    wb = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    saldos, encabezado = {}, None
    for r in ws.iter_rows(values_only=True):
        if not r:
            continue
        if encabezado is None:
            if r[0] and str(r[0]).strip().lower().startswith("código artículo"):
                encabezado = [str(x or "").strip().lower() for x in r]
            continue
        if not r[0]:
            continue   # filas "Total Artículo"
        cod = str(r[0]).strip().upper()
        i_saldo = encabezado.index("saldo") if "saldo" in encabezado else 4
        try:
            saldos[cod] = saldos.get(cod, 0) + float(r[i_saldo] or 0)
        except (TypeError, ValueError):
            pass
    if encabezado is None:
        raise ValueError("No encontré la fila 'Código Artículo' — ¿es el Informe de Inventario de Defontana?")
    return saldos


def comparar(saldos: dict, catalogo: dict):
    """Arma la tabla de diferencias entre Defontana y la web."""
    with db.Session() as s:
        eqs = [e for e in s.scalars(select(db.Equivalencia)) if e.codigo_defontana]
    filas = []
    for e in eqs:
        cod = e.codigo_defontana.strip().upper()
        if cod not in saldos:
            continue
        factor = e.factor or 1
        deberia = max(0, math.floor(saldos[cod] / factor + 1e-9))
        web = catalogo.get(e.woo_id, {})
        actual = web.get("stock")
        filas.append({
            "woo_id": e.woo_id, "parent": web.get("parent", 0), "nombre": e.nombre_web,
            "produccion": e.nombre_produccion, "unidad": e.unidad, "codigo": cod,
            "saldo": saldos[cod], "factor": factor, "deberia": deberia, "actual": actual,
            "en_web": bool(web),
            "difiere": web and actual != deberia,
        })
    filas.sort(key=lambda f: (not f["difiere"], f["nombre"] or ""))
    return filas
