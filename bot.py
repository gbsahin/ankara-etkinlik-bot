"""
Ankara Events Bot — Enhanced Edition
Features:
  - Fuzzy deduplication (title similarity, not just URL)
  - Skips image-less events in digest (quality over quantity)
  - AI-written event summaries via Claude API
  - Daily digest grouped by category (one message per category)
  - Inline Telegram buttons: Get Tickets / Save Date
  - Friday weekly top-picks edition (editorial AI pick of 3)
"""

import requests
from bs4 import BeautifulSoup
import os
import json
import hashlib
import time
import signal
import anthropic
from datetime import datetime
from difflib import SequenceMatcher

# ─────────────────────────────────────────
# Config
# ─────────────────────────────────────────
TOKEN            = os.getenv('TELEGRAM_TOKEN')
CHAT_ID          = os.getenv('TELEGRAM_CHAT_ID')
ANTHROPIC_KEY    = os.getenv('ANTHROPIC_API_KEY')
SEEN_EVENTS_FILE = "seen_events.json"

MAX_RUN_SECONDS  = 360
MAX_OG_FETCHES   = 25
FUZZY_THRESHOLD  = 0.82   # titles this similar → treated as duplicate
MAX_AI_SUMMARIES = 30     # cap Claude API calls per run

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'tr-TR,tr;q=0.9,en;q=0.8',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

BILETIX_IGNORE_KEYWORDS = [
    'myaccount', 'my-tickets', 'business.ticketmaster', 'trust.ticketmaster',
    'privacy.ticketmaster', 'developer.ticketmaster', 'tiktok.com',
    'play.google.com', 'bize-ulasin', 'affiliate', 'cookie', 'gizlilik',
    'reklam', 'account'
]

# Category keywords → bucket mapping
CATEGORY_MAP = {
    "🎵 Müzik / Konser": ["konser", "müzik", "music", "concert", "jazz", "rock", "pop",
                           "klasik", "opera", "festival", "sahne", "gece"],
    "🎭 Tiyatro & Gösteri": ["tiyatro", "theatre", "theater", "oyun", "gösteri",
                              "stand-up", "comedy", "komedi", "dans", "bale"],
    "🏛 Ücretsiz & Kültür": ["sergi", "exhibition", "müze", "museum", "kültür",
                              "ücretsiz", "free", "açık", "festival", "ankara"],
    "🎪 Diğer Etkinlikler": [],
}

SOURCE_STYLE = {
    "Biletix":    {"icon": "🎟", "label": "Biletix"},
    "filAnkara":  {"icon": "📰", "label": "filAnkara"},
    "Eventbrite": {"icon": "🌍", "label": "Eventbrite"},
    "Biletinial": {"icon": "🎫", "label": "Biletinial"},
    "BiletimGO":  {"icon": "🎪", "label": "BiletimGO"},
    "Mobilet":    {"icon": "🎭", "label": "Mobilet"},
    "ABB":        {"icon": "🏛", "label": "Ankara Büyükşehir"},
    "LaKonser":   {"icon": "🎵", "label": "LaKonser"},
}


# ─────────────────────────────────────────
# Timeout guard
# ─────────────────────────────────────────
class BotTimeoutError(Exception):
    pass

def _timeout_handler(signum, frame):
    raise BotTimeoutError("Exceeded max runtime.")

def set_global_timeout(seconds):
    try:
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(seconds)
    except AttributeError:
        pass

def cancel_global_timeout():
    try:
        signal.alarm(0)
    except AttributeError:
        pass


# ─────────────────────────────────────────
# Deduplication — fuzzy + hash
# ─────────────────────────────────────────
def load_seen_events():
    if os.path.exists(SEEN_EVENTS_FILE):
        with open(SEEN_EVENTS_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen_events(seen):
    with open(SEEN_EVENTS_FILE, "w") as f:
        json.dump(list(seen), f)

def event_hash(title, link):
    return hashlib.md5(f"{title}{link}".encode()).hexdigest()

def _normalize(title):
    import re
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', '', title.lower())).strip()

def fuzzy_deduplicate(events):
    """Remove events whose titles are suspiciously similar to an already-kept event."""
    kept = []
    kept_titles = []
    for ev in events:
        norm = _normalize(ev['title'])
        duplicate = any(
            SequenceMatcher(None, norm, kt).ratio() >= FUZZY_THRESHOLD
            for kt in kept_titles
        )
        if not duplicate:
            kept.append(ev)
            kept_titles.append(norm)
    return kept

def url_deduplicate(events):
    seen_links = set()
    unique = []
    for e in events:
        if e['link'] not in seen_links:
            seen_links.add(e['link'])
            unique.append(e)
    return unique


# ─────────────────────────────────────────
# Category classifier
# ─────────────────────────────────────────
def classify_event(title):
    lower = title.lower()
    for category, keywords in CATEGORY_MAP.items():
        if any(kw in lower for kw in keywords):
            return category
    return "🎪 Diğer Etkinlikler"


# ─────────────────────────────────────────
# AI summary via Claude
# ─────────────────────────────────────────
def get_ai_summary(title, link, ai_counter):
    if not ANTHROPIC_KEY:
        return None
    if ai_counter.get('count', 0) >= MAX_AI_SUMMARIES:
        return None
    ai_counter['count'] = ai_counter.get('count', 0) + 1
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        prompt = (
            f"Etkinlik adı: {title}\n"
            f"Link: {link}\n\n"
            "Bu Ankara etkinliği için kısa, samimi ve merak uyandırıcı 1-2 cümlelik bir Türkçe açıklama yaz. "
            "Neden gitmeye değer? Kitlesi kim? Öne çıkan özelliği ne? "
            "Abartmadan, doğal bir dille yaz. Emoji kullanma. Sadece açıklama metnini döndür."
        )
        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}]
        )
        summary = message.content[0].text.strip()
        return summary if len(summary) > 10 else None
    except Exception as e:
        print(f"[AI Özet Hata] {e}")
        return None


# ─────────────────────────────────────────
# AI weekly top picks
# ─────────────────────────────────────────
def get_weekly_top_picks(events):
    if not ANTHROPIC_KEY or not events:
        return []
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        event_list = "\n".join(
            f"{i+1}. {e['title']} ({e['source']})" for i, e in enumerate(events[:40])
        )
        prompt = (
            f"Aşağıda bu haftaki Ankara etkinlikleri listesi var:\n\n{event_list}\n\n"
            "Bunlardan en ilgi çekici, en geniş kitleye hitap eden 3 tanesini seç. "
            "Sadece seçtiğin etkinliklerin numaralarını virgülle döndür (örn: 3,7,12). "
            "Başka hiçbir şey yazma."
        )
        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=20,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = message.content[0].text.strip()
        indices = [int(x.strip()) - 1 for x in raw.split(',') if x.strip().isdigit()]
        return [events[i] for i in indices if 0 <= i < len(events)]
    except Exception as e:
        print(f"[Top Picks Hata] {e}")
        return []


# ─────────────────────────────────────────
# Image fetcher
# ─────────────────────────────────────────
def get_og_image(url, fetch_counter=None, max_fetches=MAX_OG_FETCHES):
    if fetch_counter is not None:
        if fetch_counter.get('count', 0) >= max_fetches:
            return None
        fetch_counter['count'] = fetch_counter.get('count', 0) + 1
    try:
        res = requests.get(url, headers=HEADERS, timeout=6)
        if res.status_code != 200:
            return None
        soup = BeautifulSoup(res.text, 'html.parser')
        for attr in ['og:image', 'twitter:image']:
            tag = soup.find('meta', property=attr) or soup.find('meta', attrs={'name': attr})
            if tag and tag.get('content'):
                img_url = tag['content'].strip()
                if img_url.startswith('http'):
                    return img_url
        for img in soup.find_all('img', src=True):
            src = img['src']
            if src.startswith('http') and any(ext in src for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                return src
    except Exception:
        pass
    return None


# ─────────────────────────────────────────
# Telegram senders
# ─────────────────────────────────────────
def _inline_keyboard(link):
    return {
        "inline_keyboard": [[
            {"text": "🎟 Bilet Al", "url": link},
            {"text": "📅 Takvime Ekle", "callback_data": "save_date"},
        ]]
    }

def send_photo_message(image_url, caption, reply_markup=None):
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    payload = {
        "chat_id": CHAT_ID,
        "photo": image_url,
        "caption": caption,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        r = requests.post(url, json=payload, timeout=15)
        return r.status_code == 200
    except Exception:
        return False

def send_text_message(text, reply_markup=None, disable_preview=True):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview,
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            print(f"[Telegram HATA] {r.status_code}: {r.text}")
            return False
        return True
    except Exception as e:
        print(f"[Telegram İstisna] {e}")
        return False

def send_event(event):
    title   = event['title']
    link    = event['link']
    source  = event['source']
    image   = event.get('image')
    summary = event.get('summary', '')

    style = SOURCE_STYLE.get(source, {"icon": "📅", "label": source})
    summary_line = f"\n💬 <i>{summary}</i>\n" if summary else "\n"

    caption = (
        f"{style['icon']} <b>{title}</b>"
        f"{summary_line}"
        f"\n🔗 <a href='{link}'>Etkinlik Sayfası</a>"
        f"\n<i>{style['label']} · Ankara</i>"
    )

    markup = _inline_keyboard(link)

    if image:
        return send_photo_message(image, caption, reply_markup=markup)
    return False  # skip image-less events from digest

def send_digest_header(category, count):
    today = datetime.now().strftime("%d %B %Y")
    text = (
        f"\n{category}\n"
        f"<i>{today} · {count} etkinlik</i>\n"
        f"{'─' * 20}"
    )
    send_text_message(text, disable_preview=True)

def send_daily_intro():
    today = datetime.now().strftime("%A, %d %B %Y")
    is_friday = datetime.now().weekday() == 4
    if is_friday:
        header = "🏆 <b>Haftanın Öne Çıkanları</b>"
        sub = "Bu haftanın en dikkat çekici Ankara etkinlikleri."
    else:
        header = "📍 <b>Ankara'da Bugün & Yakında</b>"
        sub = "Günün taze etkinlik listesi."
    send_text_message(f"{header}\n<i>{today}</i>\n{sub}", disable_preview=True)

def send_weekly_top_picks(picks, ai_counter):
    if not picks:
        return
    send_text_message(
        "🏆 <b>Bu Haftanın Editör Seçimleri</b>\n<i>Pek çok etkinlik arasından öne çıkanlar:</i>",
        disable_preview=True
    )
    for i, event in enumerate(picks, 1):
        if not event.get('summary'):
            event['summary'] = get_ai_summary(event['title'], event['link'], ai_counter)
        title   = event['title']
        link    = event['link']
        source  = event['source']
        image   = event.get('image')
        summary = event.get('summary', '')
        style   = SOURCE_STYLE.get(source, {"icon": "📅", "label": source})

        caption = (
            f"🥇 <b>#{i} Editör Seçimi</b>\n\n"
            f"{style['icon']} <b>{title}</b>\n"
            f"{'💬 <i>' + summary + '</i>' if summary else ''}\n\n"
            f"🔗 <a href='{link}'>Etkinlik Sayfası</a>\n"
            f"<i>{style['label']} · Ankara</i>"
        )
        markup = _inline_keyboard(link)
        if image:
            send_photo_message(image, caption, reply_markup=markup)
        else:
            send_text_message(caption, reply_markup=markup)
        time.sleep(1)


# ─────────────────────────────────────────
# Scrapers
# ─────────────────────────────────────────
def scrape_biletix():
    events = []
    category_urls = [
        "https://www.biletix.com/category/MUSIC/ANKARA/tr",
        "https://www.biletix.com/category/THEATRE/ANKARA/tr",
        "https://www.biletix.com/category/COMEDY/ANKARA/tr",
        "https://www.biletix.com/category/SPORTS/ANKARA/tr",
        "https://www.biletix.com/category/ARTS/ANKARA/tr",
        "https://www.biletix.com/anasayfa/ANKARA/tr",
    ]
    seen_slugs = set()
    fetch_counter = {'count': 0}
    for url in category_urls:
        try:
            res = requests.get(url, headers=HEADERS, timeout=15)
            if res.status_code != 200:
                continue
            soup = BeautifulSoup(res.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a['href']
                if '/etkinlik/' not in href:
                    continue
                if any(bad in href for bad in BILETIX_IGNORE_KEYWORDS):
                    continue
                title = a.get_text(strip=True)
                if not title or len(title) < 4:
                    continue
                if href in seen_slugs:
                    continue
                seen_slugs.add(href)
                link = href if href.startswith('http') else f"https://www.biletix.com{href}"
                image = get_og_image(link, fetch_counter=fetch_counter)
                time.sleep(0.3)
                events.append({"title": title, "link": link, "source": "Biletix", "image": image})
        except Exception as e:
            print(f"[Biletix Hata] {url}: {e}")
    print(f"Biletix: {len(events)} etkinlik")
    return events

def scrape_filankara():
    events = []
    try:
        res = requests.get("https://filankara.beehiiv.com/", headers=HEADERS, timeout=15)
        if res.status_code != 200:
            return events
        soup = BeautifulSoup(res.text, 'html.parser')
        seen_links = set()
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/p/filankara-' not in href:
                continue
            title = a.get_text(strip=True)
            if not title or len(title) < 4:
                continue
            link = href if href.startswith('http') else f"https://filankara.beehiiv.com{href}"
            if link in seen_links:
                continue
            seen_links.add(link)
            image = get_og_image(link)
            events.append({"title": title, "link": link, "source": "filAnkara", "image": image})
        if events:
            return [events[0]]
    except Exception as e:
        print(f"[filAnkara Hata] {e}")
    return events

def scrape_eventbrite():
    events = []
    seen_links = set()
    try:
        res = requests.get("https://www.eventbrite.com/d/turkey--ankara/events/", headers=HEADERS, timeout=15)
        if res.status_code != 200:
            return events
        soup = BeautifulSoup(res.text, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = a['href']
            if "/e/" not in href or "eventbrite.com" not in href:
                continue
            title = a.get_text(strip=True)
            if not title or len(title) < 4:
                continue
            clean_link = href.split("?")[0]
            if clean_link in seen_links:
                continue
            seen_links.add(clean_link)
            events.append({"title": title, "link": clean_link, "source": "Eventbrite", "image": None})
    except Exception as e:
        print(f"[Eventbrite Hata] {e}")
    print(f"Eventbrite: {len(events)} etkinlik")
    return events

def scrape_biletinial():
    events = []
    try:
        res = requests.get("https://www.biletinial.com/ankara-etkinlikleri", headers=HEADERS, timeout=15)
        if res.status_code != 200:
            return events
        soup = BeautifulSoup(res.text, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/etkinlik/' in href or '/event/' in href:
                title = a.get_text(strip=True)
                if title and len(title) > 4:
                    link = href if href.startswith('http') else f"https://www.biletinial.com{href}"
                    events.append({"title": title, "link": link, "source": "Biletinial", "image": None})
    except Exception as e:
        print(f"[Biletinial Hata] {e}")
    print(f"Biletinial: {len(events)} etkinlik")
    return events

def scrape_biletimgo():
    events = []
    try:
        res = requests.get("https://www.biletimgo.com/sehir-etkinlikleri/ankara", headers=HEADERS, timeout=15)
        if res.status_code != 200:
            return events
        soup = BeautifulSoup(res.text, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/etkinlik/' in href or '/event/' in href:
                title = a.get_text(strip=True)
                if title and len(title) > 4:
                    link = href if href.startswith('http') else f"https://www.biletimgo.com{href}"
                    events.append({"title": title, "link": link, "source": "BiletimGO", "image": None})
    except Exception as e:
        print(f"[BiletimGO Hata] {e}")
    print(f"BiletimGO: {len(events)} etkinlik")
    return events

def scrape_mobilet():
    events = []
    for url in [
        "https://mobilet.com/tr/search/?q=ankara",
        "https://mobilet.com/tr/?city=ankara",
        "https://mobilet.com/tr/events/ankara",
    ]:
        try:
            res = requests.get(url, headers=HEADERS, timeout=15)
            if res.status_code != 200:
                continue
            soup = BeautifulSoup(res.text, 'html.parser')
            found = []
            for a in soup.find_all('a', href=True):
                href = a['href']
                if '/event/' in href or '/tr/event/' in href:
                    title = a.get_text(strip=True)
                    if title and len(title) > 4:
                        link = href if href.startswith('http') else f"https://mobilet.com{href}"
                        found.append({"title": title, "link": link, "source": "Mobilet", "image": None})
            if found:
                events = found
                break
        except Exception as e:
            print(f"[Mobilet Hata] {url}: {e}")
    print(f"Mobilet: {len(events)} etkinlik")
    return events

def scrape_abb():
    events = []
    try:
        res = requests.get("https://www.ankara.bel.tr/etkinlikler", headers=HEADERS, timeout=15)
        if res.status_code != 200:
            return events
        soup = BeautifulSoup(res.text, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/etkinlik' in href or '/event' in href:
                title = a.get_text(strip=True)
                if title and len(title) > 4:
                    link = href if href.startswith('http') else f"https://www.ankara.bel.tr{href}"
                    events.append({"title": title, "link": link, "source": "ABB", "image": None})
    except Exception as e:
        print(f"[ABB Hata] {e}")
    print(f"ABB: {len(events)} etkinlik")
    return events

def scrape_lakonser():
    events = []
    try:
        res = requests.get("https://lakonser.com/etkinlikler/", headers=HEADERS, timeout=15)
        if res.status_code != 200:
            return events
        soup = BeautifulSoup(res.text, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = a['href']
            if 'lakonser.com' in href or href.startswith('/etkinlik/'):
                title = a.get_text(strip=True)
                if title and len(title) > 4:
                    link = href if href.startswith('http') else f"https://lakonser.com{href}"
                    events.append({"title": title, "link": link, "source": "LaKonser", "image": None})
    except Exception as e:
        print(f"[LaKonser Hata] {e}")
    print(f"LaKonser: {len(events)} etkinlik")
    return events


# ─────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────
def run_bot():
    if not TOKEN or not CHAT_ID:
        print("[HATA] TELEGRAM_TOKEN veya TELEGRAM_CHAT_ID tanımlı değil!")
        return

    set_global_timeout(MAX_RUN_SECONDS)
    seen = load_seen_events()

    try:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Bot başlatılıyor...")
        print(f"Daha önce görülen etkinlik sayısı: {len(seen)}")

        all_events = []
        for scraper in [
            scrape_filankara, scrape_biletix, scrape_eventbrite,
            scrape_biletinial, scrape_biletimgo, scrape_mobilet,
            scrape_abb, scrape_lakonser
        ]:
            all_events.extend(scraper())

        # Deduplicate
        all_events = url_deduplicate(all_events)
        all_events = fuzzy_deduplicate(all_events)
        print(f"Deduplikasyon sonrası: {len(all_events)} etkinlik")

        # Filter new only — mark ALL as seen (even image-less, so they don't resurface)
        new_events = [e for e in all_events if event_hash(e['title'], e['link']) not in seen]
        for e in new_events:
            seen.add(event_hash(e['title'], e['link']))
        print(f"Yeni etkinlik: {len(new_events)}")

        if not new_events:
            print("Yeni etkinlik bulunamadı.")
            save_seen_events(seen)
            return

        # Only send events with images → quality digest
        digest_events = [e for e in new_events if e.get('image')]
        print(f"Görselli (digest): {len(digest_events)}")

        if not digest_events:
            print("Görselli yeni etkinlik yok, gönderilecek bir şey yok.")
            save_seen_events(seen)
            return

        # AI summaries
        ai_counter = {'count': 0}
        for event in digest_events:
            event['summary'] = get_ai_summary(event['title'], event['link'], ai_counter)
            time.sleep(0.4)

        # Categorize
        categorized = {cat: [] for cat in CATEGORY_MAP}
        for event in digest_events:
            categorized[classify_event(event['title'])].append(event)

        # Friday top picks
        is_friday = datetime.now().weekday() == 4
        top_picks = get_weekly_top_picks(digest_events) if is_friday else []

        # ── Send ──────────────────────────────────
        send_daily_intro()
        time.sleep(1)

        if top_picks:
            send_weekly_top_picks(top_picks, ai_counter)
            time.sleep(1)

        sent_count = 0
        for category, events in categorized.items():
            if not events:
                continue
            send_digest_header(category, len(events))
            time.sleep(0.8)
            for event in events:
                if send_event(event):
                    sent_count += 1
                time.sleep(1.5)

        save_seen_events(seen)
        print(f"Tamamlandı. {sent_count} etkinlik gönderildi. AI özet: {ai_counter['count']}.")

    except BotTimeoutError:
        print(f"[UYARI] {MAX_RUN_SECONDS}s sınırına ulaşıldı.")
        save_seen_events(seen)

    finally:
        cancel_global_timeout()


if __name__ == "__main__":
    run_bot()
