"""Envío a producción: pedidos con 'curacautin' y sin color → Excel carta horizontal (sin lugar,
comentario ni mail) → correo a la impresora de Curacautín → se pintan las filas en el Drive con el color de la tanda."""
import io
import json
import smtplib
from datetime import datetime
from email.message import EmailMessage

from sqlalchemy import Column, DateTime, Integer, LargeBinary, String, Text, select

import config
import db
import seguimiento as sg

# colores suaves que se van turnando en cada envío (distintos al anterior)
PALETA = ["#c9daf8", "#d9ead3", "#fff2cc", "#f4cccc", "#d9d2e9", "#fce5cd", "#d0e0e3", "#ead1dc",
          "#b6d7a8", "#ffe599", "#a4c2f4", "#ea9999"]


class EnvioProduccion(db.Base):
    __tablename__ = "envios_produccion"
    id = Column(Integer, primary_key=True)
    fecha = Column(DateTime, default=datetime.now)
    numero_dia = Column(Integer)
    color = Column(String(10))
    cantidad = Column(Integer)
    clientes = Column(Text)
    correo = Column(String(250))          # a quién se envió, o el error
    excel = Column(LargeBinary)


def listos(filas):
    return [f for f in filas if sg.es_curacautin(f["lugar"]) and not f["color"]]


def siguiente_color(s):
    ult = s.scalar(select(EnvioProduccion).order_by(EnvioProduccion.id.desc()))
    if not ult or ult.color not in PALETA:
        return PALETA[0]
    return PALETA[(PALETA.index(ult.color) + 1) % len(PALETA)]


def numero_del_dia(s):
    hoy = datetime.now().date()
    return 1 + sum(1 for e in s.scalars(select(EnvioProduccion)) if e.fecha and e.fecha.date() == hoy)


# Sin "Lugar de preparación", "Comentario" ni "Mail"
COLS = [("cliente", "Cliente", 24), ("pedido", "Pedido", 62), ("telefono", "Teléfono", 12),
        ("direccion", "Dirección", 32), ("comuna", "Comuna", 15), ("vendedor", "Encargado", 10),
        ("doc", "Documento", 14), ("empresa", "Empresa", 11)]


def excel(filas, titulo) -> bytes:
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.worksheet.properties import PageSetupProperties
    cols = [c for c in COLS if c[0] in ("cliente", "pedido") or any((f.get(c[0]) or "").strip() for f in filas)]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Producción"
    ws.append([titulo])
    ws["A1"].font = Font(name="Arial", bold=True, size=13)
    ws.append([c[1] for c in cols])
    linea = Side(style="thin", color="999999")
    for c in ws[2]:
        c.font = Font(name="Arial", bold=True, color="FFFFFF", size=10)
        c.fill = PatternFill("solid", fgColor="5B4636")
        c.alignment = Alignment(vertical="center", wrap_text=True)
        c.border = Border(top=linea, bottom=linea, left=linea, right=linea)
    for f in filas:
        ws.append([f.get(c[0], "") for c in cols])
        for c in ws[ws.max_row]:
            c.font = Font(name="Arial", size=10)
            c.alignment = Alignment(vertical="top", wrap_text=True)
            c.border = Border(top=linea, bottom=linea, left=linea, right=linea)
    for i, c in enumerate(cols, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = c[2]
    # carta horizontal, todo el ancho en una página
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_LETTER
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_margins.left = ws.page_margins.right = 0.3
    ws.page_margins.top = ws.page_margins.bottom = 0.4
    ws.print_title_rows = "2:2"
    ws.print_options.horizontalCentered = True
    ws.freeze_panes = "A3"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def enviar_correo(destino, asunto, cuerpo, adjunto, nombre):
    if not (config.SMTP_USER and config.SMTP_PASSWORD):
        raise RuntimeError("falta configurar el correo (SMTP_USER y SMTP_PASSWORD en Render)")
    msg = EmailMessage()
    msg["From"] = config.SMTP_USER
    msg["To"] = destino
    msg["Subject"] = asunto
    msg.set_content(cuerpo)
    msg.add_attachment(adjunto, maintype="application",
                       subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=nombre)
    with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
        smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.send_message(msg)


def pintar(filas_num, color_hex):
    import sheets
    ws = sheets._hoja()
    r, g, b = (int(color_hex[i:i + 2], 16) / 255 for i in (1, 3, 5))
    ws.batch_format([{"range": f"A{n}:L{n}", "format": {"backgroundColor": {"red": r, "green": g, "blue": b}}}
                     for n in filas_num])


def resumen_clientes(filas):
    return json.dumps([f["cliente"] for f in filas], ensure_ascii=False)
