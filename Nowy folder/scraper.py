import sqlite3
import requests
from bs4 import BeautifulSoup
import re
import time
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

def get_connection():
    return sqlite3.connect('otomoto.db')

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            otomoto_id TEXT UNIQUE NOT NULL,
            brand TEXT NOT NULL,
            model TEXT NOT NULL,
            production_year INTEGER NOT NULL,
            title TEXT NOT NULL,
            mileage_km INTEGER,
            current_price REAL NOT NULL,
            url TEXT NOT NULL,
            status TEXT DEFAULT 'Aktywne',
            publication_date TEXT,
            first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            offer_id INTEGER,
            price REAL NOT NULL,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(offer_id) REFERENCES offers(id) ON DELETE CASCADE
        )
    ''')
    conn.commit()
    conn.close()

def generate_otomoto_slugs(brand, model):
    """ Tłumacz dziwnych linków Otomoto. Zmienia 'CLA' na 'cla-klasa' itd. """
    b = brand.strip().lower().replace(" ", "-")
    m = model.strip().lower().replace(" ", "-")
    
    if b in ["mercedes", "mercedes-benz"]:
        b = "mercedes-benz"
        if not m.endswith("-klasa") and m in ["a", "b", "c", "e", "s", "g", "v", "x", "cla", "cls", "clk", "glk", "gla", "glb", "glc", "gle", "gls", "slk", "slc", "sl", "amg-gt"]:
            m = f"{m}-klasa"
            
    elif b == "bmw":
        if re.match(r'^[1-8]$', m):
            m = f"seria-{m}"
            
    return b, m

def scrape_and_update(category, brand, model, year_from, year_to, custom_url=""):
    init_db()
    conn = get_connection()
    cursor = conn.cursor()
    
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)',
        'Accept-Language': 'pl-PL,pl;q=0.9',
    })
    
    now_time = datetime.now(ZoneInfo("Europe/Warsaw"))
    now_time_str = now_time.strftime("%Y-%m-%d %H:%M:%S")

    cat_slug = "motocykle-i-quady" if category == "Motocykle" else "osobowe"
    
    # Używamy naprawionych linków!
    b_slug, m_slug = generate_otomoto_slugs(brand, model)

    brand_parts = [p for p in brand.lower().replace('-', ' ').split() if len(p) > 2]
    model_parts = [p for p in model.lower().replace('-', ' ').split() if len(p) > 1]
    if not brand_parts: brand_parts = [brand.lower()]
    if not model_parts: model_parts = [model.lower()]

    new_inserts = 0
    updates = 0
    processed_this_run = set() 

    # Skanujemy rok po roku, żeby ominąć blokady paginacji
    for current_year in range(int(year_from), int(year_to) + 1):
        
        # Koszyki cenowe
        price_brackets = [
            (0, 80000), (80001, 120000), (120001, 160000), 
            (160001, 220000), (220001, 300000), (300001, 5000000)
        ]
        
        for p_min, p_max in price_brackets:
            for page in (1, 2, 3): 
                if custom_url and "otomoto.pl" in custom_url:
                    url = f"{custom_url}&page={page}" if '?' in custom_url else f"{custom_url}?page={page}"
                else:
                    url = f"https://www.otomoto.pl/{cat_slug}/{b_slug}/{m_slug}/od-{current_year}"
                    
                params = {
                    "search[filter_float_year:to]": current_year,
                    "search[filter_float_price:from]": p_min,
                    "search[filter_float_price:to]": p_max,
                    "page": page
                } if not custom_url else None
                
                try:
                    time.sleep(random.uniform(0.3, 0.7))
                    resp = session.get(url, params=params, timeout=10)
                    if resp.status_code != 200: break
                except:
                    break
                    
                soup = BeautifulSoup(resp.text, 'html.parser')
                articles = soup.find_all('article')
                if not articles: break 
                    
                for art in articles:
                    oid = art.get('id') or art.get('data-id')
                    if not oid or oid in processed_this_run: continue
                    
                    title_elem = art.find('h1') or art.find('h2') or art.find('h6')
                    title = title_elem.text.strip() if title_elem else ""
                    title_lower = title.lower()
                    
                    # Wyrzucamy ew. reklamy innych marek
                    if not any(p in title_lower for p in brand_parts) and not any(p in title_lower for p in model_parts):
                        continue
                    
                    raw_text = art.get_text(" ", strip=True).replace('\xa0', ' ').replace('\u202f', ' ')
                    
                    price = 0.0
                    p_matches = re.findall(r'(\d{1,3}(?:[ \.]\d{3})*|\d{4,7})\s*(PLN|EUR|zł|zl)', raw_text, re.IGNORECASE)
                    if p_matches:
                        vals = [float(p[0].replace(' ', '').replace('.', '')) for p in p_matches]
                        valid_vals = [v for v in vals if 3000 < v < 5000000]
                        if valid_vals: price = max(valid_vals)
                    if price == 0: continue

                    year = None
                    mileage = 0
                    for tag in art.find_all(['li', 'dd', 'span', 'p', 'div']):
                        text = tag.text.strip()
                        text_lower_tag = text.lower()
                        y_match = re.search(r'\b(19\d{2}|20\d{2})\b', text)
                        if y_match and 'cm' not in text_lower_tag and 'pln' not in text_lower_tag:
                            y_val = int(y_match.group(1))
                            if 1950 <= y_val <= 2026: year = y_val
                                
                        if 'km' in text_lower_tag:
                            m_match = re.search(r'(\d{1,3}(?:[ \.]\d{3})*|\d+)\s*km', text_lower_tag)
                            if m_match: mileage = int(m_match.group(1).replace(' ', '').replace('.', ''))
                                
                    if not custom_url and year != current_year: 
                        continue
                    elif custom_url and not (int(year_from) <= (year or 0) <= int(year_to)):
                        continue

                    a_elem = art.find('a', href=True)
                    offer_url = a_elem['href'] if a_elem else ""

                    processed_this_run.add(oid)

                    cursor.execute("SELECT id, current_price FROM offers WHERE otomoto_id = ?", (oid,))
                    row = cursor.fetchone()
                    
                    if row:
                        offer_db_id, old_price = row
                        if old_price != price:
                            cursor.execute("INSERT INTO price_history (offer_id, price) VALUES (?, ?)", (offer_db_id, price))
                        cursor.execute("""
                            UPDATE offers 
                            SET current_price = ?, mileage_km = ?, status = 'Aktywne', last_seen_at = ? 
                            WHERE id = ?
                        """, (price, mileage, now_time_str, offer_db_id))
                        updates += 1
                    else:
                        cursor.execute('''
                            INSERT INTO offers (otomoto_id, brand, model, production_year, title, mileage_km, current_price, url, status, publication_date, first_seen_at, last_seen_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Aktywne', 'Brak danych', ?, ?)
                        ''', (oid, brand.strip(), model.strip(), year, title, mileage, price, offer_url, now_time_str, now_time_str))
                        
                        new_id = cursor.lastrowid
                        cursor.execute("INSERT INTO price_history (offer_id, price) VALUES (?, ?)", (new_id, price))
                        new_inserts += 1

                # Jeśli na stronie jest mało ofert, nie ma sensu sprawdzać kolejnej 
                if len(articles) < 28 or custom_url:
                    break

        # W trybie custom url robimy jeden wielki obieg, nie pętlę po latach
        if custom_url: 
            break

    # Aktualizacja sprzedanych i zliczanie
    limit_time = (now_time - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''
        UPDATE offers 
        SET status = 'Sprzedane' 
        WHERE LOWER(brand) = LOWER(?) AND LOWER(model) = LOWER(?) 
          AND production_year BETWEEN ? AND ? AND last_seen_at < ?
    ''', (brand.strip().lower(), model.strip().lower(), year_from, year_to, limit_time))

    cursor.execute('''
        SELECT COUNT(*) FROM offers 
        WHERE LOWER(brand) = LOWER(?) AND LOWER(model) = LOWER(?) 
          AND production_year BETWEEN ? AND ? AND status = 'Aktywne'
    ''', (brand.strip().lower(), model.strip().lower(), year_from, year_to))
    active_in_db = cursor.fetchone()[0]

    conn.commit()
    conn.close()

    total_processed = new_inserts + updates
    return f"✅ Raport: System wczytał {total_processed} unikalnych ofert (Nowych: {new_inserts}, Aktualizacji: {updates}). Aktualnie w bazie: {active_in_db} szt."
