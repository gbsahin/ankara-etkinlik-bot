import requests
from bs4 import BeautifulSoup
import os

# Your GitHub Secrets
TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
DB_FILE = "sent_events.txt"

# 2026 Optimized Headers
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
    # Caption formatted for a clean Channel look
    caption = (
        f"📍 **YENİ ETKİNLİK DUYURUSU**\n\n"
        f"✨ {title}\n\n"
        f"🔗 [Detaylar ve Bilet İçin Tıklayın]({link})\n\n"
        f"#Ankara #Etkinlik #Biletix"
    )
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    payload = {"chat_id": CHAT_ID, "photo": img, "caption": caption, "parse_mode": "Markdown"}
    requests.post(url, data=payload)

def scrape_sources():
    sent = load_sent_events()
    
    # 1. Fil Ankara Scraper
    try:
        res = requests.get("https://filankara.com/etkinlikler/", headers=HEADERS)
        soup = BeautifulSoup(res.text, 'html.parser')
        for item in soup.select('.elementor-post')[:3]:
            title = item.select_one('.elementor-post__title').text.strip()
            link = item.select_one('a')['href']
            img = item.select_one('img')['src']
            if link not in sent:
                send_telegram(title, img, link)
                save_sent_event(link)
    except: print("Fil Ankara bypass failed")

    # 2. Biletix Ankara Scraper
    try:
        # Targeting the 'Newest' Ankara events
        res = requests.get("https://www.biletix.com/search/TURKIYE/tr#ankara", headers=HEADERS)
        soup = BeautifulSoup(res.text, 'html.parser')
        for event in soup.select('.searchResultEventName')[:3]:
            title = event.text.strip()
            link = "https://www.biletix.com" + event.find_parent('a')['href']
            img = "https://www.biletix.com/static/images/biletix_logo.png" # Safe fallback
            if link not in sent:
                send_telegram(title, img, link)
                save_sent_event(link)
    except: print("Biletix bypass failed")

if __name__ == "__main__":
    scrape_sources()
