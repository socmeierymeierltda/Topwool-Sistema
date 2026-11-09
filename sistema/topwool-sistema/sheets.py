"""Escritura en Google Sheets (Drive de pedidos)."""
import json

import config
from logica import COLUMNAS

_gc = None


def _cliente():
    global _gc
    if _gc is None:
        import gspread
        from google.oauth2.service_account import Credentials
        info = json.loads(config.GOOGLE_CREDENTIALS_JSON)
        creds = Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        _gc = gspread.authorize(creds)
    return _gc


def correo_servicio():
    try:
        return json.loads(config.GOOGLE_CREDENTIALS_JSON).get("client_email", "")
    except Exception:
        return ""


def _hoja():
    return _cliente().open_by_key(config.PEDIDOS_SHEET_ID).worksheet(config.PEDIDOS_TAB)


def agregar_pedido(fila: dict) -> int:
    """Escribe en la primera fila vacía de la columna Cliente (C). Devuelve el número de fila."""
    if config.DEMO:
        return 0
    ws = _hoja()
    clientes = ws.col_values(3)
    n = len(clientes) + 1
    for i, v in enumerate(clientes[1:], start=2):   # primer hueco después del encabezado
        if not str(v).strip():
            n = i
            break
    valores = [fila[c] for c in COLUMNAS]
    ws.update(range_name=f"A{n}:L{n}", values=[valores], value_input_option="USER_ENTERED")
    return n


def quitar_comentario(fila_num: int, cliente: str, comentario: str) -> bool:
    """Borra el comentario 'por confirmar'. Busca la fila por cliente por si se movió."""
    if config.DEMO:
        return True
    ws = _hoja()
    candidatos = []
    if fila_num:
        candidatos.append(fila_num)
    candidatos += [c.row for c in ws.findall(cliente, in_column=3)]
    for r in candidatos:
        fila = ws.row_values(r)
        if len(fila) >= 3 and fila[2].strip().lower() == cliente.strip().lower() \
                and len(fila) >= 2 and fila[1].strip().upper() == comentario.strip().upper():
            ws.update_acell(f"B{r}", "")
            return True
    return False
