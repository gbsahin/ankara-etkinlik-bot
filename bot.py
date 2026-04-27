import requests
from bs4 import BeautifulSoup
import os

# Secrets from GitHub
TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
DB_FILE = "sent_events.txt"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'tr-TR,tr;q=0.9'
}

def load_sent_events():
    if not os.path.exists(DB_FILE): return []
    with open(DB_FILE, "r") as f: return f.read().splitlines()

def save_sent_event(link):
    with open(DB_FILE, "a") as f: f.write(link + "\n")

def send_telegram(title, img, link):
    caption = f"📍 **ANKARA ETKİNLİK**\n\n🎭 **{title}**\n\n🔗 [Detaylar ve Kayıt İçin Tıklayın]({link})"
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    payload = {"chat_id": CHAT_ID, "photo": img, "caption": caption, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, data=payload)
        print(f"Post Durumu: {r.status_code} - {title[:20]}")
    except Exception as e:
        print(f"Telegram Gönderim Hatası: {e}")

def scrape_sources():
    sent = load_sent_events()
    
    # --- BILETIX SCRAPER (Safe Version) ---
    print("Biletix taranıyor...")
    try:
        res = requests.get("https://www.biletix.com/search/TURKIYE/tr#ankara", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        events = soup.find_all(class_='searchResultEventName')
        
        for ev in events[:5]:
            parent = ev.find_parent('a')
            # The 'Safe-Grab' check:
            if parent and parent.has_attr('href'):
                link = "https://www.biletix.com" + parent['href']
                title = ev.get_text(strip=True)
                if link not in sent:
                    img = "https://www.biletix.com/static/images/biletix_logo.png"
                    send_telegram(title, img, link)
                    save_sent_event(link)
    except Exception as e:
        print(f"Biletix Hatası Atlatıldı: {e}")

    # --- FIL ANKARA SCRAPER (2026 Version) ---
    print("Fil Ankara taranıyor...")
    try:
        res = requests.get("https://filankara.com/", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        # Looking for the most recent article links
        for a_tag in soup.find_all('a', href=True):
            if '/etkinlik/' in a_tag['href'] or '/p/' in a_tag['href']:
                link = a_tag['href']
                title = a_tag.get_text().strip()
                if len(title) > 10 and link not in sent:
                    img = "https://filankara.com/wp-content/uploads/2022/11/filankara-logo.png"
                    send_telegram(title, img, link)
                    save_sent_event(link)
                    break # Just grab the top one
    except Exception as e:
        print(f"Fil Ankara Hatası Atlatıldı: {e}")

if __name__ == "__main__":
    scrape_sources()
