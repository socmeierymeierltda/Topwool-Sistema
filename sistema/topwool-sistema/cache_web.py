"""Catálogo de la web guardado en memoria por un rato (lo usan Stock rápido y Descuentos)."""
import threading
import traceback
from datetime import datetime, timedelta

import config

CATALOGO = {"datos": None, "fecha": None, "cargando": False, "error": ""}


def demo():
    out = {10: {"id": 10, "parent": 0, "nombre": "Malal", "sku": "", "stock": None, "maneja": False, "tipo": "variable",
                "estado": "publish", "precio": None, "rebaja": None, "rebaja_desde": "", "rebaja_hasta": "", "categoria": "Lanas"},
           20: {"id": 20, "parent": 0, "nombre": "Merino Socks", "sku": "", "stock": None, "maneja": False, "tipo": "variable",
                "estado": "publish", "precio": None, "rebaja": None, "rebaja_desde": "", "rebaja_hasta": "", "categoria": "Socks"},
           30: {"id": 30, "parent": 0, "nombre": "Kit Gorro Fácil", "sku": "KITGORRO", "stock": 8, "maneja": True, "tipo": "simple",
                "estado": "publish", "precio": 15990, "rebaja": None, "rebaja_desde": "", "rebaja_hasta": "", "categoria": "Kits"}}
    for i, (padre, color, sku, st, precio) in enumerate([(10, "Rojo", "CHIMAROJO100", 12, 4990), (10, "Mostaza", "100CHIMAMOSTAZA", 97, 4990),
                                                           (20, "Azul", "SOCKAZUL", 0, 6990), (20, "Verde", "SOCKVERDE", 4, 7490)]):
        out[100 + i] = {"id": 100 + i, "parent": padre, "nombre": f"{out[padre]['nombre']} - {color}", "producto": out[padre]["nombre"],
                        "color": color, "sku": sku, "stock": st, "maneja": True, "tipo": "variation", "estado": "publish",
                        "precio": precio, "rebaja": None, "rebaja_desde": "", "rebaja_hasta": "", "categoria": out[padre]["categoria"]}
    return out


def cargar():
    import woo

    def correr():
        CATALOGO.update(cargando=True, error="")
        try:
            CATALOGO.update(datos=demo() if config.DEMO else woo.catalogo(), fecha=datetime.now())
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            CATALOGO["error"] = str(e)[:250]
        CATALOGO["cargando"] = False
    if not CATALOGO["cargando"]:
        threading.Thread(target=correr, daemon=True).start()


def asegurar(recargar=False, minutos=30):
    viejo = CATALOGO["fecha"] and CATALOGO["fecha"] < datetime.now() - timedelta(minutes=minutos)
    if recargar or CATALOGO["datos"] is None or viejo:
        cargar()
    return CATALOGO


def vendibles(cat):
    padres = {w["parent"] for w in cat.values() if w["parent"]}
    return sorted([w for w in cat.values() if w["id"] not in padres], key=lambda w: (w["nombre"] or "").lower())
