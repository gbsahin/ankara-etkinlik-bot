import requests
from bs4 import BeautifulSoup
import os
import json
import hashlib
import time
from datetime import datetime

# — Config —

TOKEN = os.getenv(‘TELEGRAM_TOKEN’)
CHAT_ID = os.getenv(‘TELEGRAM_CHAT_ID’)
SEEN_EVENTS_FILE = “seen_events.json”
MAX_PER_SOURCE = 8
MAX_NEW_TO_SEND = 10
MAX_RUNTIME_SECONDS = 240
START_TIME = time.time()

HEADERS = {
‘User-Agent’: ’Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ’
‘(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36’,
‘Accept-Language’: ‘tr-TR,tr;q=0.9,en;q=0.8’,
‘Accept’: ‘text/html,application/xhtml+xml,application/xml;q=0.9,/;q=0.8’,
}

BILETIX_IGNORE_KEYWORDS = [
‘myaccount’, ‘my-tickets’, ‘business.ticketmaster’, ‘trust.ticketmaster’,
‘privacy.ticketmaster’, ‘developer.ticketmaster’, ‘tiktok.com’,
‘play.google.com’, ‘bize-ulasin’, ‘affiliate’, ‘cookie’, ‘gizlilik’,
‘reklam’, ‘account’
]

SOURCE_STYLE = {
“Biletix”:    {“icon”: “🎟”,  “label”: “Biletix”},
“filAnkara”:  {“icon”: “📰”,  “label”: “filAnkara”},
“Eventbrite”: {“icon”: “🌍”,  “label”: “Eventbrite”},
“Biletinial”: {“icon”: “🎫”,  “label”: “Biletinial”},
“BiletimGO”:  {“icon”: “🎪”,  “label”: “BiletimGO”},
“Mobilet”:    {“icon”: “🎭”,  “label”: “Mobilet”},
“ABB”:        {“icon”: “🏛”,  “label”: “Ankara Büyükşehir”},
“LaKonser”:   {“icon”: “🎵”,  “label”: “LaKonser”},
}

# — Helpers —

def is_timed_out():
return (time.time() - START_TIME) > MAX_RUNTIME_SECONDS

def load_seen_events():
if os.path.exists(SEEN_EVENTS_FILE):
with open(SEEN_EVENTS_FILE, “r”) as f:
return set(json.load(f))
return set()

def save_seen_events(seen):
with open(SEEN_EVENTS_FILE, “w”) as f:
json.dump(list(seen), f)

def event_hash(title, link):
return hashlib.md5(f”{title}{link}”.encode()).hexdigest()

def get_og_image(url):
“”“Fetch OG image only at send time, with short timeout.”””
try:
res = requests.get(url, headers=HEADERS, timeout=5)
if res.status_code != 200:
return None
soup = BeautifulSoup(res.text, ‘html.parser’)
for attr in [‘og:image’, ‘twitter:image’]:
tag = soup.find(‘meta’, property=attr) or soup.find(‘meta’, attrs={‘name’: attr})
if tag and tag.get(‘content’, ‘’).startswith(‘http’):
return tag[‘content’].strip()
except Exception:
pass
return None

# — Telegram —

def send_event(event):
style = SOURCE_STYLE.get(event[‘source’], {“icon”: “📅”, “label”: event[‘source’]})
caption = (
f”{style[‘icon’]} {event[‘title’]}\n\n”
f”🔗 {event[‘link’]}\n”
f”━━━━━━━━━━━━━━━\n”
f”{style[‘label’]} · Ankara”
)


image = get_og_image(event['link'])

if image:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
            json={"chat_id": CHAT_ID, "photo": image,
                  "caption": caption, "parse_mode": "Markdown"},
            timeout=10
        )
        if r.status_code == 200:
            return True
    except Exception:
        pass

# Fallback: text only
try:
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": caption,
              "parse_mode": "Markdown", "disable_web_page_preview": False},
        timeout=10
    )
    return r.status_code == 200
except Exception:
    return False


# — Scrapers (no image fetching inside, hard capped) —

def scrape_biletix():
events = []
category_urls = [
“https://www.biletix.com/category/MUSIC/ANKARA/tr”,
“https://www.biletix.com/category/THEATRE/ANKARA/tr”,
“https://www.biletix.com/category/COMEDY/ANKARA/tr”,
“https://www.biletix.com/category/SPORTS/ANKARA/tr”,
“https://www.biletix.com/category/ARTS/ANKARA/tr”,
“https://www.biletix.com/anasayfa/ANKARA/tr”,
]
seen_slugs = set()
for url in category_urls:
if is_timed_out() or len(events) >= MAX_PER_SOURCE:
break
try:
res = requests.get(url, headers=HEADERS, timeout=10)
if res.status_code != 200:
continue
soup = BeautifulSoup(res.text, ‘html.parser’)
for a in soup.find_all(‘a’, href=True):
if len(events) >= MAX_PER_SOURCE:
break
href = a[‘href’]
if ‘/etkinlik/’ not in href:
continue
if any(bad in href for bad in BILETIX_IGNORE_KEYWORDS):
continue
title = a.get_text(strip=True)
if not title or len(title) < 4 or href in seen_slugs:
continue
seen_slugs.add(href)
link = href if href.startswith(‘http’) else f”https://www.biletix.com{href}”
events.append({“title”: title, “link”: link, “source”: “Biletix”})
except Exception as e:
print(f”[Biletix Hata] {url}: {e}”)
print(f”Biletix: {len(events)} etkinlik”)
return events

def scrape_filankara():
events = []
try:
res = requests.get(“https://filankara.beehiiv.com/”, headers=HEADERS, timeout=10)
if res.status_code != 200:
return events
soup = BeautifulSoup(res.text, ‘html.parser’)
seen_links = set()
for a in soup.find_all(‘a’, href=True):
if len(events) >= MAX_PER_SOURCE:
break
href = a[‘href’]
if ‘/p/filankara-’ not in href:
continue
title = a.get_text(strip=True)
if not title or len(title) < 4:
continue
link = href if href.startswith(‘http’) else f”https://filankara.beehiiv.com{href}”
if link in seen_links:
continue
seen_links.add(link)
events.append({“title”: title, “link”: link, “source”: “filAnkara”})
# filAnkara is a newsletter — only send the latest issue
if events:
return [events[0]]
except Exception as e:
print(f”[filAnkara Hata] {e}”)
print(f”filAnkara: {len(events)} etkinlik”)
return events

def scrape_eventbrite():
events = []
seen_links = set()
try:
res = requests.get(
“https://www.eventbrite.com/d/turkey–ankara/events/”,
headers=HEADERS, timeout=10
)
if res.status_code != 200:
return events
soup = BeautifulSoup(res.text, ‘html.parser’)
for a in soup.find_all(‘a’, href=True):
if len(events) >= MAX_PER_SOURCE:
break
href = a[‘href’]
if “/e/” not in href or “eventbrite.com” not in href:
continue
title = a.get_text(strip=True)
if not title or len(title) < 4:
continue
clean_link = href.split(”?”)[0]
if clean_link in seen_links:
continue
seen_links.add(clean_link)
events.append({“title”: title, “link”: clean_link, “source”: “Eventbrite”})
except Exception as e:
print(f”[Eventbrite Hata] {e}”)
print(f”Eventbrite: {len(events)} etkinlik”)
return events

def scrape_biletinial():
events = []
try:
res = requests.get(
“https://www.biletinial.com/ankara-etkinlikleri”,
headers=HEADERS, timeout=10
)
if res.status_code != 200:
return events
soup = BeautifulSoup(res.text, ‘html.parser’)
for a in soup.find_all(‘a’, href=True):
if len(events) >= MAX_PER_SOURCE:
break
href = a[‘href’]
if ‘/etkinlik/’ in href or ‘/event/’ in href:
title = a.get_text(strip=True)
if title and len(title) > 4:
link = href if href.startswith(‘http’) else f”https://www.biletinial.com{href}”
events.append({“title”: title, “link”: link, “source”: “Biletinial”})
except Exception as e:
print(f”[Biletinial Hata] {e}”)
print(f”Biletinial: {len(events)} etkinlik”)
return events

def scrape_biletimgo():
events = []
try:
res = requests.get(
“https://www.biletimgo.com/sehir-etkinlikleri/ankara”,
headers=HEADERS, timeout=10
)
if res.status_code != 200:
return events
soup = BeautifulSoup(res.text, ‘html.parser’)
for a in soup.find_all(‘a’, href=True):
if len(events) >= MAX_PER_SOURCE:
break
href = a[‘href’]
if ‘/etkinlik/’ in href or ‘/event/’ in href:
title = a.get_text(strip=True)
if title and len(title) > 4:
link = href if href.startswith(‘http’) else f”https://www.biletimgo.com{href}”
events.append({“title”: title, “link”: link, “source”: “BiletimGO”})
except Exception as e:
print(f”[BiletimGO Hata] {e}”)
print(f”BiletimGO: {len(events)} etkinlik”)
return events

def scrape_mobilet():
events = []
urls_to_try = [
“https://mobilet.com/tr/search/?q=ankara”,
“https://mobilet.com/tr/?city=ankara”,
“https://mobilet.com/tr/events/ankara”,
]
for url in urls_to_try:
if is_timed_out():
break
try:
res = requests.get(url, headers=HEADERS, timeout=10)
if res.status_code != 200:
continue
soup = BeautifulSoup(res.text, ‘html.parser’)
found = []
for a in soup.find_all(‘a’, href=True):
if len(found) >= MAX_PER_SOURCE:
break
href = a[‘href’]
if ‘/event/’ in href or ‘/tr/event/’ in href:
title = a.get_text(strip=True)
if title and len(title) > 4:
link = href if href.startswith(‘http’) else f”https://mobilet.com{href}”
found.append({“title”: title, “link”: link, “source”: “Mobilet”})
if found:
events = found
break
except Exception as e:
print(f”[Mobilet Hata] {url}: {e}”)
print(f”Mobilet: {len(events)} etkinlik”)
return events

def scrape_abb():
events = []
try:
res = requests.get(
“https://www.ankara.bel.tr/etkinlikler”,
headers=HEADERS, timeout=10
)
if res.status_code != 200:
return events
soup = BeautifulSoup(res.text, ‘html.parser’)
for a in soup.find_all(‘a’, href=True):
if len(events) >= MAX_PER_SOURCE:
break
href = a[‘href’]
if ‘/etkinlik’ in href or ‘/event’ in href:
title = a.get_text(strip=True)
if title and len(title) > 4:
link = href if href.startswith(‘http’) else f”https://www.ankara.bel.tr{href}”
events.append({“title”: title, “link”: link, “source”: “ABB”})
except Exception as e:
print(f”[ABB Hata] {e}”)
print(f”ABB: {len(events)} etkinlik”)
return events

def scrape_lakonser():
events = []
try:
res = requests.get(
“https://lakonser.com/etkinlikler/”,
headers=HEADERS, timeout=10
)
if res.status_code != 200:
return events
soup = BeautifulSoup(res.text, ‘html.parser’)
for a in soup.find_all(‘a’, href=True):
if len(events) >= MAX_PER_SOURCE:
break
href = a[‘href’]
if ‘lakonser.com’ in href or href.startswith(’/etkinlik/’):
title = a.get_text(strip=True)
if title and len(title) > 4:
link = href if href.startswith(‘http’) else f”https://lakonser.com{href}”
events.append({“title”: title, “link”: link, “source”: “LaKonser”})
except Exception as e:
print(f”[LaKonser Hata] {e}”)
print(f”LaKonser: {len(events)} etkinlik”)
return events

def deduplicate(events):
seen_links = set()
unique = []
for e in events:
if e[‘link’] not in seen_links:
seen_links.add(e[‘link’])
unique.append(e)
return unique

# — Main Pipeline —

def run_bot():
if not TOKEN or not CHAT_ID:
print(”[HATA] TELEGRAM_TOKEN veya TELEGRAM_CHAT_ID tanımlı değil!”)
return


print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Bot başlatılıyor...")
seen = load_seen_events()
print(f"Daha önce gönderilen etkinlik sayısı: {len(seen)}")

scrapers = [
    scrape_filankara,
    scrape_biletix,
    scrape_eventbrite,
    scrape_biletinial,
    scrape_biletimgo,
    scrape_mobilet,
    scrape_abb,
    scrape_lakonser,
]

all_events = []
for scraper in scrapers:
    if is_timed_out():
        print(f"[Timeout] {scraper.__name__} atlandı — süre doldu.")
        break
    all_events.extend(scraper())

all_events = deduplicate(all_events)
print(f"Toplam bulunan etkinlik: {len(all_events)}")

new_count = 0
for event in all_events:
    if is_timed_out():
        print(f"[Timeout] Gönderme durdu. {new_count} etkinlik gönderildi.")
        break
    if new_count >= MAX_NEW_TO_SEND:
        print(f"[Limit] Maksimum {MAX_NEW_TO_SEND} etkinlik gönderildi.")
        break
    h = event_hash(event['title'], event['link'])
    if h in seen:
        continue
    if send_event(event):
        seen.add(h)
        new_count += 1
        print(f"[✓] {event['title']}")
        time.sleep(1.2)
    else:
        print(f"[✗] Gönderilemedi: {event['title']}")

save_seen_events(seen)
elapsed = int(time.time() - START_TIME)
print(f"Bitti. {new_count} yeni etkinlik gönderildi. Süre: {elapsed}s")


if *name* == “*main*”:
run_bot()
