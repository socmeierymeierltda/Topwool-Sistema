"""Conciliación bancaria: se sube la cartola, se guardan los abonos y el sistema propone qué venta
de Cobranza paga cada transferencia. Al confirmar se registra el abono y se aprende quién es el depositante."""
import hashlib
import io
import re
from datetime import date, datetime, timedelta

from sqlalchemy import Column, Date, DateTime, Integer, String, Text, select

import cobranza as cb
import db

BANCOS = ["Banco de Chile", "BancoEstado", "Santander"]


class MovBanco(db.Base):
    __tablename__ = "movimientos_banco"
    id = Column(Integer, primary_key=True)
    clave = Column(String(40), unique=True, index=True)
    banco = Column(String(40))
    cuenta = Column(String(40))
    fecha = Column(Date, index=True)
    descripcion = Column(String(250))
    canal = Column(String(80))
    nro_doc = Column(String(40))
    monto = Column(Integer)
    saldo = Column(Integer)
    tipo = Column(String(30), index=True)       # transferencia | efectivo | transbank | flow | otro
    contraparte = Column(String(200))
    estado = Column(String(20), default="pendiente", index=True)   # pendiente | conciliado | ignorado
    nota = Column(Text)
    creado = Column(DateTime, default=datetime.now)


class MovDoc(db.Base):
    """Qué parte de un movimiento pagó qué documento (y el abono que se creó)."""
    __tablename__ = "movimientos_documentos"
    id = Column(Integer, primary_key=True)
    mov_id = Column(Integer, index=True)
    documento_id = Column(Integer, index=True)
    abono_id = Column(Integer)
    monto = Column(Integer)


# ---------------- lectura de cartolas ----------------
def _filas_excel(contenido: bytes, nombre: str):
    if nombre.lower().endswith(".xls"):
        import xlrd
        sh = xlrd.open_workbook(file_contents=contenido).sheet_by_index(0)
        return [sh.row_values(i) for i in range(sh.nrows)]
    import openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(contenido), data_only=True, read_only=True).worksheets[0]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _txt(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _monto(v):
    if v in (None, ""):
        return 0
    if isinstance(v, (int, float)):
        return int(round(v))
    t = re.sub(r"[^\d,-]", "", str(v)).replace(",", ".")
    try:
        return int(round(float(t))) if t else 0
    except ValueError:
        return 0


def _fecha(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    t = _txt(v)
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(t[:10], fmt).date()
        except ValueError:
            pass
    return None


def leer_cartola(contenido: bytes, nombre: str, banco: str) -> dict:
    """Lee la cartola buscando la fila de encabezados (Fecha, Descripción, Cargos, Abonos)."""
    filas = _filas_excel(contenido, nombre)
    cuenta = ""
    cab_i = None
    for i, r in enumerate(filas):
        celdas = [cb.norm(_txt(c)) for c in r]
        if any(c.startswith("cuenta") for c in celdas):
            vals = [_txt(c) for c in r if _txt(c) and not cb.norm(_txt(c)).startswith("cuenta")]
            cuenta = cuenta or (vals[0] if vals else "")
        if "fecha" in celdas and any("abono" in c for c in celdas) and any("descrip" in c for c in celdas):
            cab_i = i
            break
    if cab_i is None:
        raise ValueError("No encontré la tabla de movimientos (Fecha / Descripción / Abonos). ¿Es la cartola del banco elegido?")
    cab = [cb.norm(_txt(c)) for c in filas[cab_i]]

    def col(*palabras):
        return next((j for j, c in enumerate(cab) if any(p in c for p in palabras)), None)
    i_f, i_d, i_c, i_n = col("fecha"), col("descrip"), col("canal", "sucursal"), col("docto", "documento")
    i_ab, i_ca, i_s = col("abono"), col("cargo"), col("saldo")
    movs = []
    for r in filas[cab_i + 1:]:
        if i_f is None or i_f >= len(r):
            continue
        f = _fecha(r[i_f])
        if not f:
            continue
        movs.append({"fecha": f, "descripcion": _txt(r[i_d]) if i_d is not None else "",
                     "canal": _txt(r[i_c]) if i_c is not None else "", "nro_doc": _txt(r[i_n]) if i_n is not None else "",
                     "abono": _monto(r[i_ab]) if i_ab is not None else 0,
                     "cargo": _monto(r[i_ca]) if i_ca is not None else 0,
                     "saldo": _monto(r[i_s]) if i_s is not None else 0})
    return {"banco": banco, "cuenta": cuenta, "movimientos": movs}


def clasificar(desc: str, aj) -> tuple:
    """Devuelve (tipo, contraparte)."""
    d = desc or ""
    n = cb.norm(d)
    for par in db.lista(aj.get("ruts_conocidos", "")):     # "0768300143=flow"
        if "=" in par:
            rut, tipo = [x.strip() for x in par.split("=", 1)]
            if rut and rut in d.replace(".", "").replace("-", ""):
                return cb.norm(tipo), tipo.title()
    if "transbank" in n:
        return "transbank", "Transbank"
    if "flow" in n:
        return "flow", "Flow"
    if "efectivo" in n or n.startswith("depos"):
        return "efectivo", ""
    m = re.match(r"(?:app-)?(?:traspaso|transferencia|transf\.?)\s*(?:de|desde)\s*:?\s*(.+)", d, re.I)
    if m:
        return "transferencia", m.group(1).strip()
    if n.startswith("pago:"):
        return "otro", d.split(":", 1)[1].strip()
    return "otro", ""


def guardar(s, datos: dict, aj) -> dict:
    nuevos = repetidos = 0
    for m in datos["movimientos"]:
        if m["abono"] <= 0:
            continue      # los cargos (pagos de la empresa) no se concilian con cobranza
        clave = hashlib.sha1("|".join(str(x) for x in (datos["banco"], datos["cuenta"], m["fecha"], m["descripcion"],
                                                        m["abono"], m["saldo"])).encode()).hexdigest()
        if s.scalar(select(MovBanco).where(MovBanco.clave == clave)):
            repetidos += 1
            continue
        tipo, contra = clasificar(m["descripcion"], aj)
        s.add(MovBanco(clave=clave, banco=datos["banco"], cuenta=datos["cuenta"], fecha=m["fecha"],
                       descripcion=m["descripcion"][:250], canal=m["canal"][:80], nro_doc=m["nro_doc"][:40],
                       monto=m["abono"], saldo=m["saldo"], tipo=tipo, contraparte=contra[:200], estado="pendiente"))
        nuevos += 1
    return {"nuevos": nuevos, "repetidos": repetidos}


# ---------------- sugerencias ----------------
def deudas(s):
    """Documentos con saldo por cobrar: [(doc, debe)]."""
    from sqlalchemy import func
    pagos = dict(s.execute(select(cb.Abono.documento_id, func.coalesce(func.sum(cb.Abono.monto), 0))
                           .group_by(cb.Abono.documento_id)).all())
    out = []
    for d in s.scalars(select(cb.Documento).where(cb.Documento.total > 0)):
        debe = (d.total or 0) - int(pagos.get(d.id, 0))
        if debe > 0:
            out.append((d, debe))
    return out


def sugerencias(s, mov: MovBanco, pendientes, limite=4):
    """Propuestas ordenadas por puntaje: cada una es una lista de (doc, debe) que este pago cubriría."""
    import seguimiento as sg
    if mov.tipo not in ("transferencia", "otro") or not mov.contraparte:
        return []
    alias = s.get(cb.Alias, "dep:" + cb.norm(mov.contraparte))
    por_cliente = {}
    for d, debe in pendientes:
        if d.fecha and mov.fecha and d.fecha > mov.fecha + timedelta(days=3):
            continue
        por_cliente.setdefault(d.cliente or "", []).append((d, debe))
    props = []
    for cliente, docs in por_cliente.items():
        if alias and alias.cliente == cliente:
            nombre = 1.0
        else:
            nombre = max([sg.parecido(mov.contraparte, cliente)] +
                         [sg.parecido(mov.contraparte, d.razon_social or "") for d, _ in docs])
        if nombre < 0.7:
            continue
        docs = sorted(docs, key=lambda x: x[0].fecha or date.min)
        total_cliente = sum(x[1] for x in docs)
        # 1) un documento con el mismo monto  2) todos los del cliente suman el monto  3) abono parcial
        for d, debe in docs:
            if abs(debe - mov.monto) <= 2 or abs((d.total or 0) - mov.monto) <= 2:
                props.append((nombre * 0.6 + 0.4, [(d, debe)], "monto exacto"))
        if len(docs) > 1 and abs(total_cliente - mov.monto) <= 2:
            props.append((nombre * 0.6 + 0.38, docs, "paga todas sus deudas"))
        if not any(p[1][0][0].cliente == cliente for p in props):
            props.append((nombre * 0.6 + (0.15 if mov.monto < total_cliente else 0.05), docs, "abono a su deuda"))
    props.sort(key=lambda p: -p[0])
    return [{"puntaje": round(p[0] * 100), "docs": p[1], "motivo": p[2],
             "total": sum(x[1] for x in p[1])} for p in props[:limite]]


# ---------------- confirmar / ignorar / deshacer ----------------
def confirmar(s, mov: MovBanco, doc_ids: list):
    pend = {d.id: debe for d, debe in deudas(s)}
    resto = mov.monto
    usados = []
    for did in doc_ids:
        d = s.get(cb.Documento, did)
        if not d or resto <= 0:
            continue
        monto = min(resto, pend.get(did, 0)) if pend.get(did, 0) else 0
        if monto <= 0:
            continue
        a = cb.Abono(documento_id=did, fecha=mov.fecha, monto=monto,
                     medio=f"Transferencia {mov.banco}", nota=f"Conciliación: {mov.descripcion}"[:250])
        s.add(a)
        s.flush()
        s.add(MovDoc(mov_id=mov.id, documento_id=did, abono_id=a.id, monto=monto))
        usados.append(d)
        resto -= monto
    if not usados:
        raise ValueError("Esos documentos ya no tienen saldo por cobrar.")
    # aprender: este depositante es este cliente
    if mov.contraparte and len({d.cliente for d in usados}) == 1:
        s.merge(cb.Alias(clave="dep:" + cb.norm(mov.contraparte), cliente=usados[0].cliente))
    mov.estado = "conciliado"
    mov.nota = (f"Sobran ${resto:,.0f} (saldo a favor del cliente)".replace(",", ".") if resto > 0 else "")
    return usados, resto


def deshacer(s, mov: MovBanco):
    for md in s.scalars(select(MovDoc).where(MovDoc.mov_id == mov.id)):
        a = s.get(cb.Abono, md.abono_id) if md.abono_id else None
        if a:
            s.delete(a)
        s.delete(md)
    mov.estado = "pendiente"
    mov.nota = ""
