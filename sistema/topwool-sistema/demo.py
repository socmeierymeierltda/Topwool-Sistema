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
