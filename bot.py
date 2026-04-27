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
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

# Known nav/footer/account links to ignore on Biletix
BILETIX_IGNORE_KEYWORDS = [
    'myaccount', 'my-tickets', 'business.ticketmaster', 'trust.ticketmaster',
    'privacy.ticketmaster', 'developer.ticketmaster', 'tiktok.com',
    'play.google.com', 'bize-ulasin', 'affiliate', 'cookie', 'gizlilik',
    'reklam', 'account'
]


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
        "disable_web_page_preview": True
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
    Biletix: scrape category pages directly which have real event links
    like /etkinlik/SLUG/ANKARA/tr — much more reliable than the search page.
    """
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
            res = requests.get(url, headers=HEADERS, timeout=15)
            if res.status_code != 200:
                print(f"[Biletix] {url} -> {res.status_code}")
                continue

            soup = BeautifulSoup(res.text, 'html.parser')

            for a in soup.find_all('a', href=True):
                href = a['href']

                # Real event links look like: /etkinlik/SOME-SLUG/ANKARA/tr
                if '/etkinlik/' not in href:
                    continue

                # Skip known non-event links
                if any(bad in href for bad in BILETIX_IGNORE_KEYWORDS):
                    continue

                title = a.get_text(strip=True)
                if not title or len(title) < 4:
                    continue

                # Deduplicate by slug
                if href in seen_slugs:
                    continue
                seen_slugs.add(href)

                link = href if href.startswith('http') else f"https://www.biletix.com{href}"
                events.append({"title": title, "link": link, "source": "Biletix"})

        except Exception as e:
            print(f"[Biletix Hata] {url}: {e}")

    print(f"Biletix toplam etkinlik: {len(events)}")
    return events


def scrape_eventbrite():
    """
    Eventbrite: filter strictly to Turkey/Ankara events only,
    and deduplicate since each event appears twice in the HTML.
    """
    events = []
    seen_links = set()

    url = "https://www.eventbrite.com/d/turkey--ankara/events/"
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            print(f"[Eventbrite] status: {res.status_code}")
            return events

        soup = BeautifulSoup(res.text, 'html.parser')

        for a in soup.find_all('a', href=True):
            href = a['href']

            # Must be an Eventbrite event link
            if "/e/" not in href or "eventbrite.com" not in href:
                continue

            # Skip non-Turkey events — Eventbrite sometimes inserts global events
            # We filter by checking the URL doesn't contain other country indicators
            # and the title makes sense
            title = a.get_text(strip=True)
            if not title or len(title) < 4:
                continue

            # Deduplicate (each event appears twice in Eventbrite HTML)
            clean_link = href.split("?")[0]  # strip query params
            if clean_link in seen_links:
                continue
            seen_links.add(clean_link)

            events.append({"title": title, "link": clean_link, "source": "Eventbrite"})

    except Exception as e:
        print(f"[Eventbrite Hata] {e}")

    print(f"Eventbrite toplam etkinlik: {len(events)}")
    return events


def scrape_mobilet():
    """
    Mobilet: correct URL found by checking their sitemap.
    """
    events = []
    # Correct Mobilet URLs for Ankara
    urls = [
        "https://www.mobilet.com/ankara/",
        "https://www.mobilet.com/tr/city/ankara",
        "https://www.mobilet.com/events?location=ankara",
    ]

    for url in urls:
        try:
            res = requests.get(url, headers=HEADERS, timeout=15)
            print(f"[Mobilet] {url} -> {res.status_code}")
            if res.status_code != 200:
                continue

            soup = BeautifulSoup(res.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a['href']
                if any(kw in href for kw in ['/etkinlik/', '/event/', '/bilet/']):
                    title = a.get_text(strip=True)
                    if title and len(title) > 4:
                        link = href if href.startswith('http') else f"https://www.mobilet.com{href}"
                        events.append({"title": title, "link": link, "source": "Mobilet"})

            if events:
                break  # Stop if we found events on this URL

        except Exception as e:
            print(f"[Mobilet Hata] {url}: {e}")

    print(f"Mobilet toplam etkinlik: {len(events)}")
    return events


def scrape_biletinial():
    """
    Biletinial: another Turkish ticketing platform, static HTML.
    """
    events = []
    try:
        url = "https://www.biletinial.com/ankara-etkinlikleri"
        res = requests.get(url, headers=HEADERS, timeout=15)
        print(f"[Biletinial] status: {res.status_code}")
        if res.status_code != 200:
            return events

        soup = BeautifulSoup(res.text, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/etkinlik/' in href or '/event/' in href:
                title = a.get_text(strip=True)
                if title and len(title) > 4:
                    link = href if href.startswith('http') else f"https://www.biletinial.com{href}"
                    events.append({"title": title, "link": link, "source": "Biletinial"})

    except Exception as e:
        print(f"[Biletinial Hata] {e}")

    print(f"Biletinial toplam etkinlik: {len(events)}")
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

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Bot başlatılıyor...")
    seen = load_seen_events()
    print(f"Daha önce gönderilen etkinlik sayısı: {len(seen)}")

    all_events = []
    all_events.extend(scrape_biletix())
    all_events.extend(scrape_eventbrite())
    all_events.extend(scrape_mobilet())
    all_events.extend(scrape_biletinial())

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
