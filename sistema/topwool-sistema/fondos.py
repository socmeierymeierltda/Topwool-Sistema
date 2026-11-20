"""Búsqueda automática de fondos públicos: lee las páginas de convocatorias de las instituciones y
noticias recientes (Google Noticias), y deja lo que encuentra «por revisar» en Gestor web → Fondos."""
import html as htmlmod
import re
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urljoin

import httpx
from sqlalchemy import select

import cobranza as cb
import db
import gestor as g

MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
         "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}
REGIONES = ["arica", "tarapaca", "antofagasta", "atacama", "coquimbo", "valparaiso", "metropolitana", "o'higgins",
            "ohiggins", "maule", "nuble", "biobio", "araucania", "los rios", "los lagos", "aysen", "magallanes"]
INSTITUCIONES = [("sercotec", "Sercotec"), ("corfo", "Corfo"), ("indap", "INDAP"), ("conadi", "CONADI"),
                 ("fosis", "FOSIS"), ("fondosdecultura", "Ministerio de las Culturas"), ("cultura", "Ministerio de las Culturas"),
                 ("fondart", "Ministerio de las Culturas"), ("gorearaucania", "Gobierno Regional Araucanía"),
                 ("gobierno regional", "Gobierno Regional"), ("fondos.gob", "Fondos.gob.cl"), ("prochile", "ProChile")]
UA = {"User-Agent": "Mozilla/5.0 (compatible; topwool-sistema; +https://www.topwool.cl)"}


def _texto(h):
    return " ".join(htmlmod.unescape(re.sub(r"<[^>]+>", " ", h or "")).split())


def fechas(texto, hoy=None):
    """Fechas que aparecen en un texto (12 de diciembre de 2026, 12/12/2026, 12-12-26)."""
    hoy = hoy or date.today()
    out = []
    t = cb.norm(texto)
    for d, m, y in re.findall(r"\b(\d{1,2})\s+de\s+(" + "|".join(MESES) + r")(?:\s+(?:de|del)\s+(\d{4}))?", t):
        try:
            out.append(date(int(y) if y else hoy.year, MESES[m], int(d)))
        except ValueError:
            pass
    for d, m, y in re.findall(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", t):
        y = int(y) + (2000 if len(y) == 2 else 0)
        try:
            out.append(date(y, int(m), int(d)))
        except ValueError:
            pass
    return out


def _sirve(texto, aj):
    t = cb.norm(texto)
    claves = [cb.norm(x) for x in db.lista(aj.get("fondos_palabras", ""))]
    excluir = [cb.norm(x) for x in db.lista(aj.get("fondos_excluir", ""))]
    return len(t) >= 12 and any(k in t for k in claves) and not any(x in t for x in excluir)


def _institucion(url, texto):
    t = cb.norm(url + " " + texto)
    return next((n for k, n in INSTITUCIONES if k in t), "")


def _region(texto):
    t = cb.norm(texto)
    r = next((x for x in REGIONES if x in t), "")
    return r.title().replace("Araucania", "Araucanía").replace("Biobio", "Biobío").replace("Nuble", "Ñuble") if r else ""


def de_pagina(url, contenido, aj):
    """Enlaces de una página de convocatorias que parecen fondos abiertos."""
    out, vistos = [], set()
    for m in re.finditer(r"<a\b[^>]*href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", contenido, re.S | re.I):
        href, txt = urljoin(url, m.group(1).strip()), _texto(m.group(2))
        if href in vistos or not txt or len(txt) > 220 or href.rstrip("/") == url.rstrip("/"):
            continue
        sig = contenido.find("<a ", m.end())
        cerca = _texto(contenido[m.end(): min(m.end() + 700, sig if sig > 0 else len(contenido))])[:350]
        if not _sirve(txt, aj):
            continue
        vistos.add(href)
        fs = sorted(set(fechas(txt + " " + cerca)))
        out.append({"nombre": txt, "url": href, "nota": cerca[:250], "fechas": fs,
                    "institucion": _institucion(href, txt) or _institucion(url, ""),
                    "region": _region(txt) or _region(cerca)})
    return out


def de_noticias(consulta, contenido, aj, dias=21):
    """Noticias recientes (RSS de Google Noticias) sobre convocatorias."""
    out = []
    limite = datetime.now() - timedelta(days=dias)
    for item in re.findall(r"<item>(.*?)</item>", contenido, re.S):
        tit = _texto(re.search(r"<title>(.*?)</title>", item, re.S).group(1)) if "<title>" in item else ""
        link = (re.search(r"<link>(.*?)</link>", item, re.S) or re.search(r"<guid[^>]*>(.*?)</guid>", item, re.S))
        fecha = re.search(r"<pubDate>(.*?)</pubDate>", item)
        try:
            pub = parsedate_to_datetime(fecha.group(1)).replace(tzinfo=None) if fecha else None
        except (TypeError, ValueError):
            pub = None
        if not tit or not link or (pub and pub < limite) or not _sirve(tit, aj):
            continue
        desc = _texto(re.search(r"<description>(.*?)</description>", item, re.S).group(1)) if "<description>" in item else ""
        out.append({"nombre": tit[:240], "url": link.group(1).strip(), "nota": f"Noticia del {pub:%d-%m-%Y}" if pub else "Noticia",
                    "fechas": sorted(set(fechas(tit + " " + desc))), "institucion": _institucion("", tit),
                    "region": _region(tit)})
    return out


def fuentes(aj):
    paginas = [u.strip() for u in (aj.get("fondos_fuentes") or "").splitlines() if u.strip().startswith("http")]
    noticias = [q.strip() for q in (aj.get("fondos_noticias") or "").splitlines() if q.strip()]
    return paginas, noticias


def buscar(avance=lambda t: None) -> dict:
    aj = db.ajustes()
    paginas, consultas = fuentes(aj)
    hoy = date.today()
    encontrados, resumen = [], []
    with httpx.Client(timeout=30, follow_redirects=True, headers=UA) as c:
        for i, url in enumerate(paginas, 1):
            avance(f"Fondos: revisando {url} ({i} de {len(paginas)})…")
            try:
                r = c.get(url)
                r.raise_for_status()
                hs = de_pagina(str(r.url), r.text, aj)
                resumen.append(f"{url}: {len(hs)}")
                for h in hs:
                    h["origen"] = re.sub(r"^https?://(www\.)?", "", url).split("/")[0][:80]
                encontrados += hs
            except Exception as e:  # noqa: BLE001
                resumen.append(f"{url}: no respondió ({str(e)[:60]})")
        for q in consultas:
            avance(f"Fondos: noticias «{q}»…")
            try:
                r = c.get("https://news.google.com/rss/search?q=" + quote_plus(q) + "&hl=es-419&gl=CL&ceid=CL:es-419")
                r.raise_for_status()
                hs = de_noticias(q, r.text, aj)
                resumen.append(f"Noticias «{q}»: {len(hs)}")
                for h in hs:
                    h["origen"] = "noticias"
                encontrados += hs
            except Exception as e:  # noqa: BLE001
                resumen.append(f"Noticias «{q}»: no respondió ({str(e)[:60]})")
    nuevos = 0
    with db.Session() as s:
        ya = {f.url for f in s.scalars(select(g.Fondo)) if f.url}
        ya_nom = {cb.norm(f.nombre) for f in s.scalars(select(g.Fondo))}
        for h in encontrados:
            if h["url"] in ya or cb.norm(h["nombre"]) in ya_nom:
                continue
            futuras = [f for f in h["fechas"] if f >= hoy]
            pasadas = [f for f in h["fechas"] if f < hoy]
            if h["fechas"] and not futuras and max(pasadas) < hoy - timedelta(days=3):
                continue      # todas sus fechas ya pasaron: convocatoria cerrada
            s.add(g.Fondo(nombre=h["nombre"][:250], institucion=h["institucion"][:80], region=h["region"][:80],
                          url=h["url"][:400], nota=h["nota"], cierra=max(futuras) if futuras else None,
                          abre=min(h["fechas"]) if len(h["fechas"]) > 1 else None,
                          estado="por_revisar", origen=h.get("origen", "")))
            ya.add(h["url"])
            ya_nom.add(cb.norm(h["nombre"]))
            nuevos += 1
        s.commit()
    db.guardar_ajustes({"fondos_ultima_busqueda": datetime.now().isoformat(timespec="minutes"),
                        "fondos_resumen": " · ".join(resumen)[:1500]})
    return {"nuevos": nuevos, "revisados": len(encontrados), "resumen": resumen}
