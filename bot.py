import requests
from bs4 import BeautifulSoup
import os
import json
import hashlib
import html as html_lib
import time
import signal
import re
from datetime import datetime
from difflib import SequenceMatcher

# ─────────────────────────────────────────
# Config
# ─────────────────────────────────────────
TOKEN            = os.getenv('TELEGRAM_TOKEN')
CHAT_ID          = os.getenv('TELEGRAM_CHAT_ID')
SEEN_EVENTS_FILE = "seen_events.json"

MAX_RUN_SECONDS    = 480   # hard stop after 8 minutes
MAX_OG_FETCHES     = 80    # max page/image fetches per run (covers all sources)
FUZZY_THRESHOLD    = 0.82  # titles this similar → duplicate
MAX_SENDS_PER_RUN  = 30    # flood guard: leftover events go out on the next run

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'tr-TR,tr;q=0.9,en;q=0.8',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Ankara + district/venue names accepted as proof of location
ANKARA_HINTS = (
    'ankara', 'çankaya', 'cankaya', 'kızılay', 'kizilay', 'yenimahalle',
    'keçiören', 'kecioren', 'çayyolu', 'cayyolu', 'batıkent', 'batikent',
    'gölbaşı', 'golbasi', 'ulus', 'bilkent', 'odtü', 'metu', 'incek',
    'bahçelievler', 'bahcelievler', 'etimesgut', 'sincan', 'mamak',
)

# Titles that scream "online" — cheap prefilter before any page fetch
ONLINE_TITLE_RE = re.compile(
    r'\b(online|webinar|webinaire|virtual|sanal|zoom|livestream|live stream)\b',
    re.IGNORECASE,
)

BILETIX_IGNORE_KEYWORDS = [
    'myaccount', 'my-tickets', 'business.ticketmaster', 'trust.ticketmaster',
    'privacy.ticketmaster', 'developer.ticketmaster', 'tiktok.com',
    'play.google.com', 'bize-ulasin', 'affiliate', 'cookie', 'gizlilik',
    'reklam', 'account'
]

CATEGORY_MAP = {
    "🎵 Müzik & Konser": [
        "konser", "müzik", "music", "concert", "jazz", "rock", "pop",
        "klasik", "opera", "festival", "sahne", "gece", "dj", "akustik"
    ],
    "🎭 Tiyatro & Gösteri": [
        "tiyatro", "theatre", "theater", "oyun", "gösteri",
        "stand-up", "comedy", "komedi", "dans", "bale", "sirk"
    ],
    "🏛 Kültür & Ücretsiz": [
        "sergi", "exhibition", "müze", "museum", "kültür",
        "ücretsiz", "free", "açık", "sanat", "fotoğraf", "sinema"
    ],
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
        pass  # Windows doesn't support SIGALRM

def cancel_global_timeout():
    try:
        signal.alarm(0)
    except AttributeError:
        pass


# ─────────────────────────────────────────
# Deduplication
# ─────────────────────────────────────────
def load_seen_events():
    if os.path.exists(SEEN_EVENTS_FILE):
        try:
            with open(SEEN_EVENTS_FILE, "r") as f:
                data = json.load(f)
                return set(data[-2000:])  # cap at 2000 to prevent bloat
        except Exception:
            pass
    return set()

def save_seen_events(seen):
    with open(SEEN_EVENTS_FILE, "w") as f:
        json.dump(list(seen)[-2000:], f)

def event_hash(title, link):
    clean_link = link.split("?")[0].rstrip("/").lower()
    return hashlib.md5(clean_link.encode()).hexdigest()

def _normalize(title):
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', '', title.lower())).strip()

def fuzzy_deduplicate(events):
    kept = []
    kept_titles = []
    for ev in events:
        norm = _normalize(ev['title'])
        is_duplicate = any(
            SequenceMatcher(None, norm, kt).ratio() >= FUZZY_THRESHOLD
            for kt in kept_titles
        )
        if not is_duplicate:
            kept.append(ev)
            kept_titles.append(norm)
    return kept

def url_deduplicate(events):
    seen_links = set()
    unique = []
    for e in events:
        clean = e['link'].split("?")[0].rstrip("/").lower()
        if clean not in seen_links:
            seen_links.add(clean)
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
# Page / image fetcher
# ─────────────────────────────────────────
def fetch_page(url, fetch_counter=None, timeout=8):
    """Fetch a page's HTML, honoring the global fetch budget. Returns str or None."""
    if fetch_counter is not None:
        if fetch_counter.get('count', 0) >= MAX_OG_FETCHES:
            return None
        fetch_counter['count'] = fetch_counter.get('count', 0) + 1
    try:
        res = SESSION.get(url, timeout=timeout)
        if res.status_code != 200:
            return None
        return res.text
    except Exception:
        return None

def og_image_from_soup(soup):
    for attr in ['og:image', 'twitter:image']:
        tag = soup.find('meta', property=attr) or soup.find('meta', attrs={'name': attr})
        if tag and tag.get('content'):
            img_url = tag['content'].strip()
            if img_url.startswith('http'):
                return img_url
    return None

def get_og_image(url, fetch_counter=None):
    page = fetch_page(url, fetch_counter, timeout=6)
    if not page:
        return None
    return og_image_from_soup(BeautifulSoup(page, 'html.parser'))


# ─────────────────────────────────────────
# Location verification (JSON-LD schema.org)
# ─────────────────────────────────────────
def jsonld_events(soup):
    """Yield schema.org Event dicts found in a page."""
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or '')
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            types = item.get('@type')
            types = types if isinstance(types, list) else [types]
            if 'Event' in types or any(isinstance(t, str) and t.endswith('Event') for t in types if t):
                yield item

def is_ankara_event_page(soup):
    """
    Strict check on an event page's structured data:
    the event must be a physical (non-online) event located in Ankara.
    Returns True only when this can be positively verified.
    """
    for item in jsonld_events(soup):
        mode = str(item.get('eventAttendanceMode', ''))
        if 'Online' in mode:
            return False
        loc = item.get('location') or {}
        if isinstance(loc, dict) and loc.get('@type') == 'VirtualLocation':
            return False
        loc_text = json.dumps(loc, ensure_ascii=False).lower()
        return any(hint in loc_text for hint in ANKARA_HINTS)
    return False  # no structured data → cannot verify → reject


# ─────────────────────────────────────────
# Embedded JSON extractor (window.__SERVER_DATA__ etc.)
# ─────────────────────────────────────────
def extract_embedded_json(html, marker):
    """Extract the JSON object assigned right after `marker` in raw HTML."""
    m = re.search(re.escape(marker) + r'\s*=\s*', html)
    if not m:
        return None
    start = html.find('{', m.end())
    if start == -1:
        return None
    depth, i, in_str, esc = 0, start, False, False
    while i < len(html):
        ch = html[i]
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(html[start:i + 1])
                    except Exception:
                        return None
        i += 1
    return None


# ─────────────────────────────────────────
# Telegram
# ─────────────────────────────────────────
def send_photo_message(image_url, caption):
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    payload = {
        "chat_id": CHAT_ID,
        "photo": image_url,
        "caption": caption,
        "parse_mode": "HTML",
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        return r.status_code == 200
    except Exception:
        return False

def send_text_message(text, disable_preview=True):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            print(f"[Telegram HATA] {r.status_code}: {r.text}")
            return False
        return True
    except Exception as e:
        print(f"[Telegram İstisna] {e}")
        return False

TR_MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
             "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]

def turkish_date(dt=None):
    dt = dt or datetime.now()
    return f"{dt.day:02d} {TR_MONTHS[dt.month - 1]} {dt.year}"

def send_category_header(category, count):
    text = (
        f"\n{category}\n"
        f"<i>{turkish_date()} · {count} etkinlik</i>\n"
        f"{'─' * 20}"
    )
    send_text_message(text)

def send_event(event):
    title  = html_lib.escape(event['title'])  # titles with & < > break HTML parse_mode
    link   = event['link']
    source = event['source']
    image  = event.get('image')

    style   = SOURCE_STYLE.get(source, {"icon": "📅", "label": source})
    caption = (
        f"{style['icon']} <b>{title}</b>\n"
        f"\n"
        f"🔗 {link}\n"
        f"<i>{style['label']} · Ankara</i>"
    )

    if image:
        success = send_photo_message(image, caption)
        if success:
            return True

    # Fallback to text with link preview for image-less events
    return send_text_message(caption, disable_preview=False)


# ─────────────────────────────────────────
# Scrapers
# ─────────────────────────────────────────
def scrape_biletix(fetch_counter):
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
    for url in category_urls:
        try:
            page = fetch_page(url, timeout=15)
            if not page:
                continue
            soup = BeautifulSoup(page, 'html.parser')
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

def scrape_filankara(fetch_counter):
    events = []
    try:
        page = fetch_page("https://filankara.beehiiv.com/", timeout=15)
        if not page:
            return events
        soup = BeautifulSoup(page, 'html.parser')
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
            image = get_og_image(link, fetch_counter)
            events.append({"title": title, "link": link, "source": "filAnkara", "image": image})
        if events:
            print(f"filAnkara: {events[0]['title']}")
            return [events[0]]
    except Exception as e:
        print(f"[filAnkara Hata] {e}")
    return events

def _eventbrite_candidates_from_server_data(data):
    """
    Eventbrite's listing page embeds window.__SERVER_DATA__ with 'buckets'.
    When Ankara has few physical events, the page pads itself with an
    'online_events' bucket — the source of the non-Ankara notifications.
    Keep only non-online events from non-online buckets.
    """
    candidates = []
    for bucket in data.get('buckets') or []:
        key = str(bucket.get('key') or '').lower()
        name = str(bucket.get('name') or '').lower()
        if 'online' in key or 'online' in name:
            continue
        for ev in bucket.get('events') or []:
            if not isinstance(ev, dict) or ev.get('is_online_event'):
                continue
            title = (ev.get('name') or '').strip()
            link = (ev.get('url') or '').split('?')[0]
            if title and '/e/' in link:
                candidates.append({"title": title, "link": link})
    return candidates

def _eventbrite_candidates_from_anchors(soup):
    """Fallback if the embedded JSON layout ever changes."""
    candidates, seen_links = [], set()
    for a in soup.find_all('a', href=True):
        href = a['href']
        if "/e/" not in href or "eventbrite." not in href:
            continue
        title = a.get_text(strip=True)
        if not title or len(title) < 4:
            continue
        clean_link = href.split("?")[0]
        if clean_link in seen_links:
            continue
        seen_links.add(clean_link)
        candidates.append({"title": title, "link": clean_link})
    return candidates

def scrape_eventbrite(fetch_counter):
    events = []
    try:
        page = fetch_page("https://www.eventbrite.com/d/turkey--ankara/events/", timeout=15)
        if not page:
            return events

        data = extract_embedded_json(page, 'window.__SERVER_DATA__')
        if data:
            candidates = _eventbrite_candidates_from_server_data(data)
        else:
            print("[Eventbrite] __SERVER_DATA__ bulunamadı, anchor fallback kullanılıyor")
            candidates = _eventbrite_candidates_from_anchors(BeautifulSoup(page, 'html.parser'))

        skipped = 0
        for c in candidates:
            # Cheap prefilter: obviously-online titles don't deserve a fetch
            if ONLINE_TITLE_RE.search(c['title']):
                skipped += 1
                continue
            # Strict verification on the event's own page (JSON-LD):
            # must be a physical event located in Ankara.
            ev_page = fetch_page(c['link'], fetch_counter)
            if not ev_page:
                skipped += 1
                continue
            soup = BeautifulSoup(ev_page, 'html.parser')
            if not is_ankara_event_page(soup):
                skipped += 1
                continue
            image = og_image_from_soup(soup)
            time.sleep(0.2)
            events.append({"title": c['title'], "link": c['link'],
                           "source": "Eventbrite", "image": image})
        if skipped:
            print(f"[Eventbrite] {skipped} online/Ankara dışı etkinlik elendi")
    except Exception as e:
        print(f"[Eventbrite Hata] {e}")
    print(f"Eventbrite: {len(events)} etkinlik")
    return events

def scrape_biletinial(fetch_counter):
    events = []
    try:
        page = fetch_page("https://www.biletinial.com/ankara-etkinlikleri", timeout=15)
        if not page:
            return events
        soup = BeautifulSoup(page, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/etkinlik/' in href or '/event/' in href:
                title = a.get_text(strip=True)
                if title and len(title) > 4:
                    link = href if href.startswith('http') else f"https://www.biletinial.com{href}"
                    image = get_og_image(link, fetch_counter)
                    time.sleep(0.2)
                    events.append({"title": title, "link": link, "source": "Biletinial", "image": image})
    except Exception as e:
        print(f"[Biletinial Hata] {e}")
    print(f"Biletinial: {len(events)} etkinlik")
    return events

def scrape_biletimgo(fetch_counter):
    events = []
    try:
        page = fetch_page("https://www.biletimgo.com/sehir-etkinlikleri/ankara", timeout=15)
        if not page:
            return events
        soup = BeautifulSoup(page, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/etkinlik/' in href or '/event/' in href:
                title = a.get_text(strip=True)
                if title and len(title) > 4:
                    link = href if href.startswith('http') else f"https://www.biletimgo.com{href}"
                    image = get_og_image(link, fetch_counter)
                    time.sleep(0.2)
                    events.append({"title": title, "link": link, "source": "BiletimGO", "image": image})
    except Exception as e:
        print(f"[BiletimGO Hata] {e}")
    print(f"BiletimGO: {len(events)} etkinlik")
    return events

def scrape_mobilet(fetch_counter):
    events = []
    for url in [
        "https://mobilet.com/tr/search/?q=ankara",
        "https://mobilet.com/tr/?city=ankara",
        "https://mobilet.com/tr/events/ankara",
    ]:
        try:
            page = fetch_page(url, timeout=15)
            if not page:
                continue
            soup = BeautifulSoup(page, 'html.parser')
            found = []
            for a in soup.find_all('a', href=True):
                href = a['href']
                if '/event/' in href or '/tr/event/' in href:
                    title = a.get_text(strip=True)
                    if title and len(title) > 4:
                        link = href if href.startswith('http') else f"https://mobilet.com{href}"
                        image = get_og_image(link, fetch_counter)
                        time.sleep(0.2)
                        found.append({"title": title, "link": link, "source": "Mobilet", "image": image})
            if found:
                events = found
                break
        except Exception as e:
            print(f"[Mobilet Hata] {url}: {e}")
    print(f"Mobilet: {len(events)} etkinlik")
    return events

def scrape_abb(fetch_counter):
    events = []
    try:
        page = fetch_page("https://www.ankara.bel.tr/etkinlikler", timeout=15)
        if not page:
            return events
        soup = BeautifulSoup(page, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/etkinlik' in href or '/event' in href:
                title = a.get_text(strip=True)
                if title and len(title) > 4:
                    link = href if href.startswith('http') else f"https://www.ankara.bel.tr{href}"
                    image = get_og_image(link, fetch_counter)
                    time.sleep(0.2)
                    events.append({"title": title, "link": link, "source": "ABB", "image": image})
    except Exception as e:
        print(f"[ABB Hata] {e}")
    print(f"ABB: {len(events)} etkinlik")
    return events

def scrape_lakonser(fetch_counter):
    events = []
    try:
        page = fetch_page("https://lakonser.com/etkinlikler/", timeout=15)
        if not page:
            return events
        soup = BeautifulSoup(page, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = a['href']
            if 'lakonser.com' in href or href.startswith('/etkinlik/'):
                title = a.get_text(strip=True)
                if title and len(title) > 4:
                    link = href if href.startswith('http') else f"https://lakonser.com{href}"
                    image = get_og_image(link, fetch_counter)
                    time.sleep(0.2)
                    events.append({"title": title, "link": link, "source": "LaKonser", "image": image})
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
        print(f"Daha önce görülen etkinlik: {len(seen)}")

        fetch_counter = {'count': 0}
        all_events = []
        for scraper in [
            scrape_filankara, scrape_biletix, scrape_eventbrite,
            scrape_biletinial, scrape_biletimgo, scrape_mobilet,
            scrape_abb, scrape_lakonser
        ]:
            all_events.extend(scraper(fetch_counter))

        all_events = url_deduplicate(all_events)
        all_events = fuzzy_deduplicate(all_events)
        print(f"Deduplikasyon sonrası: {len(all_events)} etkinlik")

        new_events = [e for e in all_events if event_hash(e['title'], e['link']) not in seen]
        print(f"Yeni etkinlik: {len(new_events)}")

        if not new_events:
            print("Yeni etkinlik bulunamadı.")
            save_seen_events(seen)
            return

        # Flood guard: cap per run, leftovers stay unseen and go out next run
        if len(new_events) > MAX_SENDS_PER_RUN:
            print(f"[UYARI] {len(new_events)} yeni etkinlik, {MAX_SENDS_PER_RUN} ile sınırlandı")
            new_events = new_events[:MAX_SENDS_PER_RUN]

        # Mark attempted events as seen (even on send failure, to avoid spam loops)
        for e in new_events:
            seen.add(event_hash(e['title'], e['link']))

        # Categorize
        categorized = {cat: [] for cat in CATEGORY_MAP}
        for event in new_events:
            categorized[classify_event(event['title'])].append(event)

        # Send grouped by category
        sent_count = 0
        for category, events in categorized.items():
            if not events:
                continue
            send_category_header(category, len(events))
            time.sleep(0.8)
            for event in events:
                if send_event(event):
                    sent_count += 1
                time.sleep(1.5)

        save_seen_events(seen)
        print(f"Tamamlandı. {sent_count} etkinlik gönderildi.")

    except BotTimeoutError:
        print(f"[UYARI] {MAX_RUN_SECONDS}s sınırına ulaşıldı, kaydediliyor...")
        save_seen_events(seen)

    finally:
        cancel_global_timeout()


if __name__ == "__main__":
    run_bot()
