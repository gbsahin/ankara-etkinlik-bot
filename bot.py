import requests
from bs4 import BeautifulSoup
import os
import json
import hashlib
import time
from datetime import datetime

# --- Config ---
TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
SEEN_EVENTS_FILE = "seen_events.json"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'tr-TR,tr;q=0.9,en;q=0.8',
    'Accept': 'application/json, text/html, */*',
}


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


# --- Telegram ---
def send_telegram_message(text, parse_mode="Markdown"):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": False
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


# --- Scrapers ---

def scrape_biletix():
    """
    Biletix exposes a JSON search API — much more reliable than scraping HTML.
    """
    events = []
    try:
        # Biletix internal search API
        api_url = "https://www.biletix.com/searcher/TURKIYE/tr/results"
        params = {
            "searchterm": "",
            "city": "ANKARA",
            "category": "",
            "page": 1
        }
        res = requests.get(api_url, headers=HEADERS, params=params, timeout=15)
        res.raise_for_status()
        data = res.json()

        # The API returns events in different possible structures
        items = data.get("events") or data.get("results") or data.get("items") or []
        if isinstance(items, dict):
            items = items.get("event") or []

        for item in items:
            title = item.get("name") or item.get("title") or item.get("eventName", "")
            slug = item.get("slug") or item.get("id") or item.get("eventId", "")
            if not title:
                continue
            link = f"https://www.biletix.com/etkinlik/{slug}/TURKIYE/tr" if slug else "https://www.biletix.com"
            events.append({"title": title.strip(), "link": link, "source": "Biletix"})

    except Exception as e:
        print(f"[Biletix API Hata] {e}")
        # Fallback: try scraping the mobile/lite version
        events.extend(scrape_biletix_fallback())

    return events


def scrape_biletix_fallback():
    """Fallback: scrape Biletix's lighter search page."""
    events = []
    try:
        url = "https://www.biletix.com/search/ANKARA/tr"
        res = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')

        # Try multiple selectors Biletix has used over time
        selectors = [
            ('a', {'class': lambda c: c and 'event' in c.lower()}),
            ('a', {'href': lambda h: h and ('/etkinlik/' in h or '/event/' in h)}),
            ('div', {'class': lambda c: c and 'event-name' in c.lower()}),
        ]

        for tag, attrs in selectors:
            for el in soup.find_all(tag, attrs):
                title = el.get_text(strip=True)
                href = el.get('href', '')
                if not title or len(title) < 3:
                    continue
                link = href if href.startswith('http') else f"https://www.biletix.com{href}"
                events.append({"title": title, "link": link, "source": "Biletix"})
            if events:
                break

    except Exception as e:
        print(f"[Biletix Fallback Hata] {e}")

    return events


def scrape_eventbrite():
    """Scrape Eventbrite for Ankara events."""
    events = []
    urls = [
        "https://www.eventbrite.com/d/turkey--ankara/events/",
        "https://www.eventbrite.com/d/turkey--ankara/music/",
        "https://www.eventbrite.com/d/turkey--ankara/arts/",
    ]
    for url in urls:
        try:
            res = requests.get(url, headers=HEADERS, timeout=15)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, 'html.parser')

            for a in soup.find_all('a', href=True):
                href = a['href']
                if "/e/" in href and "eventbrite.com" in href:
                    title = a.get_text(strip=True)
                    if not title or len(title) < 3:
                        continue
                    events.append({"title": title, "link": href, "source": "Eventbrite"})
        except Exception as e:
            print(f"[Eventbrite Hata] {url}: {e}")
    return events


def scrape_mobilet():
    """Scrape Mobilet - another major Turkish ticketing site with Ankara events."""
    events = []
    try:
        url = "https://www.mobilet.com/etkinlikler/?city=ankara"
        res = requests.get(url, headers=HEADERS, timeout=15)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')

        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/etkinlik/' in href or '/event/' in href:
                title = a.get_text(strip=True)
                if not title or len(title) < 3:
                    continue
                link = href if href.startswith('http') else f"https://www.mobilet.com{href}"
                events.append({"title": title, "link": link, "source": "Mobilet"})
    except Exception as e:
        print(f"[Mobilet Hata] {e}")
    return events


def scrape_konser_org():
    """Scrape konser.org - Turkish concert listings."""
    events = []
    try:
        url = "https://konser.org/ankara-konserleri"
        res = requests.get(url, headers=HEADERS, timeout=15)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')

        for a in soup.find_all('a', href=True):
            href = a['href']
            if href and len(href) > 5 and href != '#':
                title = a.get_text(strip=True)
                if not title or len(title) < 3:
                    continue
                # Filter out nav/footer links by title length
                if len(title) > 8:
                    link = href if href.startswith('http') else f"https://konser.org{href}"
                    events.append({"title": title, "link": link, "source": "Konser.org"})
    except Exception as e:
        print(f"[Konser.org Hata] {e}")
    return events


# --- Dedup helper ---
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

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Bot başlatılıyor...")
    seen = load_seen_events()

    all_events = []
    all_events.extend(scrape_biletix())
    all_events.extend(scrape_eventbrite())
    all_events.extend(scrape_mobilet())
    all_events.extend(scrape_konser_org())

    all_events = deduplicate(all_events)
    print(f"Toplam bulunan etkinlik: {len(all_events)}")

    new_count = 0
    for event in all_events:
        title = event['title']
        link = event['link']
        source = event['source']

        h = event_hash(title, link)
        if h in seen:
            continue

        msg = (
            f"🎉 *YENİ ETKİNLİK* — {source}\n\n"
            f"📌 *{title}*\n"
            f"🔗 {link}"
        )
        success = send_telegram_message(msg)
        if success:
            seen.add(h)
            new_count += 1
            print(f"[Gönderildi] {title}")
            time.sleep(1)

    save_seen_events(seen)
    print(f"Tamamlandı. {new_count} yeni etkinlik gönderildi.")
    if new_count == 0:
        print("Yeni etkinlik bulunamadı.")


if __name__ == "__main__":
    run_bot()
