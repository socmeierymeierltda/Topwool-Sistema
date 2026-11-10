"""Cobranza: lectura de documentos (factura, boleta, pago electrónico) y etiquetas de despacho,
unión con clientes (aprende RUT → cliente) y cálculo de deudas."""
import io
import re
import unicodedata
from datetime import date, datetime

from pypdf import PdfReader
from sqlalchemy import (Column, Date, DateTime, ForeignKey, Integer, String, Text,
                        select)

import db

GENERICOS = {"11111111-1", "66666666-6"}


# ---------------- tablas ----------------
class Documento(db.Base):
    __tablename__ = "documentos"
    id = Column(Integer, primary_key=True)
    tipo = Column(String(20))                 # factura | boleta | cv | nc | guia | importado
    folio = Column(Integer, index=True)
    fecha = Column(Date, index=True)
    rut = Column(String(20), index=True)
    razon_social = Column(String(200))
    vendedor_doc = Column(String(120))
    vendedor = Column(String(40), index=True)
    comentario = Column(Text)
    cliente = Column(String(200), index=True)
    empresa = Column(String(60))
    tracking = Column(String(60))
    monto = Column(Integer, default=0)        # productos con IVA
    flete = Column(Integer, default=0)        # envío con IVA
    total = Column(Integer, default=0)
    detalle = Column(Text)
    origen = Column(String(20))               # pdf | planilla | manual
    archivo = Column(String(250))
    revisar = Column(String(200))             # motivo si el cliente no es seguro
    notificado = Column(DateTime)
    despachado = Column(DateTime)             # cuando el paquete se entregó al transporte
    creado = Column(DateTime, default=datetime.now)


class Abono(db.Base):
    __tablename__ = "abonos"
    id = Column(Integer, primary_key=True)
    documento_id = Column(Integer, ForeignKey("documentos.id", ondelete="CASCADE"), index=True)
    fecha = Column(Date)
    monto = Column(Integer)
    medio = Column(String(60))
    nota = Column(String(250))


class Despacho(db.Base):
    __tablename__ = "despachos"
    id = Column(Integer, primary_key=True)
    empresa = Column(String(30))
    tracking = Column(String(60), index=True)
    destinatario = Column(String(200))
    folio_ref = Column(Integer)
    fecha = Column(Date)
    documento_id = Column(Integer, index=True)
    archivo = Column(String(250))


class Alias(db.Base):
    """Lo que el sistema aprende: un RUT o razón social corresponde a tal cliente."""
    __tablename__ = "alias_clientes"
    clave = Column(String(220), primary_key=True)   # "rut:76295333-1" o "rs:sociedad soto ..."
    cliente = Column(String(200))


# ---------------- utilidades ----------------
def norm(t):
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", t).strip()


def rut_norm(r):
    return (r or "").replace(".", "").strip().upper()


def _pesos(t):
    try:
        return int(re.sub(r"[^\d]", "", t or "") or 0)
    except ValueError:
        return 0


def _fecha(t):
    for fmt in ("%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(t.strip(), fmt).date()
        except (ValueError, AttributeError):
            pass
    return None


def link_seguimiento(empresa, tracking, aj):
    if not tracking:
        return ""
    e = norm(empresa)
    if "fedex" in e:
        return aj["link_fedex"].replace("{n}", tracking)
    if "blue" in e:
        return aj["link_blue"].replace("{n}", tracking)
    if "starken" in e:
        return aj["link_starken"].replace("{n}", tracking)
    return ""


def vendedor_corto(nombre_doc, aj):
    n = norm(nombre_doc)
    for par in db.lista(aj["vendedores"]):
        if "=" in par:
            k, v = [x.strip() for x in par.split("=", 1)]
            if n.startswith(norm(k)):
                return v
    return n.split(" ")[0] if n else ""


EMPRESAS = [("fedex", "fedex"), ("bluex", "blue"), ("blue", "blue"), ("cacem", "cacem"),
            ("starken", "starken"), ("cruz del sur", "cruz del sur"), ("chilexpress", "chilexpress"),
            ("ret tco", "retiro temuco"), ("retiro", "retiro"), ("ret", "retiro"),
            ("entrega", "entrega"), ("vta cura", "mesón curacautín")]


def leer_comentario(com):
    """'lorena soto - fedex 879843455020' → (cliente, empresa, tracking)."""
    com = (com or "").strip()
    if not com:
        return "", "", ""
    partes = re.split(r"\s+-\s+", com, maxsplit=1)
    cliente = norm(partes[0])
    resto = norm(partes[1]) if len(partes) > 1 else ""
    if cliente.startswith("web ") and not cliente.startswith("web - "):
        cliente = "web - " + cliente[4:].strip()
    empresa = ""
    for k, v in EMPRESAS:
        if re.search(r"\b" + re.escape(k) + r"\b", resto):
            empresa = v
            break
    m = re.search(r"\b(\d[\d ]{7,}\d)\b", resto)
    tracking = m.group(1).replace(" ", "") if m else ""
    return cliente, empresa, tracking


# ---------------- lectura de PDF ----------------
TIPOS = {"factura electronica": "factura", "boleta electronica": "boleta", "pago electronico": "cv",
         "nota de credito electronica": "nc", "guia de despacho electronica": "guia",
         "factura no afecta o exenta electronica": "factura"}


def _despues(lineas, etiqueta, n=1):
    for i, l in enumerate(lineas):
        if l.strip() == etiqueta and i + n < len(lineas):
            return lineas[i + n].strip()
    return ""


def leer_pdf(contenido: bytes, archivo: str) -> dict:
    texto = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(contenido)).pages)
    lineas = [l.rstrip() for l in texto.splitlines()]
    plano = norm(texto)

    # --- etiqueta FedEx
    if "trk#" in plano and "inv:" in plano:
        trk = next((l.strip() for l in lineas if re.fullmatch(r"\d{4} \d{4} \d{4}", l.strip())), "")
        inv = re.search(r"INV:\s*(\d+)", texto)
        dest = next((l.strip()[3:] for l in lineas if l.startswith("TO ")), "")
        f = re.search(r"SHIP DATE:\s*(\d{2})([A-Z]{3})(\d{2})", texto)
        fecha = None
        if f:
            meses = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
            if f.group(2) in meses:
                fecha = date(2000 + int(f.group(3)), meses.index(f.group(2)) + 1, int(f.group(1)))
        return {"clase": "despacho", "empresa": "fedex", "tracking": trk.replace(" ", ""),
                "folio_ref": int(inv.group(1)) if inv else None, "destinatario": norm(dest),
                "fecha": fecha, "archivo": archivo}

    # --- etiqueta Blue Express
    if "enviar a:" in plano and ("bluex" in plano or "be pdf" in plano):
        trk = next((l.strip() for l in lineas if re.fullmatch(r"\d{10}", l.strip())), "")
        dest = re.search(r"ENVIAR A:\s*(.+)", texto)
        f = re.search(r"(\d{2}/\d{2}/\d{4})", texto)
        return {"clase": "despacho", "empresa": "blue", "tracking": trk, "folio_ref": None,
                "destinatario": norm(dest.group(1)) if dest else "", "fecha": _fecha(f.group(1)) if f else None,
                "archivo": archivo}

    # --- documento de venta
    tipo = next((v for k, v in TIPOS.items() if k in plano), None)
    folio = re.search(r"Folio N°\s*(\d+)", texto)
    if not tipo or not folio:
        return {"clase": "desconocido", "archivo": archivo}
    rut = ""
    for i, l in enumerate(lineas):
        if l.strip() == "R.U.T" and i + 1 < len(lineas):
            rut = rut_norm(lineas[i + 1])
            break
    vend = []
    for i, l in enumerate(lineas):
        if l.strip() == "Vendedor":
            for x in lineas[i + 1:i + 5]:
                x = x.replace("Moneda de ingreso", "").strip()
                if not x or x.startswith(("Moneda", "PESO")):
                    break
                vend.append(x.replace("...", ""))
            break
    com = []
    for i, l in enumerate(lineas):
        if l.strip() == "Comentario":
            for x in lineas[i + 1:i + 4]:
                if x.strip().startswith(("Timbre", "Res. 80", "Total")):
                    break
                com.append(x.strip())
            break
    total = re.search(r"^Total \$ ([\d.]+)\s*$", texto, re.M)
    total = _pesos(total.group(1)) if total else 0
    items = [l.strip() for l in lineas if re.match(r"^\d+ \S+ .+\$ [\d.]+\s*$", l.strip())]
    envio = 0
    for l in items:
        if re.match(r"^\d+ ENVIO\b", l):
            envio += _pesos(re.findall(r"\$ ([\d.]+)", l)[-1])
    flete = round(envio * 1.19) if tipo == "factura" else envio
    comentario = " ".join(com).strip()
    if tipo == "nc":
        total, flete = -total, -flete
    return {"clase": "documento", "tipo": tipo, "folio": int(folio.group(1)),
            "fecha": _fecha(_despues(lineas, "Fecha Documento")), "rut": rut,
            "razon_social": _despues(lineas, "Señor (es)"), "vendedor_doc": " ".join(vend).strip(),
            "comentario": comentario, "total": total, "flete": flete, "monto": total - flete,
            "detalle": "\n".join(items), "archivo": archivo}


# ---------------- registro ----------------
def pagado(d: Documento, s) -> int:
    return sum(a.monto or 0 for a in s.scalars(select(Abono).where(Abono.documento_id == d.id)))


def estado(total, pagado_):
    if total <= 0:
        return "pagado"
    if pagado_ >= total:
        return "pagado"
    return "abonado" if pagado_ > 0 else "por pagar"


def aprender(s, rut, razon, cliente):
    if not cliente:
        return
    if rut and rut not in GENERICOS:
        s.merge(Alias(clave="rut:" + rut, cliente=cliente))
    if razon and norm(razon) not in ("cliente generico", "generico"):
        s.merge(Alias(clave="rs:" + norm(razon), cliente=cliente))


def registrar_documento(s, d: dict, aj) -> tuple:
    """Devuelve (Documento, nuevo?)."""
    ya = s.scalar(select(Documento).where(Documento.tipo == d["tipo"], Documento.folio == d["folio"]))
    if ya:
        return ya, False
    cliente, empresa, tracking = leer_comentario(d["comentario"])
    revisar = ""
    if cliente:
        aprender(s, d["rut"], d["razon_social"], cliente)
    else:
        al = (s.get(Alias, "rut:" + d["rut"]) if d["rut"] and d["rut"] not in GENERICOS else None) \
            or s.get(Alias, "rs:" + norm(d["razon_social"]))
        if al:
            cliente = al.cliente
        else:
            cliente = norm(d["razon_social"])
            revisar = "Sin comentario: cliente tomado de la razón social"
    if not empresa:
        empresa = "mesón curacautín" if not tracking else ""
    doc = Documento(tipo=d["tipo"], folio=d["folio"], fecha=d["fecha"], rut=d["rut"],
                    razon_social=d["razon_social"], vendedor_doc=d["vendedor_doc"],
                    vendedor=vendedor_corto(d["vendedor_doc"], aj), comentario=d["comentario"],
                    cliente=cliente, empresa=empresa, tracking=tracking, monto=d["monto"],
                    flete=d["flete"], total=d["total"], detalle=d["detalle"], origen="pdf",
                    archivo=d["archivo"], revisar=revisar)
    s.add(doc)
    s.flush()
    # pagado al emitir: pago electrónico (Flow), ventas web, o palabras clave del comentario
    com = norm(d["comentario"])
    if d["tipo"] == "cv" or cliente.startswith("web") or \
            any(re.search(r"\b" + re.escape(norm(p)) + r"\b", com) for p in db.lista(aj["palabras_pagado"])):
        s.add(Abono(documento_id=doc.id, fecha=d["fecha"] or date.today(), monto=d["total"],
                    medio="Flow / web" if (d["tipo"] == "cv" or cliente.startswith("web")) else "Al emitir",
                    nota="Automático al subir el documento"))
    return doc, True


def registrar_despacho(s, d: dict):
    """Devuelve (Despacho, nuevo?)."""
    ya = s.scalar(select(Despacho).where(Despacho.tracking == d["tracking"])) if d["tracking"] else None
    if ya:
        return ya, False
    des = Despacho(empresa=d["empresa"], tracking=d["tracking"], destinatario=d["destinatario"],
                   folio_ref=d["folio_ref"], fecha=d["fecha"], archivo=d["archivo"])
    s.add(des)
    s.flush()
    vincular_despacho(s, des)
    return des, True


def vincular_despacho(s, des: Despacho):
    doc = None
    if des.tracking:
        doc = s.scalar(select(Documento).where(Documento.tracking == des.tracking))
    if not doc and des.folio_ref:
        doc = s.scalar(select(Documento).where(Documento.folio == des.folio_ref, Documento.tipo == "factura")) \
            or s.scalar(select(Documento).where(Documento.folio == des.folio_ref))
    if not doc and des.destinatario:
        cands = list(s.scalars(select(Documento).where(Documento.tracking.is_(None) | (Documento.tracking == ""))
                               .order_by(Documento.fecha.desc()).limit(300)))
        for c in cands:
            nombre = (c.cliente or "").replace("web - ", "")
            if nombre and (nombre == des.destinatario or nombre in des.destinatario or des.destinatario in nombre):
                doc = c
                break
    if doc:
        des.documento_id = doc.id
        if not doc.tracking:
            doc.tracking = des.tracking
        if not doc.empresa or doc.empresa == "mesón curacautín":
            doc.empresa = des.empresa


def vincular_pendientes(s):
    for des in s.scalars(select(Despacho).where(Despacho.documento_id.is_(None))):
        vincular_despacho(s, des)


# ---------------- importación de la planilla Ventas antigua ----------------
def importar_planilla(s, contenido: bytes) -> int:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(contenido), data_only=True, read_only=True)
    ws = wb["Ventas"] if "Ventas" in wb.sheetnames else wb.worksheets[0]
    filas = list(ws.iter_rows(values_only=True))
    cab_i = next(i for i, r in enumerate(filas) if r and any(str(c or "").strip().upper() == "CLIENTE" for c in r))
    cab = [str(c or "").strip().upper() for c in filas[cab_i]]

    def col(r, nombre):
        return r[cab.index(nombre)] if nombre in cab and cab.index(nombre) < len(r) else None

    # vendedor por cliente desde la hoja "clientes" (para filas sin vendedor)
    enc = {}
    if "clientes" in wb.sheetnames:
        for r in wb["clientes"].iter_rows(min_row=2, values_only=True):
            if r and r[0] and len(r) > 1 and r[1]:
                enc[norm(str(r[0]))] = str(r[1]).strip().lower()
    previos = {(d.folio, d.cliente, d.total) for d in s.scalars(select(Documento).where(Documento.origen == "planilla"))}
    n = 0
    for r in filas[cab_i + 1:]:
        cliente = col(r, "CLIENTE")
        if not cliente or not str(cliente).strip():
            continue
        fecha = col(r, "FECHA")
        fecha = fecha.date() if isinstance(fecha, datetime) else fecha if isinstance(fecha, date) else None
        folio = col(r, "FOLIO")
        try:
            folio = int(float(folio)) if folio not in (None, "") else None
        except (TypeError, ValueError):
            folio = None
        num = lambda v: int(round(float(v))) if isinstance(v, (int, float)) else 0  # noqa: E731
        monto, flete = num(col(r, "MONTO")), num(col(r, "FLETE"))
        total = num(col(r, "TOTAL")) or monto + flete
        if (folio, norm(str(cliente)), total) in previos:   # ya importado antes
            continue
        doc_txt = str(col(r, "DOCUMENTO") or "").strip().lower()
        doc = Documento(tipo="importado" if doc_txt in ("", "#n/a") else doc_txt[:20], folio=folio, fecha=fecha,
                        cliente=norm(str(cliente)),
                        vendedor=(str(col(r, "VENDEDOR") or "").strip().lower().replace("#n/a", "")
                                  or enc.get(norm(str(cliente)), "")),
                        empresa=str(col(r, "EMPRESA") or "").strip().lower().replace("#n/a", ""),
                        comentario=str(col(r, "COMENTARIO") or "").strip(), monto=monto, flete=flete, total=total,
                        origen="planilla")
        s.add(doc)
        s.flush()
        pago = num(col(r, "MONTO PAGO"))
        if pago:
            s.add(Abono(documento_id=doc.id, fecha=fecha or date.today(), monto=pago, medio="Planilla anterior",
                        nota="Importado desde la planilla Ventas"))
        n += 1
    return n
