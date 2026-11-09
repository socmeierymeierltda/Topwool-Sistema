# Sistema Topwool

Aplicación web interna de Topwool (Meier y Meier Ltda.) para centralizar pedidos, inventario y, más adelante, cobranza, informes y marketing.

## Qué hace hoy (etapa 1)

- **Pedidos web**: lee los pedidos nuevos de WooCommerce (www.topwool.cl) y arma la fila del Drive "Pedidos Curacautín" con las reglas de la empresa:
  - Cliente `web - nombre`, lugar `curacautin`, encargado `jesu`.
  - Pago con **Flow** → documento `cv`, entra listo.
  - Pago con **transferencia** → documento `boleta` y comentario `TRANSFERENCIA - POR CONFIRMAR`. Cuando finanzas confirma, se aprieta **Pago confirmado** y el comentario se borra del Drive.
  - Empresa de transporte a elección (sugiere la que el cliente nombre en su nota).
  - Columna Pedido con nombres de producción: `1 un malal rojo`, `2k cdk menta`, `1b nacional`.
- **Equivalencias**: tabla web ↔ nombre de producción ↔ código Defontana. Se carga desde Excel y se edita en pantalla.
- **Inventario**: se sube el *Informe de Inventario* de Defontana; el sistema suma todas las bodegas, lo compara con el stock de la web y actualiza los productos marcados (las bolsas de 500 g descuentan medio kilo).
- **Ajustes**: todas las reglas anteriores se pueden cambiar sin tocar código.

Sin claves de WooCommerce corre en **modo demo** (pedidos de ejemplo, no escribe en ningún lado).

## Puesta en marcha en Render

1. Subir este código a GitHub (cuenta `socmeierymeierltda`, repositorio `topwool-sistema`).
2. En Render: **New → Blueprint** y elegir el repositorio. El archivo `render.yaml` crea el servicio web y la base de datos.
3. Completar las variables que Render pide:

| Variable | De dónde sale |
|---|---|
| `APP_PASSWORD` | La clave para entrar al sistema (la eliges tú). |
| `WOO_CONSUMER_KEY` / `WOO_CONSUMER_SECRET` | WordPress → WooCommerce → Ajustes → Avanzado → API REST → Añadir clave. Permisos: **Lectura/Escritura**. |
| `GOOGLE_CREDENTIALS_JSON` | Google Cloud → cuenta de servicio → clave JSON (pegar el contenido completo). Activar "Google Sheets API". |
| `PEDIDOS_SHEET_ID` | El código largo de la URL del Drive de pedidos: `docs.google.com/spreadsheets/d/ESTE_CODIGO/edit`. |

4. Compartir el Drive de pedidos como **Editor** con el correo de la cuenta de servicio (aparece en Ajustes del sistema).
5. Entrar al sistema → **Equivalencias** → subir `Equivalencias_productos_Topwool.xlsx`.

## Desarrollo local

```bash
pip install -r requirements.txt
uvicorn main:app --reload
# http://localhost:8000  (clave por defecto: topwool)
```

## Próximas etapas

3. Cobranza: pedidos con tag `curacautin` + resumen diario de documentos → Drive de cobranza; conciliación bancaria.
4. Informes: ventas por mes/producto, estacionalidad, kits, campañas (historia Defontana 2022–2026 + web).
5. Marketing: calendario corto/mediano/largo plazo con feriados y fechas comerciales de Chile.
