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

def scrape_and_update(category, brand, model, year_from, year_to, custom_url=""):
    init_db()
    conn = get_connection()
    cursor = conn.cursor()
    
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept-Language': 'pl-PL,pl;q=0.9',
    })
    
    now_time = datetime.now(ZoneInfo("Europe/Warsaw"))
    now_time_str = now_time.strftime("%Y-%m-%d %H:%M:%S")

    brand_clean = brand.strip().lower()
    model_clean = model.strip().lower()
    
    # Dzielimy markę i model na części, by wykryć np. "Mercedes" w ogłoszeniu "Mercedes-Benz"
    brand_parts = [p for p in brand_clean.replace('-', ' ').split() if len(p) > 2]
    if not brand_parts: brand_parts = [brand_clean]
    
    model_parts = [p for p in model_clean.replace('-', ' ').split() if len(p) > 1]
    if not model_parts: model_parts = [model_clean]

    # Jeśli użytkownik nie wklei linku - budujemy go ręcznie, bez psucia znaków specjalnych
    if not custom_url:
        cat_slug = "motocykle-i-quady" if category == "Motocykle" else "osobowe"
        b_slug = brand_clean.replace(" ", "-")
        m_slug = model_clean.replace(" ", "-")
        custom_url = f"https://www.otomoto.pl/{cat_slug}/{b_slug}/{m_slug}/od-{year_from}?search%5Bfilter_float_year%3Ato%5D={year_to}"

    page = 1
    new_inserts = 0
    updates = 0
    previous_ids = set()
    processed_this_run = set() 
    
    while page <= 25:
        # BARDZO PROSTE, BEZPIECZNE STRONICOWANIE
        if 'page=' in custom_url:
            url = re.sub(r'page=\d+', f'page={page}', custom_url)
        else:
            url = custom_url + (f"&page={page}" if '?' in custom_url else f"?page={page}")
        
        try:
            time.sleep(random.uniform(0.5, 1.0))
            resp = session.get(url, timeout=10)
            if resp.status_code != 200: 
                break
        except:
            break
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Bierzemy po prostu wszystkie artykuły. Koniec z cudowaniem z data-testid.
        articles = soup.find_all('article')
        if not articles: 
            break 
            
        current_ids = set()
        for art in articles:
            oid = art.get('id') or art.get('data-id')
            if not oid: continue
            current_ids.add(oid)
            
            # Weryfikacja duplikatów
            if oid in processed_this_run:
                continue
            
            raw_text = art.get_text(" ", strip=True).replace('\xa0', ' ').replace('\u202f', ' ')
            text_lower = raw_text.lower()
            
            # TWARDY FILTR: Odrzucamy wszystkie promowane oferty innych marek/modeli
            brand_found = any(p in text_lower for p in brand_parts)
            model_found = any(p in text_lower for p in model_parts)
            if not (brand_found and model_found):
                continue
            
            title_elem = art.find('h1') or art.find('h2') or art.find('h6')
            title = title_elem.text.strip() if title_elem else f"{brand} {model}"
            
            a_elem = art.find('a', href=True)
            offer_url = a_elem['href'] if a_elem else ""
            
            # Cena
            price = 0.0
            p_matches = re.findall(r'(\d{1,3}(?: \d{3})*|\d{4,7})\s*(PLN|EUR)', raw_text, re.IGNORECASE)
            if p_matches:
                vals = [float(p[0].replace(' ', '')) for p in p_matches]
                valid_vals = [v for v in vals if 3000 < v < 5000000]
                if valid_vals: price = max(valid_vals)
                    
            if price == 0:
                continue

            # TWARDY FILTR: Odrzucamy wszystkie auta, które nie mają w tekście naszego rocznika
            year = None
            y_matches = re.findall(r'\b(19\d{2}|20\d{2})\b', raw_text)
            for ym in y_matches:
                ym_int = int(ym)
                if int(year_from) <= ym_int <= int(year_to) and ym_int != price:
                    year = ym_int
                    break
                    
            if not year:
                continue

            # Przebieg
            mileage = 0
            m_matches = re.findall(r'\b(\d{1,3}(?: \d{3})*|\d{1,7})\s*km\b', raw_text)
            if m_matches:
                m_vals = [int(m.replace(' ', '')) for m in m_matches]
                valid_m = [m for m in m_vals if m > 0]
                if valid_m: mileage = max(valid_m)

            processed_this_run.add(oid)

            # Zapis do bazy
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

        # Mechanizm odcinający (jeśli Otomoto znów nas wyśle na pierwszą stronę, kończymy)
        overlap = len(current_ids.intersection(previous_ids))
        if page > 1 and overlap >= 20: 
            break
            
        previous_ids = current_ids
        page += 1

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
    return f"✅ Raport: Pobrano dokładnie {total_processed} unikalnych ofert (Dodano nowych: {new_inserts}, Zaktualizowano: {updates}). Aktualnie w bazie: {active_in_db} aut."
