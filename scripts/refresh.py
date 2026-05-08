#!/usr/bin/env python3
"""
Trend Radar - Fetcher principal (v2.3).
Corre cada lunes 9 AM UTC desde GitHub Actions.
Junta data de Apify (Instagram + TikTok), RSS y arma trends.json.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "trends.json"
NOW = datetime.now(timezone.utc)

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

IG_HASHTAGS_AR = ["argentina", "buenosaires", "caba", "rosario", "cordobaargentina", "palermo"]
IG_HASHTAGS_MX = ["mexico", "cdmx", "monterrey", "guadalajara", "mexicocity", "puebla"]
TT_HASHTAGS_AR = ["argentina", "buenosaires", "rosario", "cordoba"]
TT_HASHTAGS_MX = ["mexico", "cdmx", "monterrey", "guadalajara"]

_SPANISH_CHARS = set(["n","a","e","i","o","u","u","N","A","E","I","O","U","U","?","!"])

_SPANISH_WORDS = {
    "que","para","con","una","uno","los","las","del","como","muy",
    "esta","este","ese","eso","mas","pero","porque","todo","hay",
    "soy","eres","es","son","ser","voy","vas","ir","yo","tu","te",
    "le","lo","se","nos","mi","su","sin","por","en","al","de","la",
    "el","no","si","argentina","argentino","mexico","mexicano",
    "che","boludo","pibes","mate","asado","wey","chido","padre",
    "neta","jajaja","feliz","hoy","vida","amor","gracias","hola",
}

_ENGLISH_WORDS = {
    "the","and","for","you","with","this","that","have","from",
    "your","they","what","their","would","could","about","these",
    "those","really","because","people","happy","love","today",
    "thanks","good","better","first","after","before","just",
}


def is_spanish(text):
    if not text:
        return True
    text_clean = text.strip()
    if len(text_clean) < 10:
        return True
    if any(c in _SPANISH_CHARS for c in text_clean):
        return True
    words = re.findall(r"[a-z]+", text_clean.lower())
    if not words:
        return False
    es_count = sum(1 for w in words if w in _SPANISH_WORDS)
    en_count = sum(1 for w in words if w in _ENGLISH_WORDS)
    if es_count >= 2:
        return True
    if en_count >= 3 and en_count > es_count:
        return False
    return True


def fetch_apify_instagram(hashtags, country_code):
    apify_token = os.environ.get("APIFY_API_TOKEN")
    if not apify_token:
        print("[apify-ig] Sin APIFY_API_TOKEN", file=sys.stderr)
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
    skipped = 0
    for it in items_raw:
        caption = (it.get("caption") or "").strip()
        if not is_spanish(caption):
            skipped += 1
            continue
        likes = it.get("likesCount", 0)
        comments = it.get("commentsCount", 0)
        state = "hot" if likes > 10000 else "rising"
        title_line = caption.split("\n")[0][:100] if caption else "Post viral en Instagram"
        hashtag = it.get("hashtag") or (hashtags[0] if hashtags else "")
        items.append({
            "state": state,
            "title": title_line,
            "desc": caption[:240] if caption else "Post de Instagram con {} likes y {} comentarios.".format(likes, comments),
            "tags": "Instagram - #{} - {:,} likes".format(hashtag, likes),
            "source": {
                "label": "Instagram #{}".format(hashtag),
                "url": it.get("url") or "https://instagram.com/explore/tags/{}/".format(hashtag),
            },
        })
    if skipped:
        print("[apify-ig/{}] Filtrados por idioma: {}".format(country_code, skipped), file=sys.stderr)
    return items


def fetch_apify_tiktok(hashtags, country_code):
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
    skipped = 0
    for it in items_raw:
        text = (it.get("text") or "").strip()
        if not is_spanish(text):
            skipped += 1
            continue
        plays = it.get("playCount", 0)
        likes = it.get("diggCount", 0)
        author = (it.get("authorMeta") or {}).get("name", "")
        if plays > 1000000:
            state = "hot"
        elif plays > 100000:
            state = "rising"
        else:
            state = "emerging"
        title_line = text.split("\n")[0][:100] if text else "Video trending en TikTok"
        hashtag_used = ""
        if it.get("hashtags"):
            hashtag_used = (it["hashtags"][0] or {}).get("name", "")
        if not hashtag_used and hashtags:
            hashtag_used = hashtags[0]
        items.append({
            "state": state,
            "title": title_line,
            "desc": text[:240] if text else "Video con {:,} reproducciones y {:,} likes.".format(plays, likes),
            "tags": "TikTok - #{} - {:,} views".format(hashtag_used, plays) + (" - @{}".format(author) if author else ""),
            "source": {
                "label": "TikTok #{}".format(hashtag_used),
                "url": it.get("webVideoUrl") or "https://tiktok.com/tag/{}".format(hashtag_used),
            },
        })
    if skipped:
        print("[apify-tt/{}] Filtrados por idioma: {}".format(country_code, skipped), file=sys.stderr)
    return items


RSS_FEEDS = [
    ("Social Media Today", "https://www.socialmediatoday.com/rss.xml"),
    ("Marketing Brew",     "https://www.marketingbrew.com/feed"),
    ("Search Engine Land", "https://searchengineland.com/feed"),
    ("Adweek Social",      "https://www.adweek.com/category/social-media/feed/"),
]


def detect_platform(text):
    t = text.lower()
    if "instagram" in t or "reels" in t:
        return "Instagram"
    if "tiktok" in t:
        return "TikTok"
    if "youtube" in t or "shorts" in t:
        return "YouTube"
    if "linkedin" in t:
        return "LinkedIn"
    if "twitter" in t or "x.com" in t:
        return "X"
    if "facebook" in t:
        return "Facebook"
    return "Social"


def fetch_rss_updates():
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
                    "source": {"label": source_name, "url": entry.get("link", url)},
                })
        except Exception as e:
            print("[rss/{}] FALLO: {}".format(source_name, e), file=sys.stderr)
    return items[:12]


CALENDAR_AR = [
    {"day": 25, "month": "May", "title": "Dia de la Patria", "note": "Locro, mate, identidad. Marcas patrioticas activan.", "priority": False, "iso": "2026-05-25"},
    {"day": 11, "month": "Jun", "title": "Inicio Mundial 2026", "note": "Argentina debuta. Pico de contenido nostalgico.", "priority": True, "iso": "2026-06-11"},
    {"day": 17, "month": "Jun", "title": "Dia del Padre", "note": "Coincide con Mundial.", "priority": False, "iso": "2026-06-17"},
    {"day": 9,  "month": "Jul", "title": "Dia de la Independencia", "note": "Posible final del Mundial.", "priority": False, "iso": "2026-07-09"},
]

CALENDAR_MX = [
    {"day": 10, "month": "May", "title": "Dia de las Madres MX", "note": "Activaciones tardias aun a tiempo.", "priority": False, "iso": "2026-05-10"},
    {"day": 15, "month": "May", "title": "Dia del Maestro MX", "note": "Conversacion grande en redes mexicanas.", "priority": False, "iso": "2026-05-15"},
    {"day": 11, "month": "Jun", "title": "Inicio Mundial 2026 - sede MX", "note": "Apertura en CDMX.", "priority": True, "iso": "2026-06-11"},
    {"day": 16, "month": "Sep", "title": "Independencia de Mexico", "note": "El gran momento patriotico.", "priority": False, "iso": "2026-09-16"},
]


def build_calendar():
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
        "mx": {"name": "Mexico",    "items": filter_upcoming(CALENDAR_MX)},
    }


def classify(items):
    eb, ge = [], []
    for it in items:
        if it.get("state") in ("hot", "rising", "peak"):
            eb.append(it)
        else:
            ge.append(it)
    return eb, ge


def main():
    print("[{}] Refresh arranco (v2.3)".format(NOW.isoformat()))
    ar_all = []
    ar_all += fetch_apify_instagram(IG_HASHTAGS_AR, "AR")
    ar_all += fetch_apify_tiktok(TT_HASHTAGS_AR, "AR")
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
                "title": "En ebullicion esta semana",
                "subtitle": "Posts y videos top de Instagram y TikTok en espanol",
                "countries": {
                    "ar": {"name": "Argentina", "items": ar_eb},
                    "mx": {"name": "Mexico",    "items": mx_eb},
                },
            },
            "gestacion": {
                "title": "En gestacion - Early signals",
                "subtitle": "Contenido emergente con menos engagement pero ganando traccion",
                "countries": {
                    "ar": {"name": "Argentina", "items": ar_ge},
                    "mx": {"name": "Mexico",    "items": mx_ge},
                },
            },
            "calendario": {
                "title": "Calendario cultural - Proximos 120 dias",
                "subtitle": "Fechas que mover al brief de contenido",
                "countries": calendar,
            },
            "updates": {
                "title": "Updates de plataformas",
                "subtitle": "Cambios recientes",
                "items": updates,
            },
        },
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print("[{}] OK - escrito {}".format(NOW.isoformat(), OUTPUT_PATH))
    print("  AR ebullicion: {} | AR gestacion: {}".format(len(ar_eb), len(ar_ge)))
    print("  MX ebullicion: {} | MX gestacion: {}".format(len(mx_eb), len(mx_ge)))
    print("  Updates: {}".format(len(updates)))


if __name__ == "__main__":
    main()
