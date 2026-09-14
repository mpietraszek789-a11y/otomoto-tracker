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

def build_otomoto_slugs(brand, model):
    b = brand.strip().lower().replace(" ", "-")
    m = model.strip().lower().replace(" ", "-")
    
    if b in ["mercedes", "mercedes-benz"]:
        b = "mercedes-benz"
        if not m.endswith("-klasa") and m in ["a", "b", "c", "e", "s", "g", "v", "cla", "cls", "clk", "glk", "gla", "glb", "glc", "gle", "gls", "slk", "slc"]:
            m = f"{m}-klasa"
            
    elif b == "bmw" and re.match(r'^[1-8]$', m):
        m = f"seria-{m}"
            
    return b, m

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

    cat_slug = "motocykle-i-quady" if category == "Motocykle" else "osobowe"
    b_slug, m_slug = build_otomoto_slugs(brand, model)

    new_inserts = 0
    updates = 0
    processed_this_run = set()

    # Tworzymy gęste przedziały cenowe (co 15 tys. PLN), aby każdy "plasterek" mieścił się na 1 stronie (<30 aut)
    price_brackets = [(i, i + 14999) for i in range(0, 400000, 15000)]
    price_brackets.append((400000, 5000000))

    for p_min, p_max in price_brackets:
        if custom_url and "otomoto.pl" in custom_url:
            clean_url = custom_url.split('&page=')[0].split('?page=')[0]
            separator = "&" if "?" in clean_url else "?"
            url = f"{clean_url}{separator}search%5Bfilter_float_price%3Afrom%5D={p_min}&search%5Bfilter_float_price%3Ato%5D={p_max}"
        else:
            url = f"https://www.otomoto.pl/{cat_slug}/{b_slug}/{m_slug}/od-{year_from}?search%5Bfilter_float_year%3Ato%5D={year_to}&search%5Bfilter_float_price%3Afrom%5D={p_min}&search%5Bfilter_float_price%3Ato%5D={p_max}"

        try:
            time.sleep(random.uniform(0.3, 0.6))
            resp = session.get(url, timeout=10)
            if resp.status_code != 200:
                continue
        except:
            continue

        soup = BeautifulSoup(resp.text, 'html.parser')
        articles = soup.find_all('article')
        
        if not articles:
            continue

        for art in articles:
            oid = art.get('id') or art.get('data-id')
            if not oid or oid in processed_this_run:
                continue

            raw_text = art.get_text(" ", strip=True).replace('\xa0', ' ').replace('\u202f', ' ')
            text_lower = raw_text.lower()

            title_elem = art.find('h1') or art.find('h2') or art.find('h6')
            title = title_elem.text.strip() if title_elem else f"{brand} {model}"

            # Cena
            price = 0.0
            p_matches = re.findall(r'(\d{1,3}(?:[ \.]\d{3})*|\d{4,7})\s*(PLN|EUR|zł|zl)', raw_text, re.IGNORECASE)
            if p_matches:
                vals = [float(p[0].replace(' ', '').replace('.', '')) for p in p_matches]
                valid_vals = [v for v in vals if 3000 < v < 5000000]
                if valid_vals:
                    price = max(valid_vals)

            if price == 0:
                continue

            # Rocznik
            year = None
            for tag in art.find_all(['li', 'dd', 'span', 'p', 'div']):
                t_str = tag.text.strip()
                if 'cm' in t_str.lower() or 'pln' in t_str.lower() or 'km' in t_str.lower():
                    continue
                y_match = re.search(r'\b(19\d{2}|20\d{2})\b', t_str)
                if y_match:
                    y_val = int(y_match.group(1))
                    if int(year_from) <= y_val <= int(year_to):
                        year = y_val
                        break

            if not year:
                year = int(year_from)

            # Przebieg
            mileage = 0
            m_matches = re.findall(r'\b(\d{1,3}(?:[ \.]\d{3})*|\d+)\s*km\b', text_lower)
            if m_matches:
                m_vals = [int(m.replace(' ', '').replace('.', '')) for m in m_matches]
                valid_m = [m for m in m_vals if 0 < m < 1500000]
                if valid_m:
                    mileage = max(valid_m)

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

    # Oznaczanie sprzedanych
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
    return f"✅ Sukces! Metoda mikro-koszyków pobrała: {total_processed} unikalnych ofert (Nowych: {new_inserts}, Aktualizacji: {updates}). Aktywnych w bazie: {active_in_db} szt."
