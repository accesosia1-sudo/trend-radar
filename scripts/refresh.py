#!/usr/bin/env python3
"""
Trend Radar - Fetcher v3.1 (Slack + titulos reales).
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

SLACK_CHANNEL_ID = "C09MLB0D0PN"


def extract_urls(text):
    urls = []
    for m in re.finditer(r'<(https?://[^|>]+)(?:\|[^>]*)?>', text):
        urls.append(m.group(1))
    if not urls:
        urls = re.findall(r'https?://[^\s<>|]+', text)
    return urls


def clean_text(text):
    text = re.sub(r'<https?://[^>]+>', '', text)
    text = re.sub(r'<@U\w+(?:\|[^>]+)?>', '', text)
    text = re.sub(r'<#C\w+(?:\|[^>]+)?>', '', text)
    text = re.sub(r':[a-z0-9_+-]+:', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def detect_platform(url):
    if "instagram.com" in url: return "Instagram"
    if "tiktok.com" in url: return "TikTok"
    if "youtube.com" in url or "youtu.be" in url: return "YouTube"
    if "twitter.com" in url or "x.com" in url: return "X"
    if "linkedin.com" in url: return "LinkedIn"
    return "Web"


def fetch_oembed_title(url):
    import requests
    try:
        if "tiktok.com" in url:
            r = requests.get(
                "https://www.tiktok.com/oembed",
                params={"url": url},
                timeout=8,
                headers={"User-Agent": BROWSER_UA},
            )
        elif "youtube.com" in url or "youtu.be" in url:
            r = requests.get(
                "https://www.youtube.com/oembed",
                params={"url": url, "format": "json"},
                timeout=8,
            )
        else:
            return None
        if r.status_code != 200:
            return None
        d = r.json()
        title = (d.get("title") or "").strip()
        author = (d.get("author_name") or "").strip()
        if not title:
            return None
        if author and author.lower() not in title.lower():
            return ("{} — @{}".format(title, author))[:130]
        return title[:130]
    except Exception:
        return None


def fetch_og_title(url):
    import requests
    try:
        r = requests.get(
            url,
            headers={"User-Agent": BROWSER_UA},
            timeout=8,
            allow_redirects=True,
        )
        if r.status_code != 200:
            return None
        m = re.search(
            r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
            r.text, re.IGNORECASE,
        )
        if not m:
            m = re.search(
                r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']og:title["\']',
                r.text, re.IGNORECASE,
            )
        if m:
            title = m.group(1).strip()
            return title[:130] if title else None
    except Exception:
        pass
    return None


def get_link_title(url, fallback_text, author):
    title = fetch_oembed_title(url)
    if title:
        return title
    title = fetch_og_title(url)
    if title:
        return title
    if fallback_text:
        return fallback_text[:100]
    return "Link compartido por {}".format(author)


def fetch_slack_links(channel_id, days_back=30):
    import requests
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        print("[slack] Sin SLACK_BOT_TOKEN", file=sys.stderr)
        return [], []

    oldest = (NOW - timedelta(days=days_back)).timestamp()
    recent_cutoff = (NOW - timedelta(days=7)).timestamp()

    try:
        r = requests.get(
            "https://slack.com/api/conversations.history",
            headers={"Authorization": "Bearer " + token},
            params={"channel": channel_id, "oldest": str(oldest), "limit": 200},
            timeout=20,
        )
        data = r.json()
        if not data.get("ok"):
            print("[slack] API error: " + str(data.get("error")), file=sys.stderr)
            return [], []
        messages = data.get("messages", [])
    except Exception as e:
        print("[slack] FALLO: " + str(e), file=sys.stderr)
        return [], []

    user_cache = {}

    def get_user_name(uid):
        if not uid:
            return "Equipo"
        if uid in user_cache:
            return user_cache[uid]
        try:
            r = requests.get(
                "https://slack.com/api/users.info",
                headers={"Authorization": "Bearer " + token},
                params={"user": uid},
                timeout=10,
            )
            d = r.json()
            if d.get("ok"):
                u = d["user"]
                name = (u.get("profile", {}).get("display_name") or
                        u.get("real_name") or
                        u.get("name") or "Equipo")
                user_cache[uid] = name
                return name
        except Exception:
            pass
        user_cache[uid] = "Equipo"
        return "Equipo"

    recent, older = [], []

    for msg in messages:
        if msg.get("subtype") in ("bot_message", "channel_join", "channel_leave"):
            continue
        text = msg.get("text", "") or ""
        urls = extract_urls(text)
        if not urls:
            continue

        ts = float(msg.get("ts", 0))
        if ts == 0:
            continue
        msg_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        days_ago = (NOW - msg_dt).days
        when = "Hoy" if days_ago == 0 else "Hace {}d".format(days_ago)

        user_id = msg.get("user", "")
        author = get_user_name(user_id)
        context = clean_text(text)

        for url in urls[:2]:
            platform = detect_platform(url)
            real_title = get_link_title(url, context, author)
            if context and context.strip():
                desc = "{} compartio: {}".format(author, context[:240])
            else:
                desc = "Compartido por {} sin comentario adicional.".format(author)

            item = {
                "state": "hot" if ts >= recent_cutoff else "rising",
                "title": real_title,
                "desc": desc,
                "tags": "{} - Compartido por {} - {}".format(platform, author, when),
                "source": {
                    "label": platform + " (via Slack)",
                    "url": url,
                },
            }

            if ts >= recent_cutoff:
                recent.append(item)
            else:
                older.append(item)

    return recent, older


RSS_FEEDS = [
    ("Social Media Today", "https://www.socialmediatoday.com/rss.xml"),
    ("Marketing Brew",     "https://www.marketingbrew.com/feed"),
    ("Search Engine Land", "https://searchengineland.com/feed"),
    ("Adweek Social",      "https://www.adweek.com/category/social-media/feed/"),
]


def detect_rss_platform(text):
    t = text.lower()
    if "instagram" in t or "reels" in t: return "Instagram"
    if "tiktok" in t: return "TikTok"
    if "youtube" in t or "shorts" in t: return "YouTube"
    if "linkedin" in t: return "LinkedIn"
    if "twitter" in t or "x.com" in t: return "X"
    if "facebook" in t: return "Facebook"
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
                    "platform": detect_rss_platform(title + " " + summary),
                    "title": title,
                    "desc": summary,
                    "when": when,
                    "source": {"label": source_name, "url": entry.get("link", url)},
                })
        except Exception as e:
            print("[rss/{}] FALLO: {}".format(source_name, e), file=sys.stderr)
    return items[:12]


CALENDAR_AR = [
    {"day": 25, "month": "May", "title": "Dia de la Patria", "note": "Locro, mate, identidad.", "priority": False, "iso": "2026-05-25"},
    {"day": 11, "month": "Jun", "title": "Inicio Mundial 2026", "note": "Argentina debuta.", "priority": True, "iso": "2026-06-11"},
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


def main():
    print("[{}] Trend Radar v3.1 (Slack + titulos reales)".format(NOW.isoformat()))
    recent, older = fetch_slack_links(SLACK_CHANNEL_ID, days_back=30)
    print("  Slack recientes (<7d): {}".format(len(recent)))
    print("  Slack mas viejos (7-30d): {}".format(len(older)))

    updates = fetch_rss_updates()
    calendar = build_calendar()

    output = {
        "updatedAt": NOW.isoformat(),
        "panels": {
            "ebullicion": {
                "title": "En ebullicion esta semana",
                "subtitle": "Links curados por el equipo en #ideas-trends-reels-tiktoks (ultimos 7 dias)",
                "items": recent,
            },
            "gestacion": {
                "title": "En gestacion - Compartido hace 1-4 semanas",
                "subtitle": "Trends que el equipo flageo entre 7 y 30 dias atras",
                "items": older,
            },
            "calendario": {
                "title": "Calendario cultural - Proximos 120 dias",
                "subtitle": "Fechas que mover al brief de contenido",
                "countries": calendar,
            },
            "updates": {
                "title": "Updates de plataformas",
                "subtitle": "Cambios recientes desde RSS feeds",
                "items": updates,
            },
        },
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print("[{}] OK - escrito {}".format(NOW.isoformat(), OUTPUT_PATH))


if __name__ == "__main__":
    main()
