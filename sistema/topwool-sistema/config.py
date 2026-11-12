"""Configuración leída desde variables de entorno (en Render: Environment)."""
import os


def _env(name, default=""):
    return os.environ.get(name, default).strip()


# Acceso al sistema
APP_PASSWORD = _env("APP_PASSWORD", "topwool")          # cámbiala en Render
SECRET_KEY = _env("SECRET_KEY", "cambia-esta-clave-larga")

# Base de datos: en Render usa la "Internal Database URL" de Postgres.
# Sin DATABASE_URL se usa un archivo local (solo para pruebas).
DATABASE_URL = _env("DATABASE_URL", "sqlite:///./topwool.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

# WooCommerce (WordPress → WooCommerce → Ajustes → Avanzado → API REST)
WOO_URL = _env("WOO_URL", "https://www.topwool.cl")
WOO_KEY = _env("WOO_CONSUMER_KEY")
WOO_SECRET = _env("WOO_CONSUMER_SECRET")

# Google Sheets (cuenta de servicio; se comparte cada planilla con su correo)
GOOGLE_CREDENTIALS_JSON = _env("GOOGLE_CREDENTIALS_JSON")
PEDIDOS_SHEET_ID = _env("PEDIDOS_SHEET_ID")          # Drive "Pedidos Curacautín"
PEDIDOS_TAB = _env("PEDIDOS_TAB", "Pedidos")
COBRANZA_SHEET_ID = _env("COBRANZA_SHEET_ID")        # Drive "Ventas" (etapa 3)

# Correo (para enviar el Excel de producción). Con Gmail: contraseña de aplicación.
SMTP_HOST = _env("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(_env("SMTP_PORT", "465") or 465)
SMTP_USER = _env("SMTP_USER")
SMTP_PASSWORD = _env("SMTP_PASSWORD").replace(" ", "")

# Modo demo: muestra pedidos de ejemplo y no escribe en ningún lado
DEMO = _env("DEMO", "1" if not (WOO_KEY and WOO_SECRET) else "0") == "1"

# Reglas de los pedidos web (editables también desde la pantalla Ajustes)
DEFAULTS = {
    "encargado": "jesu",
    "lugar": "curacautin",
    "prefijo_cliente": "web - ",
    "doc_flow": "cv",
    "doc_transferencia": "boleta",
    "comentario_transferencia": "TRANSFERENCIA - POR CONFIRMAR",
    "empresas": "fedex, blue, cacem, starken, cruz del sur, retiro, entrega",
    "empresa_defecto": "fedex",
    # Palabras que identifican cada medio de pago en WooCommerce
    "pago_flow": "flow",
    "pago_transferencia": "bacs, transferencia",
    # Estados de WooCommerce que se consideran pedidos nuevos
    "estados_woo": "processing, on-hold",
    # Pedidos sin pagar y recordatorios
    "correo_remitente": "contacto@topwool.cl",
    "estados_sin_pagar": "pending, on-hold",
    # Envío a producción
    "correo_produccion": "socmeierymeierltda@gmail.com",
    # Cobranza
    "vendedores": "javier=javier, maria de los angeles=maria, mariajesus=jesu, manuel=manuel, cris=cris",
    "palabras_pagado": "efect, efectivo, pagado",
    # Cotizador: mínimos para precio por mayor (lista Base)
    "cot_kg_corriedale": "10", "cot_kg_merino": "5", "cot_kg_hilado_surtido": "10",
    "cot_kg_vellon_natural": "10", "cot_kg_tenido": "5", "cot_kg_tenido_surtido": "10",
    "cot_un_bolsa": "10", "cot_un_familia": "50", "cot_un_surtido": "100",
    "cot_lineas": ("HIMDK24.5, HIMDK, HIMSOCKS, HIMSLDK, HIMSF, HIMC, HIMF, HIMA, HIME, HIMMMA, HIMRM, "
                   "HICDK, HICDF, HICF, HILLAIMA, HILONQUIMAY, HIALGODON, HIR, HIL, HIP, HIT, HIA, HIE, "
                   "HAHM, HAC, HAF, HAH, HAP, COVT50, CO"),
    "link_fedex": "https://www.fedex.com/fedextrack/?trknbr={n}",
    "link_blue": "https://www.blue.cl/seguimiento/?n_seguimiento={n}",
    "link_starken": "https://www.starken.cl/seguimiento?codigo={n}",
    "aviso_despacho": ("¡Hola {nombre}! Tu pedido de Topwool ya va en camino 🚚\n"
                       "Empresa de transporte: {empresa}\nN° de seguimiento: {tracking}\n"
                       "Puedes seguirlo aquí: {link}\n\n¡Gracias por tejer con nosotros!"),
    "correo_asunto": "Tu pedido #{numero} en Topwool está esperando el pago",
    "correo_cuerpo": ("Hola {nombre}:\n\n"
                      "Vimos que tu pedido #{numero} en topwool.cl quedó pendiente de pago.\n\n"
                      "Productos: {productos}\nTotal: {total}\n\n"
                      "Si pagaste por transferencia, ¿nos puedes enviar el comprobante respondiendo este correo? "
                      "Si tuviste algún problema para pagar o quieres cambiar algo, cuéntanos y te ayudamos.\n\n"
                      "Te guardamos el pedido unos días más.\n\n"
                      "Un abrazo,\nEquipo Topwool\nwww.topwool.cl"),
}
