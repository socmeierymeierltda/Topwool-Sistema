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
}
