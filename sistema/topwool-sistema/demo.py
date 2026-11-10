"""Pedidos de ejemplo para el modo demo (formato igual al de WooCommerce)."""

def _p(id_, num, nombre, apellido, fono, dir1, ciudad, region, metodo, titulo, total, items, estado="processing", nota=""):
    return {
        "id": id_, "number": str(num), "status": estado, "date_created": "2026-11-09T10:%02d:00" % (id_ % 60),
        "total": str(total), "payment_method": metodo, "payment_method_title": titulo,
        "customer_note": nota,
        "billing": {"first_name": nombre, "last_name": apellido, "phone": fono, "email": f"{nombre.lower()}@ejemplo.cl"},
        "shipping": {"first_name": nombre, "last_name": apellido, "address_1": dir1, "address_2": "",
                     "city": ciudad, "state": region},
        "line_items": [{"product_id": pid, "variation_id": vid, "sku": sku, "name": n, "quantity": q}
                       for pid, vid, sku, n, q in items],
    }


PEDIDOS_DEMO = [
    _p(9101, 12873, "Ahylinne Marcela", "Vera Fica", "+56966545789", "Av. Grecia 993", "Concepción", "Biobío",
       "flow", "Flow - Webpay", 58500,
       [(6837, 6838, "HICFAMARILLOPASTEL100", "Corriedale Fingering Teñido - Amarillo Pastel", 3),
        (3883, 3895, "CHIADKCELESTE", "Algodón DK - Celeste", 4),
        (6820, 6825, "HICFMELANGECAFENATURAL100", "Corriedale Fingering Natural - Melange Café Natural", 3)]),
    _p(9102, 12874, "Sofía", "Maldonado", "+56968328126", "Guard Zañartu 564, casa azul", "Valparaíso", "Valparaíso",
       "bacs", "Transferencia bancaria directa", 64000,
       [(2448, 0, "HIMAROJO", "Malal Teñido - Rojo", 2),
        (2486, 0, "100CHIMAMOSTAZA", "Malal - Mostaza", 1)], estado="on-hold",
       nota="Por favor enviar por Blue"),
    _p(9104, 12870, "Paula", "Rivas", "+56911112222", "Los Aromos 120", "Temuco", "Araucanía",
       "flow", "Flow - Webpay", 22500,
       [(2486, 0, "100CHIMAMOSTAZA", "Malal - Mostaza", 5)], estado="pending"),
    _p(9103, 12875, "Clementina", "Gaete", "+56994172750", "Coronel Souper 4171 dpto 1413", "Estación Central", "RM",
       "flow", "Flow - Webpay", 31000,
       [(4909, 0, "HIMECAFENATURAL", "Mecha Colores Naturales - Café Natural", 1)]),
]


def historial_demo():
    """Dos años de pedidos inventados para probar los informes en modo demo."""
    import random
    from datetime import datetime, timedelta
    rnd = random.Random(7)
    productos = [("Malal Teñido", ["Rojo", "Mostaza", "Azul Vela", "Melange Gris"], 13900),
                 ("Corriedale DK Teñido", ["Menta", "Amarillo Pastel", "Ladrillo"], 4900),
                 ("Merino Socks", ["Crudo", "Petróleo", "Vino"], 6500),
                 ("Algodón DK", ["Celeste", "Blanco", "Rosa"], 3500),
                 ("Mecha Colores Naturales", ["Café Natural", "Gris Natural"], 15000),
                 ("Relmu", ["Café", "Crudo"], 12500)]
    comunas = [("Santiago", "CL-RM"), ("Providencia", "CL-RM"), ("Temuco", "CL-AR"), ("Concepción", "CL-BI"),
               ("Valparaíso", "CL-VS"), ("Puerto Montt", "CL-LL"), ("La Serena", "CL-CO"), ("Coyhaique", "CL-AI")]
    clientes = [(f"cliente{i}@ejemplo.cl", f"Cliente {i}") for i in range(160)]
    inicio = datetime(2024, 11, 1)
    for i in range(900):
        f = inicio + timedelta(days=rnd.random() * 738)
        if rnd.random() > (0.55 + 0.45 * (f.month in (4, 5, 6, 7, 8))):   # más ventas en otoño-invierno
            continue
        items = []
        for _ in range(rnd.choice([1, 1, 2, 2, 3])):
            fam, colores, precio = rnd.choice(productos)
            q = rnd.choice([1, 1, 2, 3, 5])
            items.append({"product_id": hash(fam) % 9999, "variation_id": 0, "sku": "",
                          "name": f"{fam} - {rnd.choice(colores)}", "quantity": q, "total": str(q * precio)})
        email, nombre = rnd.choice(clientes)
        ciudad, region = rnd.choice(comunas)
        envio = rnd.choice([0, 5990, 7990])
        estado = rnd.choices(["completed", "processing", "pending", "cancelled"], [80, 6, 8, 6])[0]
        yield {"id": 50000 + i, "number": str(50000 + i), "status": estado,
               "date_created": f.strftime("%Y-%m-%dT%H:%M:%S"), "date_modified": f.strftime("%Y-%m-%dT%H:%M:%S"),
               "total": str(sum(int(x["total"]) for x in items) + envio), "shipping_total": str(envio),
               "discount_total": "0", "payment_method": rnd.choice(["flow", "bacs"]),
               "payment_method_title": rnd.choice(["Flow - Webpay", "Transferencia bancaria directa"]),
               "customer_id": 0, "billing": {"email": email, "first_name": nombre, "last_name": "",
                                             "city": ciudad, "state": region},
               "shipping": {"city": ciudad, "state": region}, "line_items": items, "coupon_lines": []}
