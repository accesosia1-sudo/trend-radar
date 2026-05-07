#!/usr/bin/env python3
"""
Trend Radar — Fetcher principal (v2.0).
Corre cada lunes 9 AM UTC desde GitHub Actions.
Junta data de Apify (Instagram + TikTok), RSS y arma trends.json.

Cambios v2.0: Apify integrado para Instagram y TikTok (las dos
plataformas que importan), Reddit + Google Trends descartados
porque no funcionan estables sin OAuth.

Cada fuente está aislada en try/except — si una falla, las demás
siguen.
"""

import json
import os
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

# Hashtags a trackear por país. SON AMPLIOS A PROPÓSITO — capturan
# lo que está rompiendo culturalmente, no temas específicos.
# Editá las listas si querés enfocar a un cliente o vertical particular.
IG_HASHTAGS_AR = ["fyp", "viral", "parati", "trending", "argentina"]
IG_HASHTAGS_MX = ["fyp", "viral", "parati", "trending", "mexico"]
TT_HASHTAGS_AR = ["fyp", "parati", "viral", "argentina"]
TT_HASHTAGS_MX = ["fyp", "parati", "viral", "mexico"]


# ============================================================
# FUENTE 1: INSTAGRAM (vía Apify)
# ============================================================

def fetch_apify_instagram(hashtags: list[str], country_code: str) -> list[dict]:
    """
    Scrape posts top de Instagram por hashtag usando Apify.
    Actor usado: apify/instagram-hashtag-scraper (~USD 0.50 / 1000 posts).
    Limitamos a 5 posts por hashtag para mantener costo bajo.
    """
    apify_token = os.environ.get("APIFY_API_TOKEN")
    if not apify_token:
        print("[apify-ig] Sin APIFY_API_TOKEN, salteo módulo", file=sys.stderr)
        return []

    try:
        from apify_client import ApifyClient
        client = ApifyClient(token=apify_token)
        run = client.actor("apify/instagram-hashtag-scraper").call(run_input={
            "hashtags": hashtags,
            "resultsLimit": 5,
        })
        items_raw = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    except Exception as e:
        print(f"[apify-ig/{country_code}] FALLO: {e}", file=sys.stderr)
        return []

    items = []
    for it in items_raw:
        caption = (it.get("caption") or "").strip()
        likes = it.get("likesCount", 0)
        comments = it.get("commentsCount", 0)

        # Heuristica de estado: muchos likes = hot, sino rising
        state = "hot" if likes > 10000 else "rising"

        # Title: primera línea del caption o fallback
        title_line = caption.split("\n")[0][:100] if caption else "Post viral en Instagram"

        # Hashtag usado para este post (intentamos extraer del caption o usamos primero del input)
        hashtag = it.get("hashtag") or (hashtags[0] if hashtags else "")

        items.append({
            "state": state,
            "title": title_line,
            "desc": (caption[:240] if caption else
                     f"Post de Instagram con {likes} likes y {comments} comentarios."),
            "tags": f"Instagram · #{hashtag} · {likes:,} likes",
            "source": {
                "label": f"Instagram #{hashtag}",
                "url": it.get("url") or f"https://instagram.com/explore/tags/{hashtag}/"
            }
        })
    return items


# ============================================================
# FUENTE 2: TIKTOK (vía Apify)
# ============================================================

def fetch_apify_tiktok(hashtags: list[str], country_code: str) -> list[dict]:
    """
    Scrape videos top de TikTok por hashtag usando Apify.
    Actor: clockworks/tiktok-scraper.
    Limitamos a 5 videos por hashtag.
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
        })
        items_raw = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    except Exception as e:
        print(f"[apify-tt/{country_code}] FALLO: {e}", file=sys.stderr)
        return []

    items = []
    for it in items_raw:
        text = (it.get("text") or "").strip()
        plays = it.get("playCount", 0)
        likes = it.get("diggCount", 0)
        author = (it.get("authorMeta") or {}).get("name", "")

        # Heuristica de estado
        if plays > 1_000_000:    state = "hot"
        elif plays > 100_000:    state = "rising"
        else:                    state = "emerging"

        title_line = text.split("\n")[0][:100] if text else "Video trending en TikTok"
        hashtag_used = (it.get("hashtags") or [{}])[0].get("name", "") if it.get("hashtags") else ""
        if not hashtag_used and hashtags:
            hashtag_used = hashtags[0]

        items.append({
            "state": state,
            "title": title_line,
            "desc": (text[:240] if text else
                     f"Video con {plays:,} reproducciones y {likes:,} likes."),
            "tags": f"TikTok · #{hashtag_used} · {plays:,} views" + (f" · @{author}" if author else ""),
            "source": {
                "label": f"TikTok #{hashtag_used}",
                "url": it.get("webVideoUrl") or f"https://tiktok.com/tag/{hashtag_used}"
            }
        })
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

def fetch_rss_updates() -> list[dict]:
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
                    when = f"Hace {days_ago}d" if days_ago > 0 else "Hoy"
                else:
                    when = "Reciente"

                title = entry.get("title", "")[:120]
                summary = entry.get("summary", "")[:240]
                import re
                summary = re.sub(r"<[^>]+>", "", summary).strip()

                items.append({
                    "platform": detect_platform(title + " " + summary),
                    "title": title,
                    "desc": summary,
                    "when": when,
                    "source": {"label": source_name, "url": entry.get("link", url)}
                })
        except Exception as e:
            print(f"[rss/{source_name}] FALLO: {e}", file=sys.stderr)

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
# CALENDARIO CULTURAL (HARDCODED — editar aquí)
# ============================================================

CALENDAR_AR = [
    {"day": 25, "month": "May", "title": "Día de la Patria",       "note": "Locro, mate, identidad. Marcas patrióticas activan.", "priority": False, "iso": "2026-05-25"},
    {"day": 11, "month": "Jun", "title": "Inicio Mundial 2026",    "note": "Argentina debuta. Pico de contenido nostálgico y activación.", "priority": True,  "iso": "2026-06-11"},
    {"day": 17, "month": "Jun", "title": "Día del Padre",          "note": "Coincide con Mundial — aprovechar el cruce.", "priority": False, "iso": "2026-06-17"},
    {"day": 9,  "month": "Jul", "title": "Día de la Independencia","note": "Posible final del Mundial. Mega-momento si Argentina llega.", "priority": False, "iso": "2026-07-09"},
]

CALENDAR_MX = [
    {"day": 10, "month": "May", "title": "Día de las Madres MX",        "note": "Activaciones tardías aún a tiempo.", "priority": False, "iso": "2026-05-10"},
    {"day": 15, "month": "May", "title": "Día del Maestro MX",          "note": "Conversación grande en redes mexicanas.", "priority": False, "iso": "2026-05-15"},
    {"day": 11, "month": "Jun", "title": "Inicio Mundial 2026 — sede MX","note": "Apertura en CDMX. Oportunidad gigante hotelería y travel.", "priority": True,  "iso": "2026-06-11"},
    {"day": 16, "month": "Sep", "title": "Independencia de México",     "note": "El gran momento patriótico. Prep desde julio.", "priority": False, "iso": "2026-09-16"},
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
        return out

    return {
        "ar": {"name": "Argentina", "items": filter_upcoming(CALENDAR_AR)},
        "mx": {"name": "México",    "items": filter_upcoming(CALENDAR_MX)},
    }


# ============================================================
# CLASIFICADOR
# ============================================================

def classify(items: list[dict]) -> tuple[list[dict], list[dict]]:
    eb, ge = [], []
    for it in items:
        if it.get("state") in ("hot", "rising", "peak"):
            eb.append(it)
        else:
            ge.append(it)
    return eb, ge


# ============================================================
# MAIN
# ============================================================

def main():
    print(f"[{NOW.isoformat()}] Refresh arrancó (v2.0 con Apify)")

    # AR — Instagram + TikTok vía Apify
    ar_all = []
    ar_all += fetch_apify_instagram(IG_HASHTAGS_AR, "AR")
    ar_all += fetch_apify_tiktok(TT_HASHTAGS_AR, "AR")

    # MX
    mx_all = []
    mx_all += fetch_apify_instagram(IG_HASHTAGS_MX, "MX")
    mx_all += fetch_apify_tiktok(TT_HASHTAGS_MX, "MX")

    ar_eb, ar_ge = classify(ar_all)
    mx_eb, mx_ge = classify(mx_all)

    updates = fetch_rss_updates()
    calendar = build_calendar()

    output = {
        "updatedAt": NOW.isoformat(),
        "panels": {
            "ebullicion": {
                "title": "En ebullición esta semana",
                "subtitle": "Posts y videos top de Instagram y TikTok — momento de subirse antes del pico",
                "countries": {
                    "ar": {"name": "Argentina", "items": ar_eb},
                    "mx": {"name": "México",    "items": mx_eb},
                }
            },
            "gestacion": {
                "title": "En gestación · Early signals",
                "subtitle": "Contenido emergente con menos plays/likes pero ganando tracción — anticiparse",
                "countries": {
                    "ar": {"name": "Argentina", "items": ar_ge},
                    "mx": {"name": "México",    "items": mx_ge},
                }
            },
            "calendario": {
                "title": "Calendario cultural · Próximos 120 días",
                "subtitle": "Fechas que mover al brief de contenido — planeá con tiempo",
                "countries": calendar,
            },
            "updates": {
                "title": "Updates de plataformas",
                "subtitle": "Cambios recientes — la mayoría aplican global, marcamos cuando hay diferencia regional",
                "items": updates,
            }
        }
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"[{NOW.isoformat()}] OK — escrito {OUTPUT_PATH}")
    print(f"  AR ebullición: {len(ar_eb)} | AR gestación: {len(ar_ge)}")
    print(f"  MX ebullición: {len(mx_eb)} | MX gestación: {len(mx_ge)}")
    print(f"  Updates: {len(updates)}")


if __name__ == "__main__":
    main()
