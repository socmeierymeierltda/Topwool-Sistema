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


def _num(v, defecto=None):
    try:
        return float(str(v).replace(",", ".")) if str(v).strip() != "" else defecto
    except ValueError:
        return defecto


def condiciones(aj):
    """Reglas para pasar del inventario real al stock que se muestra en la web."""
    reserva_cod = {}
    for par in db.lista(aj.get("inv_reserva_codigos", "")):
        if "=" in par:
            k, v = par.split("=", 1)
            reserva_cod[k.strip().upper()] = _num(v, 0)
    return {"reserva": _num(aj.get("inv_reserva"), 0), "reserva_cod": reserva_cod,
            "minimo": _num(aj.get("inv_minimo"), 0), "maximo": _num(aj.get("inv_maximo")),
            "no_tocar": [x.lower() for x in db.lista(aj.get("inv_no_tocar", ""))]}


def stock_web(saldo, factor, cod, nombre, c):
    """Devuelve (stock para la web, nota de la regla aplicada)."""
    notas = []
    reserva = c["reserva_cod"].get(cod, c["reserva"]) if c else 0
    if reserva:
        notas.append(f"reserva {reserva:g}")
    n = max(0, math.floor((saldo - reserva) / (factor or 1) + 1e-9))
    if c and c["minimo"] and 0 < n < c["minimo"]:
        notas.append(f"menos de {c['minimo']:g} → agotado")
        n = 0
    if c and c["maximo"] is not None and n > c["maximo"]:
        notas.append(f"tope {c['maximo']:g}")
        n = int(c["maximo"])
    return n, ", ".join(notas)


def no_tocar(cod, nombre, c):
    t = f"{cod} {nombre or ''}".lower()
    return bool(c) and any(x in t for x in c["no_tocar"])


def comparar(saldos: dict, catalogo: dict, aj=None):
    """Arma la tabla de diferencias entre Defontana y la web, aplicando las condiciones de Ajustes."""
    c = condiciones(aj) if aj else None
    with db.Session() as s:
        eqs = [e for e in s.scalars(select(db.Equivalencia)) if e.codigo_defontana]
    filas = []
    for e in eqs:
        cod = e.codigo_defontana.strip().upper()
        if cod not in saldos:
            continue
        factor = e.factor or 1
        deberia, regla = stock_web(saldos[cod], factor, cod, e.nombre_web, c)
        web = catalogo.get(e.woo_id, {})
        actual = web.get("stock")
        fijo = no_tocar(cod, e.nombre_web, c)
        filas.append({
            "woo_id": e.woo_id, "parent": web.get("parent", 0), "nombre": e.nombre_web,
            "produccion": e.nombre_produccion, "unidad": e.unidad, "codigo": cod,
            "saldo": saldos[cod], "factor": factor, "deberia": deberia, "actual": actual,
            "en_web": bool(web), "regla": regla, "no_tocar": fijo,
            "difiere": bool(web) and actual != deberia and not fijo,
        })
    # sin equivalencia: si el SKU de la web es igual al código de Defontana, se cruzan directo (1 a 1)
    cubiertos = {f["woo_id"] for f in filas}
    for wid, w in catalogo.items():
        cod = (w.get("sku") or "").strip().upper()
        if wid in cubiertos or not cod or cod not in saldos:
            continue
        deberia, regla = stock_web(saldos[cod], 1, cod, w.get("nombre"), c)
        fijo = no_tocar(cod, w.get("nombre"), c)
        filas.append({
            "woo_id": wid, "parent": w.get("parent", 0), "nombre": w.get("nombre"), "produccion": "(mismo SKU)",
            "unidad": "", "codigo": cod, "saldo": saldos[cod], "factor": 1, "deberia": deberia, "actual": w.get("stock"),
            "en_web": True, "regla": regla, "no_tocar": fijo, "difiere": w.get("stock") != deberia and not fijo,
        })
    filas.sort(key=lambda f: (not f["difiere"], f["nombre"] or ""))
    return filas
