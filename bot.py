import requests
from bs4 import BeautifulSoup
import os

TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
DB_FILE = "sent_events.txt"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
}

def load_sent_events():
    if not os.path.exists(DB_FILE): return []
    with open(DB_FILE, "r") as f: return f.read().splitlines()

def save_sent_event(link):
    with open(DB_FILE, "a") as f: f.write(link + "\n")

def send_telegram(title, link):
    # We'll use a standard Ankara image for now to ensure the post goes through
    img = "https://images.unsplash.com/photo-1620310860341-381079361a6b?q=80&w=1000&auto=format&fit=crop"
    caption = f"✨ **ANKARA'DA YENİ ETKİNLİK**\n\n📍 {title}\n\n🔗 [Detaylar İçin Tıklayın]({link})"
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    payload = {"chat_id": CHAT_ID, "photo": img, "caption": caption, "parse_mode": "Markdown"}
    r = requests.post(url, data=payload)
    print(f"Post Durumu: {r.status_code} ({title[:15]}...)")

def scrape_biletix():
    print("Biletix taranıyor...")
    try:
        # We target the main Ankara page
        res = requests.get("https://www.biletix.com/search/TURKIYE/tr#ankara", headers=HEADERS, timeout=20)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # This looks for EVERY link on the page and finds the first one that looks like an event
        found = 0
        for a in soup.find_all('a', href=True):
            if "/etkinlik/" in a['href'] and found < 2:
                link = "https://www.biletix.com" + a['href'] if not a['href'].startswith('http') else a['href']
                title = a.get_text(strip=True) or "Ankara Etkinliği"
                
                # For the first run, we skip the DB check to FORCE a post
                send_telegram(title, link)
                save_sent_event(link)
                found += 1
    except Exception as e: print(f"Biletix Hatası: {e}")

def scrape_fil_ankara():
    print("Fil Ankara taranıyor...")
    try:
        res = requests.get("https://filankara.beehiiv.com/", headers=HEADERS, timeout=20)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # Beehiiv uses specific post links starting with /p/
        for a in soup.find_all('a', href=True):
            if "/p/" in a['href']:
                link = "https://filankara.beehiiv.com" + a['href'] if not a['href'].startswith('http') else a['href']
                title = a.get_text(strip=True)
                if len(title) > 15:
                    send_telegram(title, link)
                    save_sent_event(link)
                    break 
    except Exception as e: print(f"Fil Ankara Hatası: {e}")

if __name__ == "__main__":
    scrape_biletix()
    scrape_fil_ankara()
