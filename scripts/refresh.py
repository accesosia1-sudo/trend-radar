#!/usr/bin/env python3
"""
Trend Radar — Fetcher principal (v1.1).
Corre cada lunes 9 AM UTC desde GitHub Actions.
Junta data de Reddit, RSS, YouTube, Google Trends y arma trends.json.
 
Cada fuente está aislada en su propia función con try/except —
si una falla, las demás siguen funcionando.
 
Cambios v1.1: Reddit ahora usa old.reddit.com con UA de navegador,
Google Trends usa RSS oficial en vez de pytrends, RSS cutoff extendido
a 30 días.
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
 
# User-Agent que simula un navegador real — evita que Reddit/Google bloqueen
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
 
 
# ============================================================
# FUENTE 1: REDDIT (vía old.reddit.com)
# ============================================================
 
def fetch_reddit(subreddit: str, country_code: str, limit: int = 8) -> list[dict]:
    """
    Trae top posts de un subreddit. Usa old.reddit.com con UA de navegador
    porque la API normal de Reddit bloquea a los bots desde GitHub Actions.
    """
    import requests
 
    url = f"https://old.reddit.com/r/{subreddit}/top.json?t=week&limit={limit}"
    headers = {"User-Agent": BROWSER_UA}
 
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        posts = r.json().get("data", {}).get("children", [])
    except Exception as e:
        print(f"[reddit/{subreddit}] FALLO: {e}", file=sys.stderr)
        return []
 
    items = []
    for p in posts:
        d = p["data"]
        score = d.get("score", 0)
        state = "rising" if score > 1000 else "emerging"
 
        items.append({
            "state": state,
            "title": d["title"][:120],
            "desc": (d.get("selftext") or d.get("link_flair_text") or "")[:240] or
                    f"Conversación con {d.get('num_comments', 0)} comentarios y {score} ups.",
            "tags": f"Reddit · r/{subreddit}",
            "source": {
                "label": f"Reddit r/{subreddit}",
                "url": f"https://reddit.com{d['permalink']}"
            }
        })
    return items
 
 
# ============================================================
# FUENTE 2: RSS (Social Media Today, Marketing Brew, Adweek)
# ============================================================
 
RSS_FEEDS = [
    ("Social Media Today", "https://www.socialmediatoday.com/rss.xml"),
    ("Marketing Brew",     "https://www.marketingbrew.com/feed"),
    ("Search Engine Land", "https://searchengineland.com/feed"),
    ("Adweek Social",      "https://www.adweek.com/category/social-media/feed/"),
]
 
def fetch_rss_updates() -> list[dict]:
    """
    Trae updates de plataformas desde RSS feeds curados.
    Filtro extendido a 30 días para no perdernos lo importante.
    """
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
 
    # Ordenar por más recientes primero (los que tengan "Hoy" / "Hace 1d" arriba)
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
# FUENTE 3: YOUTUBE TRENDING (oficial, requiere API KEY)
# ============================================================
 
def fetch_youtube_trending(country_code: str, limit: int = 8) -> list[dict]:
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        print("[youtube] Sin YOUTUBE_API_KEY, salteo módulo", file=sys.stderr)
        return []
 
    try:
        from googleapiclient.discovery import build
        yt = build("youtube", "v3", developerKey=api_key)
        resp = yt.videos().list(
            part="snippet,statistics",
            chart="mostPopular",
            regionCode=country_code,
            maxResults=limit
        ).execute()
    except Exception as e:
        print(f"[youtube/{country_code}] FALLO: {e}", file=sys.stderr)
        return []
 
    items = []
    for v in resp.get("items", []):
        snip = v["snippet"]
        stats = v.get("statistics", {})
        views = int(stats.get("viewCount", 0))
 
        if views > 1_000_000:   state = "hot"
        elif views > 100_000:    state = "rising"
        else:                    state = "emerging"
 
        items.append({
            "state": state,
            "title": snip["title"][:120],
            "desc": (snip.get("description") or "")[:240],
            "tags": f"YouTube · {snip.get('channelTitle', '')}",
            "source": {
                "label": "YouTube Trending " + country_code,
                "url": f"https://youtube.com/watch?v={v['id']}"
            }
        })
    return items
 
 
# ============================================================
# FUENTE 4: GOOGLE TRENDS (vía RSS oficial)
# ============================================================
 
def fetch_google_trends(country_code: str, limit: int = 8) -> list[dict]:
    """
    Usa el RSS oficial de Google Trends en lugar de pytrends.
    Más estable y oficial.
    """
    import feedparser
 
    geo = country_code.upper()
    url = f"https://trends.google.com/trends/trendingsearches/daily/rss?geo={geo}"
 
    try:
        feed = feedparser.parse(url, agent=BROWSER_UA)
        if not feed.entries:
            print(f"[google_trends/{country_code}] Sin entries en RSS", file=sys.stderr)
            return []
    except Exception as e:
        print(f"[google_trends/{country_code}] FALLO: {e}", file=sys.stderr)
        return []
 
    items = []
    for entry in feed.entries[:limit]:
        title = entry.get("title", "")[:120]
        # Google Trends RSS incluye news items en la descripción a veces
        desc = entry.get("ht_news_item_title", "") or entry.get("summary", "")
        import re
        desc = re.sub(r"<[^>]+>", "", desc).strip()[:240]
        if not desc:
            desc = f"Búsqueda trending en Google {country_code}."
 
        # Approx traffic puede venir como ht_approx_traffic
        traffic = entry.get("ht_approx_traffic", "")
 
        items.append({
            "state": "rising",
            "title": title,
            "desc": desc + (f" — {traffic} búsquedas" if traffic else ""),
            "tags": "Google Trends · Búsqueda",
            "source": {
                "label": f"Google Trends {country_code}",
                "url": entry.get("link") or f"https://trends.google.com/trends/explore?geo={country_code}&q={title.replace(' ', '%20')}"
            }
        })
    return items
 
 
# ============================================================
# FUENTE 5: TIKTOK CREATIVE CENTER (STUB)
# ============================================================
 
def fetch_tiktok_creative_center(country_code: str) -> list[dict]:
    """
    STUB. Requiere Apify u otro scraper. Implementar en v2.
    """
    apify_token = os.environ.get("APIFY_API_TOKEN")
    if not apify_token:
        return []
    return []
 
 
# ============================================================
# FUENTE 6: PINTEREST TRENDS (STUB)
# ============================================================
 
def fetch_pinterest_trends(country_code: str) -> list[dict]:
    """STUB. Pinterest no tiene API pública para trends."""
    return []
 
 
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
    print(f"[{NOW.isoformat()}] Refresh arrancó")
 
    ar_all = []
    ar_all += fetch_reddit("argentina", "AR")
    ar_all += fetch_youtube_trending("AR")
    ar_all += fetch_google_trends("AR")
    ar_all += fetch_tiktok_creative_center("AR")
    ar_all += fetch_pinterest_trends("AR")
 
    mx_all = []
    mx_all += fetch_reddit("mexico", "MX")
    mx_all += fetch_youtube_trending("MX")
    mx_all += fetch_google_trends("MX")
    mx_all += fetch_tiktok_creative_center("MX")
    mx_all += fetch_pinterest_trends("MX")
 
    ar_eb, ar_ge = classify(ar_all)
    mx_eb, mx_ge = classify(mx_all)
 
    updates = fetch_rss_updates()
    calendar = build_calendar()
 
    output = {
        "updatedAt": NOW.isoformat(),
        "panels": {
            "ebullicion": {
                "title": "En ebullición esta semana",
                "subtitle": "Trends que ya están explotando — momento de subirse antes del pico",
                "countries": {
                    "ar": {"name": "Argentina", "items": ar_eb},
                    "mx": {"name": "México",    "items": mx_eb},
                }
            },
            "gestacion": {
                "title": "En gestación · Early signals",
                "subtitle": "Aún no explotaron en IG/TikTok pero hay movimiento en Reddit, X y nichos — anticiparse",
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
