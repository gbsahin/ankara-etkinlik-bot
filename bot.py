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


# --- Scrapers with heavy debug output ---

def scrape_biletix():
    events = []
    print("\n=== BİLETİX SCRAPING ===")

    # Try JSON API first
    try:
        api_url = "https://www.biletix.com/searcher/TURKIYE/tr/results"
        params = {"searchterm": "", "city": "ANKARA", "category": "", "page": 1}
        res = requests.get(api_url, headers=HEADERS, params=params, timeout=15)
        print(f"Biletix API status: {res.status_code}")
        print(f"Biletix API response (first 500 chars): {res.text[:500]}")

        if res.status_code == 200:
            try:
                data = res.json()
                print(f"Biletix API keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
            except Exception as je:
                print(f"Biletix API JSON parse hatası: {je}")
    except Exception as e:
        print(f"Biletix API isteği hatası: {e}")

    # Try HTML scraping
    try:
        url = "https://www.biletix.com/search/ANKARA/tr"
        res = requests.get(url, headers=HEADERS, timeout=15)
        print(f"\nBiletix HTML status: {res.status_code}")
        soup = BeautifulSoup(res.text, 'html.parser')
        all_links = soup.find_all('a', href=True)
        print(f"Biletix sayfasındaki toplam link sayısı: {len(all_links)}")

        print("İlk 10 link:")
        for a in all_links[:10]:
            print(f"  href={a['href']} | text={a.get_text(strip=True)[:50]}")

        print("\nEtkinlik içerebilecek linkler:")
        for a in all_links:
            href = a['href']
            if any(kw in href for kw in ['/etkinlik/', '/event/', '/bilet/', 'ticket']):
                title = a.get_text(strip=True)
                print(f"  BULUNDU: {title[:60]} -> {href[:80]}")
                if title and len(title) > 3:
                    link = href if href.startswith('http') else f"https://www.biletix.com{href}"
                    events.append({"title": title, "link": link, "source": "Biletix"})

    except Exception as e:
        print(f"Biletix HTML hata: {e}")

    print(f"Biletix toplam etkinlik: {len(events)}")
    return events


def scrape_eventbrite():
    events = []
    print("\n=== EVENTBRITE SCRAPING ===")
    url = "https://www.eventbrite.com/d/turkey--ankara/events/"
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        print(f"Eventbrite status: {res.status_code}")
        soup = BeautifulSoup(res.text, 'html.parser')
        all_links = soup.find_all('a', href=True)
        print(f"Eventbrite toplam link: {len(all_links)}")

        for a in all_links:
            href = a['href']
            if "/e/" in href and "eventbrite.com" in href:
                title = a.get_text(strip=True)
                if title and len(title) > 3:
                    print(f"  BULUNDU: {title[:60]}")
                    events.append({"title": title, "link": href, "source": "Eventbrite"})
    except Exception as e:
        print(f"Eventbrite hata: {e}")

    print(f"Eventbrite toplam etkinlik: {len(events)}")
    return events


def scrape_mobilet():
    events = []
    print("\n=== MOBİLET SCRAPING ===")
    try:
        url = "https://www.mobilet.com/etkinlikler/?city=ankara"
        res = requests.get(url, headers=HEADERS, timeout=15)
        print(f"Mobilet status: {res.status_code}")
        soup = BeautifulSoup(res.text, 'html.parser')
        all_links = soup.find_all('a', href=True)
        print(f"Mobilet toplam link: {len(all_links)}")

        for a in all_links:
            href = a['href']
            if any(kw in href for kw in ['/etkinlik/', '/event/']):
                title = a.get_text(strip=True)
                if title and len(title) > 3:
                    print(f"  BULUNDU: {title[:60]}")
                    link = href if href.startswith('http') else f"https://www.mobilet.com{href}"
                    events.append({"title": title, "link": link, "source": "Mobilet"})
    except Exception as e:
        print(f"Mobilet hata: {e}")

    print(f"Mobilet toplam etkinlik: {len(events)}")
    return events


def scrape_konser_org():
    events = []
    print("\n=== KONSER.ORG SCRAPING ===")
    try:
        url = "https://konser.org/ankara-konserleri"
        res = requests.get(url, headers=HEADERS, timeout=15)
        print(f"Konser.org status: {res.status_code}")
        soup = BeautifulSoup(res.text, 'html.parser')
        all_links = soup.find_all('a', href=True)
        print(f"Konser.org toplam link: {len(all_links)}")

        for a in all_links:
            href = a['href']
            title = a.get_text(strip=True)
            if title and len(title) > 8 and href and href != '#':
                print(f"  BULUNDU: {title[:60]} -> {href[:60]}")
                link = href if href.startswith('http') else f"https://konser.org{href}"
                events.append({"title": title, "link": link, "source": "Konser.org"})
    except Exception as e:
        print(f"Konser.org hata: {e}")

    print(f"Konser.org toplam etkinlik: {len(events)}")
    return events


def deduplicate(events):
    seen_links = set()
    unique = []
    for e in events:
        if e['link'] not in seen_links:
            seen_links.add(e['link'])
            unique.append(e)
    return unique


# --- Main ---
def run_bot():
    if not TOKEN or not CHAT_ID:
        print("[HATA] TELEGRAM_TOKEN veya TELEGRAM_CHAT_ID tanımlı değil!")
        return

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Bot başlatılıyor...")
    print(f"CHAT_ID: {CHAT_ID}")

    # Clear seen events for this debug run so everything gets sent
    seen = set()
    print("DEBUG: seen_events temizlendi, tüm etkinlikler gönderilecek")

    all_events = []
    all_events.extend(scrape_biletix())
    all_events.extend(scrape_eventbrite())
    all_events.extend(scrape_mobilet())
    all_events.extend(scrape_konser_org())

    all_events = deduplicate(all_events)
    print(f"\n=== TOPLAM: {len(all_events)} etkinlik bulundu ===")

    # Send only first 5 to avoid spam
    new_count = 0
    for event in all_events[:5]:
        title = event['title']
        link = event['link']
        source = event['source']

        msg = (
            f"🎉 *YENİ ETKİNLİK* — {source}\n\n"
            f"📌 *{title}*\n"
            f"🔗 {link}"
        )
        success = send_telegram_message(msg)
        if success:
            new_count += 1
            print(f"[Gönderildi] {title}")
        time.sleep(1)

    print(f"\nTamamlandı. {new_count} etkinlik gönderildi.")


if __name__ == "__main__":
    run_bot()
