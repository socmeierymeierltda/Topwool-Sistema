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
# Instagram: token de larga duración generado en Meta for Developers (el sistema lo renueva solo)
IG_TOKEN = _env("IG_TOKEN")
# Render entrega la dirección pública del servicio en esta variable
URL_PUBLICA = _env("RENDER_EXTERNAL_URL")

# Mercado Público: ticket que se pide con Clave Única en api.mercadopublico.cl
MERCADOPUBLICO_TICKET = _env("MERCADOPUBLICO_TICKET")

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
    # Nombres de producción: abreviaturas que se aplican al producto + color de la matriz
    "abreviaturas": "corriedale dk=cdk, azul paquete de vela=azul vela, azul paquete vela=azul vela",
    # Envío a producción
    "correo_produccion": "socmeierymeierltda@gmail.com",
    "letra_pdf_produccion": "14",
    # Planilla Ventas en Drive (se comparte con el robot como Lector)
    "ultima_sync_ventas": "",
    "drive_ventas": "https://docs.google.com/spreadsheets/d/146abptOvMsCmKmXVKy4YN2GzXfW5nv2SQhLH21rZdP0/edit",
    # Conciliación: RUT que aparece en la cartola = de dónde viene el depósito
    "ruts_conocidos": "0768300143=flow, 0966893109=transbank",
    # Cobranza
    "vendedores": "javier=javier, maria de los angeles=maria, mariajesus=jesu, manuel=manuel, cris=cris",
    "palabras_pagado": "efect, efectivo, pagado",
    "link_fedex": "https://www.fedex.com/fedextrack/?trknbr={n}",
    "link_blue": "https://www.blue.cl/seguimiento/?n_seguimiento={n}",
    "link_starken": "https://www.starken.cl/seguimiento?codigo={n}",
    "aviso_despacho": ("¡Hola {nombre}! Tu pedido de Topwool ya va en camino 🚚\n"
                       "Empresa de transporte: {empresa}\nN° de seguimiento: {tracking}\n"
                       "Puedes seguirlo aquí: {link}\n\n¡Gracias por tejer con nosotros!"),
    # Inventario: del stock real (Defontana) al stock que se publica en la web
    "inv_reserva": "0",
    "inv_reserva_codigos": "",
    "inv_minimo": "0",
    "inv_maximo": "",
    "inv_no_tocar": "",
    "inv_auto": "",
    # Marketing
    "marketing_meta": "10000000",
    "marketing_meta_total": "",
    "marketing_ritmo": "lun=Carrusel de producto, mar=Historias, mie=Reel, jue=Historias, vie=Carrusel de producto, sab=Reel, dom=Comunidad, jue=Correo, mar=WhatsApp mayoristas, vie=Instagram mayoristas",
    "marketing_productos": "",
    "marketing_auto": "1",
    # Gestor web
    "gestor_dias_dormido": "180",
    "gestor_max_envios": "300",
    "gestor_responder_a": "contacto@topwool.cl",
    "gestor_url_publica": "",
    # Fondos públicos: páginas que se revisan solas (una por línea) y búsquedas de noticias
    "fondos_fuentes": "\n".join([
        "https://www.sercotec.cl/convocatorias/",
        "https://www.fondos.gob.cl/",
        "https://www.corfo.cl/sites/cpp/convocatorias_y_programas",
        "https://www.fondosdecultura.cl/",
        "https://www.conadi.gob.cl/",
        "https://www.fosis.gob.cl/",
        "https://www.indap.gob.cl/",
        "https://www.gorearaucania.cl/",
    ]),
    "fondos_noticias": "\n".join([
        "convocatoria fondo artesanía",
        "convocatoria Sercotec Araucanía",
        "fondo concursable artesanas tejedoras",
        "CONADI fondo emprendimiento postulación",
        "INDAP artesanía convocatoria",
    ]),
    "fondos_palabras": ("convocatoria, postulacion, postula, fondo, capital semilla, capital abeja, crece, concurso, "
                        "subsidio, emprende, fomento, fortalece, financiamiento, artesan"),
    "fondos_excluir": ("resultado, ganador, seleccionad, adjudica, nomina, acta de, preguntas frecuentes, iniciar sesion, "
                       "login, licitacion, cuenta publica, transparencia, trabaja con nosotros, concurso publico para"),
    "fondos_ultima_busqueda": "",
    "fondos_resumen": "",
    # Mercado Público
    "mp_palabras": ("lana, hilo, hilado, vellon, vellón, algodon, algodón, ovillo, madeja, telar, fieltro, "
                    "tejido, tejer, palillo, crochet, merino, alpaca, mohair, artesania textil"),
    "mp_excluir": ("algodon hidrofilo, algodon prensado, torula, aposito, gasa, quirurg, sutura, hilo dental, "
                   "lana de vidrio, lana mineral, lana de roca, aislacion, aislante, tejido adiposo, hilo de pesca, "
                   "hilo nylon, hilo electrico, malla, geotextil"),
    "mp_avisar_correo": "",
    "mp_ultima_busqueda": "",
    "correo_asunto": "Tu pedido #{numero} en Topwool está esperando el pago",
    "correo_cuerpo": ("Hola {nombre}:\n\n"
                      "Vimos que tu pedido #{numero} en topwool.cl quedó pendiente de pago.\n\n"
                      "Productos: {productos}\nTotal: {total}\n\n"
                      "Si pagaste por transferencia, ¿nos puedes enviar el comprobante respondiendo este correo? "
                      "Si tuviste algún problema para pagar o quieres cambiar algo, cuéntanos y te ayudamos.\n\n"
                      "Te guardamos el pedido unos días más.\n\n"
                      "Un abrazo,\nEquipo Topwool\nwww.topwool.cl"),
}
