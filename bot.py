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

BILETIX_IGNORE_KEYWORDS = [
    'myaccount', 'my-tickets', 'business.ticketmaster', 'trust.ticketmaster',
    'privacy.ticketmaster', 'developer.ticketmaster', 'tiktok.com',
    'play.google.com', 'bize-ulasin', 'affiliate', 'cookie', 'gizlilik',
    'reklam', 'account'
]

# Source styles: emoji icon + label
SOURCE_STYLE = {
    "Biletix":     {"icon": "🎟",  "label": "Biletix"},
    "filAnkara":   {"icon": "📰",  "label": "filAnkara"},
    "Eventbrite":  {"icon": "🌍",  "label": "Eventbrite"},
    "Biletinial":  {"icon": "🎫",  "label": "Biletinial"},
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


# --- Image fetcher ---
def get_og_image(url):
    """Try to fetch the og:image meta tag from an event page."""
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code != 200:
            return None
        soup = BeautifulSoup(res.text, 'html.parser')

        # Try og:image first, then twitter:image
        for attr in ['og:image', 'twitter:image']:
            tag = soup.find('meta', property=attr) or soup.find('meta', attrs={'name': attr})
            if tag and tag.get('content'):
                img_url = tag['content'].strip()
                if img_url.startswith('http'):
                    return img_url

        # Fallback: first large img tag
        for img in soup.find_all('img', src=True):
            src = img['src']
            if src.startswith('http') and any(ext in src for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                return src

    except Exception:
        pass
    return None


# --- Telegram ---
def send_photo_message(image_url, caption):
    """Send a message with a photo."""
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    payload = {
        "chat_id": CHAT_ID,
        "photo": image_url,
        "caption": caption,
        "parse_mode": "Markdown",
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code == 200:
            return True
        # If photo fails (bad URL, unsupported format), fall back to text
        print(f"[sendPhoto HATA] {r.status_code} — metin olarak gönderiliyor")
        return False
    except Exception as e:
        print(f"[sendPhoto İstisna] {e}")
        return False

def send_text_message(text):
    """Send a plain text message."""
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
    """
    Build a styled message and send it.
    If an image is available, send as photo with caption.
    Otherwise send as text with link preview (which shows the site's own image).
    """
    title  = event['title']
    link   = event['link']
    source = event['source']
    image  = event.get('image')

    style = SOURCE_STYLE.get(source, {"icon": "📅", "label": source})
    icon  = style['icon']
    label = style['label']

    # Caption / message body
    caption = (
        f"{icon} *{title}*\n"
        f"\n"
        f"🔗 [Detaylar için tıkla]({link})\n"
        f"━━━━━━━━━━━━━━━\n"
        f"_{label} · Ankara_"
    )

    if image:
        success = send_photo_message(image, caption)
        if success:
            return True

    # Fallback: text with link preview (Telegram auto-shows the page's image)
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

                # Fetch og:image from the event page
                image = get_og_image(link)
                time.sleep(0.3)  # be polite to Biletix

                events.append({"title": title, "link": link, "source": "Biletix", "image": image})

        except Exception as e:
            print(f"[Biletix Hata] {url}: {e}")

    print(f"Biletix toplam etkinlik: {len(events)}")
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

            # Get the issue cover image from og:image
            image = get_og_image(link)

            events.append({"title": title, "link": link, "source": "filAnkara", "image": image})

        # Only send the latest issue
        if events:
            print(f"filAnkara en son sayı: {events[0]['title']}")
            return [events[0]]

    except Exception as e:
        print(f"[filAnkara Hata] {e}")

    return events


def scrape_eventbrite():
    events = []
    seen_links = set()
    url = "https://www.eventbrite.com/d/turkey--ankara/events/"
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
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
            # Eventbrite's link preview already shows a good image, skip og fetch
            events.append({"title": title, "link": clean_link, "source": "Eventbrite", "image": None})

    except Exception as e:
        print(f"[Eventbrite Hata] {e}")

    print(f"Eventbrite toplam etkinlik: {len(events)}")
    return events


def scrape_biletinial():
    events = []
    try:
        url = "https://www.biletinial.com/ankara-etkinlikleri"
        res = requests.get(url, headers=HEADERS, timeout=15)
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
    all_events.extend(scrape_filankara())
    all_events.extend(scrape_biletix())
    all_events.extend(scrape_eventbrite())
    all_events.extend(scrape_biletinial())

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
            time.sleep(1.5)  # respect Telegram rate limits

    save_seen_events(seen)
    print(f"Tamamlandı. {new_count} yeni etkinlik gönderildi.")
    if new_count == 0:
        print("Yeni etkinlik bulunamadı.")


if __name__ == "__main__":
    run_bot()
