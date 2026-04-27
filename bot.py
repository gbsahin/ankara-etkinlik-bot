import requests
from bs4 import BeautifulSoup
import os

# Secrets from GitHub
TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
DB_FILE = "sent_events.txt"

# 2026 Browser Headers
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
    caption = (
        f"📍 **YENİ ETKİNLİK: ANKARA**\n\n"
        f"🎭 {title}\n\n"
        f"🔗 [Detaylar ve Bilet İçin Tıklayın]({link})\n\n"
        f"#Ankara #Etkinlik #FilAnkara #Biletix"
    )
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    payload = {"chat_id": CHAT_ID, "photo": img, "caption": caption, "parse_mode": "Markdown"}
    r = requests.post(url, data=payload)
    print(f"Telegram response: {r.status_code}") # For GitHub logs

def scrape_sources():
    sent = load_sent_events()
    
    # 1. Fil Ankara (2026 Structure)
    try:
        res = requests.get("https://filankara.beehiiv.com/", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        # Fil Ankara now uses 'post-card' or 'beehiiv-post-card'
        for card in soup.select('div[class*="post-card"]')[:5]:
            title_tag = card.select_one('h2') or card.select_one('h3')
            link_tag = card.select_one('a')
            img_tag = card.select_one('img')
            
            if title_tag and link_tag:
                title = title_tag.text.strip()
                link = link_tag['href']
                if not link.startswith('http'): link = "https://filankara.beehiiv.com" + link
                img = img_tag['src'] if img_tag else "https://filankara.com/wp-content/uploads/2022/11/filankara-logo.png"
                
                if link not in sent:
                    send_telegram(title, img, link)
                    save_sent_event(link)
    except Exception as e: print(f"Fil Ankara Hatası: {e}")

    # 2. Biletix (2026 Structure)
    try:
        # Ankara Events Search
        res = requests.get("https://www.biletix.com/search/TURKIYE/tr#ankara", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        for event in soup.select('.searchResultEventName')[:5]:
            title = event.text.strip()
            link = "https://www.biletix.com" + event.find_parent('a')['href']
            img = "https://www.biletix.com/static/images/biletix_logo.png" # Safe placeholder
            if link not in sent:
                send_telegram(title, img, link)
                save_sent_event(link)
    except Exception as e: print(f"Biletix Hatası: {e}")

if __name__ == "__main__":
    scrape_sources()
