import requests
from bs4 import BeautifulSoup
import os

TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

def send_test_message():
    # This function bypasses all filters to see if the connection works
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": "🚀 Ankara Botu Bağlantı Testi: Başarılı! Web siteleri taranıyor...",
        "parse_mode": "Markdown"
    }
    r = requests.post(url, payload)
    print(f"Telegram Test Durumu: {r.status_code}")
    if r.status_code != 200:
        print(f"HATA DETAYI: {r.text}")

def scrape_simple():
    # Simplified Biletix check
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get("https://www.biletix.com/search/TURKIYE/tr#ankara", headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        # Just grab the first 3 links it finds
        links = soup.find_all('a', href=True)
        for a in links:
            if "/etkinlik/" in a['href']:
                title = a.get_text(strip=True) or "Ankara Etkinliği"
                link = "https://www.biletix.com" + a['href']
                
                # Send the first one it finds just to prove it works
                msg_url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
                caption = f"✨ **YENİ:** {title}\n🔗 {link}"
                requests.post(msg_url, {"chat_id": CHAT_ID, "text": caption})
                print(f"Gönderildi: {title}")
                break 
    except Exception as e:
        print(f"Tarama Hatası: {e}")

if __name__ == "__main__":
    send_test_message()
    scrape_simple()
