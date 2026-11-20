"""Mercado Público (ChileCompra): busca licitaciones y compras ágiles publicadas que tengan que ver
con nuestros productos (lana, hilo, vellón, algodón…) para postular a tiempo.
El ticket de la API va en la variable MERCADOPUBLICO_TICKET de Render (nunca en el código)."""
import re
import time
from datetime import datetime, timedelta

import httpx
from sqlalchemy import Column, DateTime, Float, Integer, String, Text, select

import cobranza as cb
import config
import db

API1 = "https://api.mercadopublico.cl/servicios/v1/publico/"
API2 = "https://api2.mercadopublico.cl/v2/compra-agil"
LINK_LICITACION = "https://www.mercadopublico.cl/Procurement/Modules/RFB/DetailsAcquisition.aspx?idlicitacion={c}"

REGIONES = {1: "Tarapacá", 2: "Antofagasta", 3: "Atacama", 4: "Coquimbo", 5: "Valparaíso", 6: "O'Higgins",
            7: "Maule", 8: "Biobío", 9: "Araucanía", 10: "Los Lagos", 11: "Aysén", 12: "Magallanes",
            13: "Metropolitana", 14: "Los Ríos", 15: "Arica y Parinacota", 16: "Ñuble"}

ESTADO = {"buscando": False, "mensaje": "", "fin": None}


class Oportunidad(db.Base):
    __tablename__ = "mp_oportunidades"
    codigo = Column(String(40), primary_key=True)
    tipo = Column(String(20), index=True)          # licitacion | compra_agil
    nombre = Column(String(400))
    descripcion = Column(Text)
    organismo = Column(String(250))
    region = Column(String(60))
    publicada = Column(DateTime)
    cierre = Column(DateTime, index=True)
    monto = Column(Float)
    estado_mp = Column(String(40))
    palabras = Column(String(200))
    estado = Column(String(20), default="nueva", index=True)   # nueva | interesa | postulada | descartada
    nota = Column(Text)
    encontrada = Column(DateTime, default=datetime.now)


def hay_ticket():
    return bool(config.MERCADOPUBLICO_TICKET)


def _palabras(aj):
    return list(dict.fromkeys(cb.norm(p) for p in db.lista(aj.get("mp_palabras", ""))))


def _limpio(texto):
    return " " + " ".join(re.sub(r"[^a-z0-9ñ ]", " ", cb.norm(texto)).split()) + " "


def excluida(texto, excluir):
    t = _limpio(texto)
    return any(_limpio(x).rstrip() in t for x in excluir if x.strip())


def coincide(texto, palabras, excluir):
    """Palabras completas del texto, aceptando plural (lana → lanas; hilo → hilos; no «Lanalhue»)."""
    t = _limpio(texto)
    if excluida(texto, excluir):
        return []
    return [p for p in palabras if re.search(r"(?<![a-z0-9ñ])" + re.escape(p) + r"(?:s|es)?(?![a-z0-9ñ])", t)]


def _fecha(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")[:19])
    except ValueError:
        return None


def _licitaciones(c, palabras, excluir, avance):
    r = c.get(API1 + "licitaciones.json", params={"estado": "activas", "ticket": config.MERCADOPUBLICO_TICKET})
    r.raise_for_status()
    listado = r.json().get("Listado") or []
    hits = []
    for x in listado:
        p = coincide(x.get("Nombre", ""), palabras, excluir)
        if p:
            hits.append((x, p))
    out = []
    for i, (x, p) in enumerate(hits[:60]):
        avance(f"Licitaciones: revisando {i + 1} de {len(hits)} que coinciden (de {len(listado)} activas)…")
        d = x
        try:   # el detalle trae organismo, monto y descripción
            rd = c.get(API1 + "licitaciones.json", params={"codigo": x["CodigoExterno"], "ticket": config.MERCADOPUBLICO_TICKET})
            if rd.status_code == 200 and (rd.json().get("Listado") or []):
                d = rd.json()["Listado"][0]
            time.sleep(0.3)
        except httpx.HTTPError:
            pass
        desc = d.get("Descripcion") or ""
        if excluida(desc, excluir):
            continue
        comp, fechas = d.get("Comprador") or {}, d.get("Fechas") or {}
        out.append(dict(codigo=x["CodigoExterno"], tipo="licitacion", nombre=d.get("Nombre") or x.get("Nombre"),
                        descripcion=desc, organismo=comp.get("NombreOrganismo"), region=(comp.get("RegionUnidad") or "").strip(),
                        publicada=_fecha(fechas.get("FechaPublicacion")), cierre=_fecha(fechas.get("FechaCierre") or x.get("FechaCierre")),
                        monto=d.get("MontoEstimado") if d.get("VisibilidadMonto", 1) else None,
                        estado_mp=d.get("Estado") or "Publicada", palabras=", ".join(p)))
    return out


def _compras_agiles(c, palabras, excluir, avance, dias=10):
    vistos, out = set(), []
    desde = (datetime.utcnow() - timedelta(days=dias)).strftime("%Y-%m-%dT00:00:00Z")
    for j, palabra in enumerate(palabras):
        avance(f"Compra Ágil: buscando «{palabra}» ({j + 1} de {len(palabras)})…")
        for pagina in range(1, 5):
            r = c.get(API2, headers={"ticket": config.MERCADOPUBLICO_TICKET},
                      params={"q": palabra, "estado": "publicada", "publicado_desde": desde,
                              "tamano_pagina": 50, "numero_pagina": pagina})
            if r.status_code == 404:
                break
            r.raise_for_status()
            payload = r.json().get("payload") or {}
            items = payload.get("items") or []
            for x in items:
                cod = x.get("codigo")
                if not cod or cod in vistos:
                    continue
                vistos.add(cod)
                p = coincide(x.get("nombre", ""), palabras, excluir)
                if not p:
                    continue
                inst, fechas, montos = x.get("institucion") or {}, x.get("fechas") or {}, x.get("montos") or {}
                out.append(dict(codigo=cod, tipo="compra_agil", nombre=x.get("nombre"), descripcion="",
                                organismo=inst.get("organismo_comprador"),
                                region=inst.get("nombre_region") or REGIONES.get(inst.get("region"), ""),
                                publicada=_fecha(fechas.get("fecha_publicacion")), cierre=_fecha(fechas.get("fecha_cierre")),
                                monto=montos.get("monto_disponible_clp") or None,
                                estado_mp=(x.get("estado") or {}).get("glosa") or "Publicada", palabras=", ".join(p)))
            if len(items) < 50:
                break
            time.sleep(0.3)
    return out


def buscar(avance=lambda t: None) -> dict:
    """Consulta la API y guarda lo nuevo. Devuelve {'nuevas': [...], 'total': n}."""
    aj = db.ajustes()
    palabras, excluir = _palabras(aj), db.lista(aj.get("mp_excluir", ""))
    if config.DEMO and not hay_ticket():
        encontradas = _demo()
    else:
        if not hay_ticket():
            raise RuntimeError("falta el ticket de Mercado Público (MERCADOPUBLICO_TICKET en Render)")
        with httpx.Client(timeout=60, headers={"User-Agent": "topwool-sistema"}) as c:
            encontradas = _licitaciones(c, palabras, excluir, avance)
            try:
                encontradas += _compras_agiles(c, palabras, excluir, avance)
            except httpx.HTTPError as e:
                avance(f"Compra Ágil no respondió ({str(e)[:80]}); se guardaron las licitaciones.")
    nuevas = []
    with db.Session() as s:
        for o in encontradas:
            ya = s.get(Oportunidad, o["codigo"])
            if ya:
                for k in ("cierre", "estado_mp", "monto", "organismo", "region", "descripcion"):
                    if o.get(k):
                        setattr(ya, k, o[k] if not isinstance(o[k], str) else o[k][:4000])
            else:
                o = {k: (v[:395] if isinstance(v, str) and k != "descripcion" else v) for k, v in o.items()}
                s.add(Oportunidad(**o))
                nuevas.append(o)
        s.commit()
    return {"nuevas": nuevas, "total": len(encontradas)}


def texto_aviso(nuevas):
    lineas = ["Nuevas oportunidades en Mercado Público para Topwool:\n"]
    for o in sorted(nuevas, key=lambda o: o.get("cierre") or datetime.max):
        link = LINK_LICITACION.format(c=o["codigo"]) if o["tipo"] == "licitacion" else ""
        lineas.append(f"• [{'Licitación' if o['tipo'] == 'licitacion' else 'Compra Ágil'}] {o['nombre']}\n"
                      f"  {o.get('organismo') or ''} · {o.get('region') or ''} · cierra "
                      f"{o['cierre'].strftime('%d-%m-%Y %H:%M') if o.get('cierre') else '?'}\n  Código {o['codigo']} {link}\n")
    lineas.append("Revisa todo en el sistema → Gestor web → Mercado Público.")
    return "\n".join(lineas)


def _demo():
    ahora = datetime.now()
    return [
        dict(codigo="4280-55-L126", tipo="licitacion", nombre="Adquisición de lana e hilados para taller de telar mapuche",
             descripcion="Lana de oveja hilada a mano y vellón para programa de artesanía.", organismo="Municipalidad de Lonquimay",
             region="Región de la Araucanía", publicada=ahora - timedelta(days=2), cierre=ahora + timedelta(days=8),
             monto=2500000, estado_mp="Publicada", palabras="lana, hilado, vellon"),
        dict(codigo="1057539-228-COT26", tipo="compra_agil", nombre="Compra de ovillos de lana para taller de tejido adulto mayor",
             descripcion="", organismo="Municipalidad de Temuco", region="Araucanía", publicada=ahora - timedelta(days=1),
             cierre=ahora + timedelta(days=3), monto=450000, estado_mp="Publicada", palabras="lana, tejido"),
        dict(codigo="2301-12-COT26", tipo="compra_agil", nombre="Hilo de algodón para jardín infantil (material didáctico)",
             descripcion="", organismo="JUNJI Región de Los Ríos", region="Los Ríos", publicada=ahora - timedelta(days=3),
             cierre=ahora + timedelta(days=1), monto=180000, estado_mp="Publicada", palabras="hilo, algodon"),
    ]
