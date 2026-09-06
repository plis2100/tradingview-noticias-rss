import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup


PAGINA_NOTICIAS = "https://es.tradingview.com/news/"
API_NOTICIAS = "https://news-headlines.tradingview.com/headlines/"

ARCHIVO_RSS = Path("rss.xml")
ZONA_HORARIA = ZoneInfo("Europe/Madrid")
MAX_ARTICULOS_RSS = 3000
MAX_PAGINAS_API = 15

CABECERAS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.7",
    "Referer": "https://es.tradingview.com/",
    "Origin": "https://es.tradingview.com",
    "Cache-Control": "no-cache",
}


def dentro_del_horario():
    """
    Los lanzamientos manuales siempre se ejecutan.

    Los automáticos solamente se ejecutan:
    - De lunes a sábado.
    - Desde las 07:00 hasta las 22:59.
    - Según la hora peninsular española.
    """
    evento = os.environ.get("GITHUB_EVENT_NAME", "")

    if evento == "workflow_dispatch":
        print("Ejecución manual: se ignora la limitación horaria.")
        return True

    ahora = datetime.now(ZONA_HORARIA)
    print(f"Hora española: {ahora:%Y-%m-%d %H:%M:%S %Z}")

    if ahora.weekday() == 6:
        print("Domingo: no se actualiza el RSS.")
        return False

    if not 7 <= ahora.hour <= 22:
        print("Fuera del horario permitido: 07:00-22:59.")
        return False

    return True


def texto(valor):
    if valor is None:
        return ""

    return str(valor).strip()


def limpiar_html(valor):
    contenido = texto(valor)

    if not contenido:
        return ""

    sopa = BeautifulSoup(contenido, "html.parser")
    return " ".join(sopa.get_text(" ", strip=True).split())


def normalizar_url(url):
    url = texto(url)

    if not url:
        return ""

    if url.startswith("//"):
        return "https:" + url

    return urljoin(PAGINA_NOTICIAS, url)


def convertir_fecha(valor):
    if valor in (None, ""):
        return format_datetime(datetime.now(timezone.utc))

    try:
        if isinstance(valor, (int, float)):
            numero = float(valor)

            if numero > 10_000_000_000:
                numero = numero / 1000

            fecha = datetime.fromtimestamp(numero, timezone.utc)
            return format_datetime(fecha)

        valor = str(valor).strip()

        if valor.isdigit():
            numero = float(valor)

            if numero > 10_000_000_000:
                numero = numero / 1000

            fecha = datetime.fromtimestamp(numero, timezone.utc)
            return format_datetime(fecha)

        fecha = datetime.fromisoformat(valor.replace("Z", "+00:00"))

        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=timezone.utc)

        return format_datetime(fecha.astimezone(timezone.utc))

    except (ValueError, TypeError, OSError):
        return format_datetime(datetime.now(timezone.utc))


def nombre_fuente(valor):
    if isinstance(valor, dict):
        return texto(
            valor.get("name")
            or valor.get("title")
            or valor.get("id")
        )

    return texto(valor)


def extraer_simbolos(noticia):
    simbolos = []
    posibles = (
        noticia.get("relatedSymbols")
        or noticia.get("related_symbols")
        or noticia.get("symbols")
        or noticia.get("tickers")
        or []
    )

    if isinstance(posibles, dict):
        posibles = list(posibles.values())

    if not isinstance(posibles, list):
        posibles = [posibles]

    for elemento in posibles:
        if isinstance(elemento, dict):
            simbolo = texto(
                elemento.get("symbol")
                or elemento.get("pro_name")
                or elemento.get("short_name")
                or elemento.get("name")
                or elemento.get("ticker")
            )
        else:
            simbolo = texto(elemento)

        if simbolo and simbolo not in simbolos:
            simbolos.append(simbolo)

    return simbolos


def obtener_imagen_json(noticia):
    candidatos = [
        noticia.get("imageUrl"),
        noticia.get("image_url"),
        noticia.get("image"),
        noticia.get("thumbnail"),
        noticia.get("thumbnailUrl"),
        noticia.get("previewImageUrl"),
    ]

    for candidato in candidatos:
        if isinstance(candidato, dict):
            candidato = (
                candidato.get("url")
                or candidato.get("src")
                or candidato.get("href")
            )

        candidato = normalizar_url(candidato)

        if candidato:
            return candidato

    return ""


def obtener_url_json(noticia):
    candidatos = [
        noticia.get("storyPath"),
        noticia.get("story_path"),
        noticia.get("url"),
        noticia.get("link"),
        noticia.get("href"),
        noticia.get("canonicalUrl"),
    ]

    for candidato in candidatos:
        candidato = normalizar_url(candidato)

        if candidato:
            return candidato

    identificador = texto(noticia.get("id"))

    if identificador:
        return urljoin(PAGINA_NOTICIAS, identificador)

    return ""


def convertir_noticia_json(noticia):
    if not isinstance(noticia, dict):
        return None

    titulo = limpiar_html(
        noticia.get("title")
        or noticia.get("headline")
        or noticia.get("name")
    )

    enlace = obtener_url_json(noticia)

    if not titulo or not enlace:
        return None

    identificador = texto(
        noticia.get("id")
        or noticia.get("storyId")
        or noticia.get("story_id")
        or enlace
    )

    fuente = nombre_fuente(
        noticia.get("source")
        or noticia.get("provider")
        or noticia.get("providerId")
        or noticia.get("sourceName")
    )

    simbolos = extraer_simbolos(noticia)

    resumen = limpiar_html(
        noticia.get("summary")
        or noticia.get("description")
        or noticia.get("snippet")
        or noticia.get("shortDescription")
    )

    partes_descripcion = []

    if fuente:
        partes_descripcion.append(f"<p><strong>Fuente:</strong> {fuente}</p>")

    if resumen:
        partes_descripcion.append(f"<p>{resumen}</p>")

    if simbolos:
        lista_simbolos = ", ".join(simbolos)
        partes_descripcion.append(
            f"<p><strong>Valores relacionados:</strong> "
            f"{lista_simbolos}</p>"
        )

    partes_descripcion.append(
        f'<p><a href="{enlace}">Abrir noticia en TradingView</a></p>'
    )

    fecha = (
        noticia.get("published")
        or noticia.get("publishedAt")
        or noticia.get("published_at")
        or noticia.get("publicationDate")
        or noticia.get("timestamp")
        or noticia.get("createdAt")
        or noticia.get("updated")
    )

    categorias = []

    if fuente:
        categorias.append(fuente)

    for simbolo in simbolos:
        if simbolo not in categorias:
            categorias.append(simbolo)

    return {
        "title": titulo,
        "link": enlace,
        "guid": identificador,
        "pubDate": convertir_fecha(fecha),
        "description": "".join(partes_descripcion),
        "author": fuente,
        "categories": categorias,
        "image": obtener_imagen_json(noticia),
    }


def localizar_lista_noticias(datos):
    if isinstance(datos, list):
        return datos

    if not isinstance(datos, dict):
        return []

    for clave in (
        "items",
        "news",
        "headlines",
        "stories",
        "results",
        "data",
    ):
        valor = datos.get(clave)

        if isinstance(valor, list):
            return valor

        if isinstance(valor, dict):
            encontrados = localizar_lista_noticias(valor)

            if encontrados:
                return encontrados

    return []


def obtener_siguiente_pagina(datos):
    if not isinstance(datos, dict):
        return ""

    candidatos = [
        datos.get("nextPage"),
        datos.get("next_page"),
        datos.get("pagination"),
        datos.get("next"),
        datos.get("cursor"),
    ]

    for candidato in candidatos:
        if isinstance(candidato, dict):
            candidato = (
                candidato.get("nextPage")
                or candidato.get("next")
                or candidato.get("cursor")
                or candidato.get("token")
            )

        candidato = texto(candidato)

        if candidato and candidato.lower() not in ("none", "null", "false"):
            return candidato

    return ""


def descargar_categoria_api(sesion, categoria, etiqueta=""):
    articulos = []
    paginacion = ""

    for numero_pagina in range(1, MAX_PAGINAS_API + 1):
        parametros = {
            "category": categoria,
            "client": "overview",
            "lang": "es",
            "country": "ES",
        }

        if etiqueta:
            parametros["tag"] = etiqueta

        if paginacion:
            parametros["pagination"] = paginacion

        respuesta = sesion.get(
            API_NOTICIAS,
            params=parametros,
            timeout=40,
        )
        respuesta.raise_for_status()

        datos = respuesta.json()
        noticias = localizar_lista_noticias(datos)

        print(
            f"API {categoria}/{etiqueta or 'general'}, "
            f"página {numero_pagina}: {len(noticias)} noticias."
        )

        if not noticias:
            break

        encontrados_pagina = 0

        for noticia in noticias:
            articulo = convertir_noticia_json(noticia)

            if articulo:
                articulos.append(articulo)
                encontrados_pagina += 1

        siguiente = obtener_siguiente_pagina(datos)

        if not siguiente or siguiente == paginacion:
            break

        paginacion = siguiente

        if encontrados_pagina == 0:
            break

        time.sleep(0.35)

    return articulos


def descargar_desde_api():
    """
    Consulta varias ramas de noticias porque la portada combina
    noticias generales, acciones, índices, divisas, criptomonedas,
    futuros, bonos y economía.
    """
    consultas = [
        ("base", ""),
        ("base", "top_stories"),
        ("stock", ""),
        ("index", ""),
        ("forex", ""),
        ("crypto", ""),
        ("futures", ""),
        ("bond", ""),
        ("economy", ""),
    ]

    sesion = requests.Session()
    sesion.headers.update(CABECERAS)

    articulos = []

    for categoria, etiqueta in consultas:
        try:
            articulos.extend(
                descargar_categoria_api(sesion, categoria, etiqueta)
            )
        except Exception as error:
            print(
                f"AVISO: falló la consulta API "
                f"{categoria}/{etiqueta or 'general'}: {error}"
            )

    return articulos


def encontrar_fecha_en_html(elemento):
    time_element = elemento.find("time")

    if time_element:
        fecha = (
            time_element.get("datetime")
            or time_element.get("data-timestamp")
            or time_element.get_text(strip=True)
        )

        if fecha:
            return convertir_fecha(fecha)

    for atributo in ("data-published", "data-timestamp", "data-time"):
        valor = elemento.get(atributo)

        if valor:
            return convertir_fecha(valor)

    return format_datetime(datetime.now(timezone.utc))


def descargar_desde_html():
    respuesta = requests.get(
        PAGINA_NOTICIAS,
        headers=CABECERAS,
        timeout=40,
    )
    respuesta.raise_for_status()

    sopa = BeautifulSoup(respuesta.text, "html.parser")
    articulos = []

    for enlace_html in sopa.select('a[href*="/news/"]'):
        enlace = normalizar_url(enlace_html.get("href"))
        titulo = " ".join(enlace_html.get_text(" ", strip=True).split())

        if not enlace or not titulo:
            continue

        if enlace.rstrip("/") == PAGINA_NOTICIAS.rstrip("/"):
            continue

        if len(titulo) < 15:
            continue

        contenedor = enlace_html

        for _ in range(5):
            if contenedor.parent is None:
                break

            contenedor = contenedor.parent

            if contenedor.name in ("article", "li"):
                break

        fuente = ""

        if contenedor:
            posible_fuente = contenedor.select_one(
                '[class*="provider"], [class*="source"]'
            )

            if posible_fuente:
                fuente = posible_fuente.get_text(" ", strip=True)

        descripcion = ""

        if fuente:
            descripcion += (
                f"<p><strong>Fuente:</strong> {fuente}</p>"
            )

        descripcion += (
            f'<p><a href="{enlace}">Abrir noticia en TradingView</a></p>'
        )

        articulos.append(
            {
                "title": titulo,
                "link": enlace,
                "guid": enlace,
                "pubDate": encontrar_fecha_en_html(contenedor),
                "description": descripcion,
                "author": fuente,
                "categories": [fuente] if fuente else [],
                "image": "",
            }
        )

    print(f"Noticias encontradas directamente en la web: {len(articulos)}")
    return articulos


def extraer_json_incrustado():
    """
    TradingView puede incluir parte de las noticias dentro de bloques
    JSON de la propia página. Esta función los aprovecha como respaldo.
    """
    respuesta = requests.get(
        PAGINA_NOTICIAS,
        headers=CABECERAS,
        timeout=40,
    )
    respuesta.raise_for_status()

    sopa = BeautifulSoup(respuesta.text, "html.parser")
    articulos = []

    for script in sopa.find_all("script"):
        contenido = script.string or script.get_text()

        if not contenido:
            continue

        contenido = contenido.strip()

        if not contenido.startswith(("{", "[")):
            continue

        try:
            datos = json.loads(contenido)
        except json.JSONDecodeError:
            continue

        noticias = localizar_lista_noticias(datos)

        for noticia in noticias:
            articulo = convertir_noticia_json(noticia)

            if articulo:
                articulos.append(articulo)

    print(f"Noticias encontradas en JSON incrustado: {len(articulos)}")
    return articulos


def leer_articulos_anteriores():
    if not ARCHIVO_RSS.exists():
        return []

    try:
        raiz = ET.parse(ARCHIVO_RSS).getroot()
    except ET.ParseError:
        print("El rss.xml anterior no es válido; se reconstruirá.")
        return []

    articulos = []

    for item in raiz.findall("./channel/item"):
        categorias = [
            texto(categoria.text)
            for categoria in item.findall("category")
            if texto(categoria.text)
        ]

        enclosure = item.find("enclosure")
        imagen = ""

        if enclosure is not None:
            imagen = texto(enclosure.get("url"))

        articulos.append(
            {
                "title": texto(item.findtext("title")),
                "link": texto(item.findtext("link")),
                "guid": texto(item.findtext("guid")),
                "pubDate": texto(item.findtext("pubDate")),
                "description": texto(item.findtext("description")),
                "author": texto(item.findtext("author")),
                "categories": categorias,
                "image": imagen,
            }
        )

    print(f"Noticias recuperadas del RSS anterior: {len(articulos)}")
    return articulos


def clave_articulo(articulo):
    enlace = texto(articulo.get("link"))

    if enlace:
        enlace = enlace.split("?")[0].rstrip("/")
        return enlace.lower()

    return texto(
        articulo.get("guid") or articulo.get("title")
    ).lower()


def eliminar_duplicados(articulos):
    resultado = []
    vistos = set()

    for articulo in articulos:
        clave = clave_articulo(articulo)

        if not clave or clave in vistos:
            continue

        vistos.add(clave)
        resultado.append(articulo)

    return resultado


def fecha_ordenacion(articulo):
    valor = texto(articulo.get("pubDate"))

    try:
        from email.utils import parsedate_to_datetime

        fecha = parsedate_to_datetime(valor)

        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=timezone.utc)

        return fecha.timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0


def combinar_articulos(nuevos, anteriores):
    nuevos = eliminar_duplicados(nuevos)
    nuevos.sort(key=fecha_ordenacion, reverse=True)

    resultado = []
    vistos = set()

    for articulo in nuevos + anteriores:
        clave = clave_articulo(articulo)

        if not clave or clave in vistos:
            continue

        vistos.add(clave)
        resultado.append(articulo)

        if len(resultado) >= MAX_ARTICULOS_RSS:
            break

    return resultado


def añadir_texto(padre, etiqueta, valor):
    elemento = ET.SubElement(padre, etiqueta)
    elemento.text = texto(valor)
    return elemento


def escribir_rss(articulos):
    rss = ET.Element(
        "rss",
        {
            "version": "2.0",
            "xmlns:atom": "http://www.w3.org/2005/Atom",
        },
    )

    canal = ET.SubElement(rss, "channel")

    añadir_texto(canal, "title", "TradingView España — Todas las noticias")
    añadir_texto(canal, "link", PAGINA_NOTICIAS)
    añadir_texto(
        canal,
        "description",
        (
            "Todas las noticias públicas mostradas por TradingView España, "
            "sin filtros por empresa, mercado, activo o proveedor."
        ),
    )
    añadir_texto(canal, "language", "es")
    añadir_texto(
        canal,
        "lastBuildDate",
        format_datetime(datetime.now(timezone.utc)),
    )
    añadir_texto(canal, "generator", "GitHub Actions RSS Generator")

    atom = ET.SubElement(
        canal,
        "{http://www.w3.org/2005/Atom}link",
    )
    atom.set(
        "href",
        (
            "https://raw.githubusercontent.com/"
            "plis2100/tradingview-noticias-rss/main/rss.xml"
        ),
    )
    atom.set("rel", "self")
    atom.set("type", "application/rss+xml")

    for articulo in articulos:
        item = ET.SubElement(canal, "item")

        añadir_texto(item, "title", articulo["title"])
        añadir_texto(item, "link", articulo["link"])

        guid = añadir_texto(item, "guid", articulo["guid"])
        guid.set("isPermaLink", "false")

        añadir_texto(item, "pubDate", articulo["pubDate"])
        añadir_texto(item, "description", articulo["description"])

        if articulo["author"]:
            añadir_texto(item, "author", articulo["author"])

        for categoria in articulo["categories"]:
            if categoria:
                añadir_texto(item, "category", categoria)

        if articulo["image"]:
            enclosure = ET.SubElement(item, "enclosure")
            enclosure.set("url", articulo["image"])
            enclosure.set("type", "image/jpeg")

    arbol = ET.ElementTree(rss)
    ET.indent(arbol, space="  ")

    temporal = ARCHIVO_RSS.with_suffix(".xml.tmp")

    arbol.write(
        temporal,
        encoding="utf-8",
        xml_declaration=True,
    )

    temporal.replace(ARCHIVO_RSS)


def main():
    if not dentro_del_horario():
        return

    nuevos = []

    try:
        nuevos.extend(descargar_desde_api())
    except Exception as error:
        print(f"AVISO: no se pudo consultar la API: {error}")

    try:
        nuevos.extend(extraer_json_incrustado())
    except Exception as error:
        print(f"AVISO: no se pudo extraer el JSON de la página: {error}")

    try:
        nuevos.extend(descargar_desde_html())
    except Exception as error:
        print(f"AVISO: no se pudo analizar la página HTML: {error}")

    nuevos = eliminar_duplicados(nuevos)

    print(f"Noticias nuevas únicas localizadas: {len(nuevos)}")

    anteriores = leer_articulos_anteriores()

    if not nuevos and not anteriores:
        raise RuntimeError(
            "TradingView no ha devuelto ninguna noticia y no existe "
            "un RSS anterior que se pueda conservar."
        )

    if not nuevos and anteriores:
        print(
            "AVISO: no llegaron noticias nuevas. "
            "Se conservará el RSS anterior."
        )

    articulos = combinar_articulos(nuevos, anteriores)

    if not articulos:
        raise RuntimeError("No hay artículos para escribir en el RSS.")

    escribir_rss(articulos)

    print(f"RSS creado correctamente con {len(articulos)} artículos.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise
