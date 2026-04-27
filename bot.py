import requests
from bs4 import BeautifulSoup
import os
import json
import hashlib
import time
import signal
from datetime import datetime

# --- Config ---
TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
SEEN_EVENTS_FILE = "seen_events.json"

# ✅ NEW: Hard cap on total runtime (seconds). Raise if you run on a slow server.
MAX_RUN_SECONDS = 300  # 5 minutes total

# ✅ NEW: Maximum OG image fetches per scraper run (Biletix was fetching one per event = huge delay)
MAX_OG_FETCHES = 20

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

SOURCE_STYLE = {
    "Biletix":     {"icon": "🎟",  "label": "Biletix"},
    "filAnkara":   {"icon": "📰",  "label": "filAnkara"},
    "Eventbrite":  {"icon": "🌍",  "label": "Eventbrite"},
    "Biletinial":  {"icon": "🎫",  "label": "Biletinial"},
    "BiletimGO":   {"icon": "🎪",  "label": "BiletimGO"},
    "Mobilet":     {"icon": "🎭",  "label": "Mobilet"},
    "ABB":         {"icon": "🏛",  "label": "Ankara Büyükşehir"},
    "LaKonser":    {"icon": "🎵",  "label": "LaKonser"},
}


# ✅ NEW: Global timeout using SIGALRM (Linux/Mac only).
# On Windows, use the threading-based fallback below instead.
class TimeoutError(Exception):
    pass

def _timeout_handler(signum, frame):
    raise TimeoutError("Bot exceeded maximum runtime limit.")

def set_global_timeout(seconds):
    """Arms a hard kill-switch. Call once at the start of run_bot()."""
    try:
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(seconds)
    except AttributeError:
        # SIGALRM not available on Windows — silently skip.
        pass

def cancel_global_timeout():
    try:
        signal.alarm(0)
    except AttributeError:
        pass


# --- Deduplication ---
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


# --- Image fetcher ---
# ✅ CHANGED: accepts a counter dict so callers can cap total fetches across the run
def get_og_image(url, fetch_counter=None, max_fetches=MAX_OG_FETCHES):
    if fetch_counter is not None:
        if fetch_counter.get('count', 0) >= max_fetches:
            return None
        fetch_counter['count'] = fetch_counter.get('count', 0) + 1
    try:
        res = requests.get(url, headers=HEADERS, timeout=6)  # ✅ reduced from 10s → 6s
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


# --- Telegram ---
def send_photo_message(image_url, caption):
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    payload = {
        "chat_id": CHAT_ID,
        "photo": image_url,
        "caption": caption,
        "parse_mode": "Markdown",
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        return r.status_code == 200
    except Exception:
        return False

def send_text_message(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
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

def send_event(event):
    title  = event['title']
    link   = event['link']
    source = event['source']
    image  = event.get('image')

    style = SOURCE_STYLE.get(source, {"icon": "📅", "label": source})
    icon  = style['icon']
    label = style['label']

    caption = (
        f"{icon} *{title}*\n"
        f"\n"
        f"🔗 {link}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"_{label} · Ankara_"
    )

    if image:
        success = send_photo_message(image, caption)
        if success:
            return True

    return send_text_message(caption)


# --- Scrapers ---

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
    fetch_counter = {'count': 0}  # ✅ shared counter caps OG fetches across all Biletix pages

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
                # ✅ CHANGED: pass counter so we stop after MAX_OG_FETCHES total
                image = get_og_image(link, fetch_counter=fetch_counter)
                time.sleep(0.3)
                events.append({"title": title, "link": link, "source": "Biletix", "image": image})
        except Exception as e:
            print(f"[Biletix Hata] {url}: {e}")

    print(f"Biletix: {len(events)} etkinlik (OG fetch sayısı: {fetch_counter['count']})")
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
            print(f"filAnkara: {events[0]['title']}")
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
        print(f"[BiletimGO] status: {res.status_code}")
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
    urls_to_try = [
        "https://mobilet.com/tr/search/?q=ankara",
        "https://mobilet.com/tr/?city=ankara",
        "https://mobilet.com/tr/events/ankara",
    ]
    for url in urls_to_try:
        try:
            res = requests.get(url, headers=HEADERS, timeout=15)
            print(f"[Mobilet] {url} -> {res.status_code}")
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
        print(f"[ABB] status: {res.status_code}")
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
        print(f"[LaKonser] status: {res.status_code}")
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


def deduplicate(events):
    seen_links = set()
    unique = []
    for e in events:
        if e['link'] not in seen_links:
            seen_links.add(e['link'])
            unique.append(e)
    return unique


# --- Main Pipeline ---
def run_bot():
    if not TOKEN or not CHAT_ID:
        print("[HATA] TELEGRAM_TOKEN veya TELEGRAM_CHAT_ID tanımlı değil!")
        return

    # ✅ NEW: arm the global timeout so the process can never hang past MAX_RUN_SECONDS
    set_global_timeout(MAX_RUN_SECONDS)

    try:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Bot başlatılıyor...")
        seen = load_seen_events()
        print(f"Daha önce gönderilen etkinlik sayısı: {len(seen)}")

        all_events = []
        all_events.extend(scrape_filankara())
        all_events.extend(scrape_biletix())
        all_events.extend(scrape_eventbrite())
        all_events.extend(scrape_biletinial())
        all_events.extend(scrape_biletimgo())
        all_events.extend(scrape_mobilet())
        all_events.extend(scrape_abb())
        all_events.extend(scrape_lakonser())

        all_events = deduplicate(all_events)
        print(f"Toplam bulunan etkinlik: {len(all_events)}")

        new_count = 0
        for event in all_events:
            h = event_hash(event['title'], event['link'])
            if h in seen:
                continue

            success = send_event(event)
            if success:
                seen.add(h)
                new_count += 1
                print(f"[Gönderildi] {event['title']}")
                time.sleep(1.5)

        save_seen_events(seen)
        print(f"Tamamlandı. {new_count} yeni etkinlik gönderildi.")
        if new_count == 0:
            print("Yeni etkinlik bulunamadı.")

    except TimeoutError:
        # ✅ NEW: graceful exit on timeout — still saves whatever was collected
        print(f"[UYARI] Bot {MAX_RUN_SECONDS}s sınırına ulaştı, erken sonlandırılıyor.")
        save_seen_events(seen if 'seen' in dir() else set())

    finally:
        cancel_global_timeout()  # ✅ always disarm the alarm


if __name__ == "__main__":
    run_bot()
