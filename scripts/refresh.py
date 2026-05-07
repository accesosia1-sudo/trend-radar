#!/usr/bin/env python3
"""
Trend Radar - Fetcher principal (v2.2).
Corre cada lunes 9 AM UTC desde GitHub Actions.
Junta data de Apify (Instagram + TikTok), RSS y arma trends.json.

v2.2: hashtags solo locales (argentinos / mexicanos), filtro de
idioma espanol obligatorio, proxyCountryCode en TikTok para que
los resultados vengan desde IPs locales.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ============================================================
# CONFIG
# ============================================================

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "trends.json"
NOW = datetime.now(timezone.utc)

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Hashtags 100% LOCALES. No usamos #fyp / #viral / #trending porque
# son globales y nos traen contenido de cualquier pais en cualquier idioma.
# Editar abajo si querés sumar / sacar.
IG_HASHTAGS_AR = ["argentina", "buenosaires", "caba", "rosario", "cordobaargentina", "palermo"]
IG_HASHTAGS_MX = ["mexico", "cdmx", "monterrey", "guadalajara", "mexicocity", "puebla"]
TT_HASHTAGS_AR = ["argentina", "buenosaires", "rosario", "cordoba"]
TT_HASHTAGS_MX = ["mexico", "cdmx", "monterrey", "guadalajara"]


# ============================================================
# FILTRO DE IDIOMA
# ============================================================

# Caracteres exclusivos del espanol
_SPANISH_CHARS = set("ñáéíóúüÑÁÉÍÓÚÜ¿¡")

# Palabras frecuentes en espanol (con espacios alrededor para match exacto)
_SPANISH_WORDS = {
    "que", "qué", "para", "con", "una", "uno", "los", "las", "del", "como",
    "muy", "esta", "está", "este", "ese", "eso", "más", "mas", "pero", "porque",
    "todo", "hay", "soy", "eres", "es", "son", "ser", "voy", "vas", "ir",
    "yo", "tu", "tú", "te", "le", "lo", "se", "nos", "mi", "su", "él", "ella",
    "sin", "por", "en", "al", "de", "la", "el", "no", "sí", "si",
    "argentina", "argentino", "argentinos", "argentinas",
    "mexico", "méxico", "mexicano", "mexicana", "mexicanos",
    "che", "boluda", "boludo", "boludez", "pibes", "pibas", "mate", "asado",
    "wey", "güey", "chido", "padre", "neta", "órale", "chingón",
    "jajaja", "jeje", "feliz", "nuevo", "hoy", "vida", "amor", "gracias",
}

# Palabras frecuentes en INGLES — si aparecen muchas, descartamos
_ENGLISH_WORDS = {
    "the", "and", "for", "you", "with", "this", "that", "have",
    "from", "your", "they", "what", "their", "would", "could", "about",
    "these", "those", "really", "because", "people", "happy", "love",
    "today", "thanks", "good", "better", "first", "after", "before",
}


def is_spanish(text: str) -> bool:
    """
    Heuristica: True si el texto parece espanol o no se puede determinar
    (vacio / muy corto). False si claramente es ingles u otro idioma.
    Permisivo a proposito (preferimos no filtrar a filtrar de mas).
    """
    if not text:
        return True
    text_clean = text.strip()
    if len(text_clean) < 10:
        # Muy corto para juzgar — dejamos pasar
        return True

    # Caracteres distintivos del espanol — match seguro
    if any(c in _SPANISH_CHARS for c in text_clean):
        return True

    # Tokenizar y contar palabras
    words = re.findall(r"\b[a-záéíóúüñ]+\b", text_clean.lower())
    if not words:
        # No tiene letras latinas — probablemente otro alfabeto (turco con tildes raros, asiatico, etc.)
        return False

    es_count = sum(1 for w in words if w in _SPANISH_WORDS)
    en_count = sum(1 for w in words if w in _ENGLISH_WORDS)

    # Si hay 2+ palabras espanolas, lo damos por valido
    if es_count >= 2:
        return True

    # Si hay claramente mas ingles que espanol, descartamos
    if en_count >= 3 and en_count > es_count:
        return False

    # Por default, no filtrar (puede ser nombre propio, hashtags, etc.)
    return True


# ============================================================
# FUENTE 1: INSTAGRAM (vía Apify)
# ============================================================

def fetch_apify_instagram(hashtags: list, country_code: str) -> list:
    """
    Scrape posts top de Instagram por hashtag usando Apify.
    Actor: apify/instagram-hashtag-scraper.
    Filtramos por idioma espanol despues de traer.
    """
    apify_token = os.environ.get("APIFY_API_TOKEN")
    if not apify_token:
        print("[apify-ig] Sin APIFY_API_TOKEN, salteo modulo", file=sys.stderr)
        return []

    try:
        from apify_client import ApifyClient
        client = ApifyClient(token=apify_token)
        run = client.actor("apify/instagram-hashtag-scraper").call(run_input={
            "hashtags": hashtags,
            "resultsLimit": 8,
        })
        items_raw = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    except Exception as e:
        print("[apify-ig/{}] FALLO: {}".format(country_code, e), file=sys.stderr)
        return []

    items = []
    skipped_lang = 0
    for it in items_raw:
        caption = (it.get("caption") or "").strip()

        # Filtro de idioma
        if not is_spanish(caption):
            skipped_lang += 1
            continue

        likes = it.get("likesCount", 0)
        comments = it.get("commentsCount", 0)
        state = "hot" if likes > 10000 else "rising"
        title_line = caption.split("\n")[0][:100] if caption else "Post viral en Instagram"
        hashtag = it.get("hashtag") or (hashtags[0] if hashtags else "")

        items.append({
            "state": state,
            "title": title_line,
            "desc": (caption[:240] if caption else
                     "Post de Instagram con {} likes y {} comentarios.".format(likes, comments)),
            "tags": "Instagram - #{} - {:,} likes".format(hashtag, likes),
            "source": {
                "label": "Instagram #{}".format(hashtag),
                "url": it.get("url") or "https://instagram.com/explore/tags/{}/".format(hashtag)
            }
        })

    if skipped_lang:
        print("[apify-ig/{}] Filtrados por idioma: {}".format(country_code, skipped_lang), file=sys.stderr)
    return items


# ============================================================
# FUENTE 2: TIKTOK (vía Apify)
# ============================================================

def fetch_apify_tiktok(hashtags: list, country_code: str) -> list:
    """
    Scrape videos top de TikTok por hashtag usando Apify.
    Actor: clockworks/tiktok-scraper.
    proxyCountryCode hace que la consulta venga desde IPs locales.
    Filtramos por idioma espanol despues.
    """
    apify_token = os.environ.get("APIFY_API_TOKEN")
    if not apify_token:
        return []

    try:
        from apify_client import ApifyClient
        client = ApifyClient(token=apify_token)
        run = client.actor("clockworks/tiktok-scraper").call(run_input={
            "hashtags": hashtags,
            "resultsPerPage": 5,
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
            "shouldDownloadSubtitles": False,
            "proxyCountryCode": country_code,
        })
        items_raw = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    except Exception as e:
        print("[apify-tt/{}] FALLO: {}".format(country_code, e), file=sys.stderr)
        return []

    items = []
    skipped_lang = 0
    for it in items_raw:
        text = (it.get("text") or "").strip()

        # Filtro de idioma
        if not is_spanish(text):
            skipped_lang += 1
            continue

        plays = it.get("playCount", 0)
        likes = it.get("diggCount", 0)
        author = (it.get("authorMeta") or {}).get("name", "")

        if plays > 1000000:    state = "hot"
        elif plays > 100000:    state = "rising"
        else:                    state = "emerging"

        title_line = text.split("\n")[0][:100] if text else "Video trending en TikTok"
        hashtag_used = ""
        if it.get("hashtags"):
            hashtag_used = (it["hashtags"][0] or {}).get("name", "")
        if not hashtag_used and hashtags:
            hashtag_used = hashtags[0]

        items.append({
            "state": state,
            "title": title_line,
            "desc": (text[:240] if text else
                     "Video con {:,} reproducciones y {:,} likes.".format(plays, likes)),
            "tags": "TikTok - #{} - {:,} views".format(hashtag_used, plays) + (" - @{}".format(author) if author else ""),
            "source": {
                "label": "TikTok #{}".format(hashtag_used),
                "url": it.get("webVideoUrl") or "https://tiktok.com/tag/{}".format(hashtag_used)
            }
        })

    if skipped_lang:
        print("[apify-tt/{}] Filtrados por idioma: {}".format(country_code, skipped_lang), file=sys.stderr)
    return items


# ============================================================
# FUENTE 3: RSS (Updates de plataformas)
# ============================================================

RSS_FEEDS = [
    ("Social Media Today", "https://www.socialmediatoday.com/rss.xml"),
    ("Marketing Brew",     "https://www.marketingbrew.com/feed"),
    ("Search Engine Land", "https://searchengineland.com/feed"),
    ("Adweek Social",      "https://www.adweek.com/category/social-media/feed/"),
]

def fetch_rss_updates() -> list:
    import feedparser
    items = []
    cutoff = NOW - timedelta(days=30)

    for source_name, url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url, agent=BROWSER_UA)
            for entry in feed.entries[:8]:
                pub = entry.get("published_parsed") or entry.get("updated_parsed")
                if pub:
                    pub_dt = datetime(*pub[:6], tzinfo=timezone.utc)
                    if pub_dt < cutoff:
                        continue
                    days_ago = (NOW - pub_dt).days
                    when = "Hace {}d".format(days_ago) if days_ago > 0 else "Hoy"
                else:
                    when = "Reciente"

                title = entry.get("title", "")[:120]
                summary = entry.get("summary", "")[:240]
                summary = re.sub(r"<[^>]+>", "", summary).strip()

                items.append({
                    "platform": detect_platform(title + " " + summary),
                    "title": title,
                    "desc": summary,
                    "when": when,
                    "source": {"label": source_name, "url": entry.get("link", url)}
                })
        except Exception as e:
            print("[rss/{}] FALLO: {}".format(source_name, e), file=sys.stderr)

    return items[:12]


def detect_platform(text: str) -> str:
    t = text.lower()
    if "instagram" in t or "reels" in t or " ig " in t: return "Instagram"
    if "tiktok" in t: return "TikTok"
    if "youtube" in t or "shorts" in t: return "YouTube"
    if "linkedin" in t: return "LinkedIn"
    if "twitter" in t or " x " in t.lower() or "x.com" in t: return "X"
    if "facebook" in t or " fb " in t: return "Facebook"
    return "Social"


# ============================================================
# CALENDARIO CULTURAL (HARDCODED — editar aqui)
# ============================================================

CALENDAR_AR = [
    {"day": 25, "month": "May", "title": "Dia de la Patria",       "note": "Locro, mate, identidad. Marcas patrioticas activan.", "priority": False, "iso": "2026-05-25"},
    {"day": 11, "month": "Jun", "title": "Inicio Mundial 2026",    "note": "Argentina debuta. Pico de contenido nostalgico y activacion.", "priority": True,  "iso": "2026-06-11"},
    {"day": 17, "month": "Jun", "title": "Dia del Padre",          "note": "Coincide con Mundial — aprovechar el cruce.", "priority": False, "iso": "2026-06-17"},
    {"day": 9,  "month": "Jul", "title": "Dia de la Independencia","note": "Posible final del Mundial. Mega-momento si Argentina llega.", "priority": False, "iso": "2026-07-09"},
]

CALENDAR_MX = [
    {"day": 10, "month": "May", "title": "Dia de las Madres MX",        "note": "Activaciones tardias aun a tiempo.", "priority": False, "iso": "2026-05-10"},
    {"day": 15, "month": "May", "title": "Dia del Maestro MX",          "note": "Conversacion grande en redes mexicanas.", "priority": False, "iso": "2026-05-15"},
    {"day": 11, "month": "Jun", "title": "Inicio Mundial 2026 — sede MX","note": "Apertura en CDMX. Oportunidad gigante hoteleria y travel.", "priority": True,  "iso": "2026-06-11"},
    {"day": 16, "month": "Sep", "title": "Independencia de Mexico",     "note": "El gran momento patriotico. Prep desde julio.", "priority": False, "iso": "2026-09-16"},
]

def build_calendar() -> dict:
    today = NOW.date()
    cutoff = today + timedelta(days=120)

    def filter_upcoming(events):
        out = []
        for e in events:
            try:
                ev_date = datetime.fromisoformat(e["iso"]).date()
                if today <= ev_date <= cutoff:
                    out.append(e)
            except Exception:
                pass


if __name__ == "__main__":
    main()
