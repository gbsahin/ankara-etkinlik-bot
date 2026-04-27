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
SEEN_EVENTS_FILE = "seen_events.json"  # Stores hashes of already-sent events

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
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
    """Scrape Biletix for Ankara events."""
    events = []
    urls = [
        "https://www.biletix.com/search/ANKARA/tr",
        "https://www.biletix.com/search/TURKIYE/tr#ankara",
    ]
    for url in urls:
        try:
            res = requests.get(url, headers=HEADERS, timeout=15)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, 'html.parser')

            for a in soup.find_all('a', href=True):
                href = a['href']
                if "/etkinlik/" in href or "/event/" in href:
                    title = a.get_text(strip=True)
                    if not title or len(title) < 3:
                        continue
                    link = href if href.startswith("http") else "https://www.biletix.com" + href
                    events.append({"title": title, "link": link, "source": "Biletix"})
        except Exception as e:
            print(f"[Biletix Hata] {url}: {e}")
    return events


def scrape_passo():
    """Scrape Passo for Ankara events."""
    events = []
    url = "https://www.passo.com.tr/tr/etkinlik/ankara"
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')

        for a in soup.find_all('a', href=True):
            href = a['href']
            if "/etkinlik/" in href or "/event/" in href:
                title = a.get_text(strip=True)
                if not title or len(title) < 3:
                    continue
                link = href if href.startswith("http") else "https://www.passo.com.tr" + href
                events.append({"title": title, "link": link, "source": "Passo"})
    except Exception as e:
        print(f"[Passo Hata] {e}")
    return events


def scrape_eventbrite():
    """Scrape Eventbrite for Ankara events."""
    events = []
    url = "https://www.eventbrite.com/d/turkey--ankara/events/"
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
        print(f"[Eventbrite Hata] {e}")
    return events


# --- Main Pipeline ---
def run_bot():
    if not TOKEN or not CHAT_ID:
        print("[HATA] TELEGRAM_TOKEN veya TELEGRAM_CHAT_ID tanımlı değil!")
        return

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Bot başlatılıyor...")
    seen = load_seen_events()

    # Gather events from all sources
    all_events = []
    all_events.extend(scrape_biletix())
    all_events.extend(scrape_passo())
    all_events.extend(scrape_eventbrite())

    print(f"Toplam bulunan etkinlik: {len(all_events)}")

    new_count = 0
    for event in all_events:
        title = event['title']
        link = event['link']
        source = event['source']

        h = event_hash(title, link)
        if h in seen:
            continue  # Already sent

        # Format and send
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
            time.sleep(1)  # Avoid Telegram rate limits

    save_seen_events(seen)
    print(f"Tamamlandı. {new_count} yeni etkinlik gönderildi.")

    if new_count == 0:
        print("Yeni etkinlik bulunamadı.")


if __name__ == "__main__":
    run_bot()
