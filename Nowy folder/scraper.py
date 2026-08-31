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

def extract_and_save_offer(art, cursor, now_time_str, brand, model, year_from, year_to, model_parts, enforce_filters):
    otomoto_id = art.get('id') or art.get('data-id')
    if not otomoto_id:
        return False

    raw_text = art.get_text(" ", strip=True).replace('\xa0', ' ').replace('\u202f', ' ')

    if enforce_filters and model_parts:
        text_norm = re.sub(r'[^a-z0-9]', '', raw_text.lower())
        if not all(part in text_norm for part in model_parts if part):
            return False

    price = 0.0
    price_matches = re.findall(r'(\d{1,3}(?: \d{3})*|\d{4,7})\s*(PLN|EUR|zł|zl)', raw_text, re.IGNORECASE)
    if price_matches:
        prices = [float(p[0].replace(' ', '')) for p in price_matches]
        valid_prices = [p for p in prices if 3000 < p < 5000000]
        if valid_prices:
            price = max(valid_prices)

    year = year_from
    if enforce_filters:
        year_matches = re.findall(r'\b(19\d{2}|20\d{2})\b', raw_text)
        valid_year = None
        for ym in year_matches:
            ym_int = int(ym)
            if int(year_from) <= ym_int <= int(year_to) and ym_int != price:
                valid_year = ym_int
                break 
        if not valid_year:
            return False
        year = valid_year
    else:
        year_matches = re.findall(r'\b(19\d{2}|20\d{2})\b', raw_text)
        if year_matches:
            year = int(year_matches[0])

    title_elem = art.find('h1') or art.find('h2') or art.find('h6') or art.find('a')
    title = title_elem.text.strip() if title_elem else f"{brand} {model}"

    mileage = 0
    mileage_matches = re.findall(r'\b(\d{1,3}(?: \d{3})*|\d{1,7})\s*km\b', raw_text)
    if mileage_matches:
        mileages = [int(m.replace(' ', '')) for m in mileage_matches]
        valid_mileages = [m for m in mileages if m > 0]
        if valid_mileages:
            mileage = max(valid_mileages)
    
    link_elem = art.find('a', href=True)
    offer_url = link_elem['href'] if link_elem else ""

    pub_date = "Brak danych"
    date_match = re.search(r'(Dzisiaj|Wczoraj|\d{1,2}\s(?:stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|sierpnia|września|października|listopada|grudnia)\s\d{4})', raw_text, re.IGNORECASE)
    if date_match:
        pub_date = date_match.group(1).capitalize()

    if price > 0:
        cursor.execute("SELECT id, current_price FROM offers WHERE otomoto_id = ?", (otomoto_id,))
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
        else:
            cursor.execute('''
                INSERT INTO offers (otomoto_id, brand, model, production_year, title, mileage_km, current_price, url, status, publication_date, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Aktywne', ?, ?, ?)
            ''', (otomoto_id, brand.strip(), model.strip(), year, title, mileage, price, offer_url, pub_date, now_time_str, now_time_str))
            
            new_id = cursor.lastrowid
            cursor.execute("INSERT INTO price_history (offer_id, price) VALUES (?, ?)", (new_id, price))
        return True
    return False

def scrape_and_update(category, brand, model, year_from, year_to, custom_url=""):
    init_db()
    conn = get_connection()
    cursor = conn.cursor()
    
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept-Language': 'pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    })
    
    now_time = datetime.now(ZoneInfo("Europe/Warsaw"))
    now_time_str = now_time.strftime("%Y-%m-%d %H:%M:%S")
    
    brand_clean = brand.strip()
    model_clean = model.strip()
    model_parts = [re.sub(r'[^a-z0-9]', '', m) for m in model_clean.lower().split()]
    
    total_found = 0

    if custom_url and "otomoto.pl" in custom_url:
        page = 1
        previous_ids = set()
        
        while page <= 25:
            if '?' in custom_url:
                if 'page=' in custom_url:
                    url = re.sub(r'page=\d+', f'page={page}', custom_url)
                else:
                    url = f"{custom_url}&page={page}"
            else:
                url = f"{custom_url}?page={page}"
                
            try:
                time.sleep(random.uniform(0.5, 1.0))
                resp = session.get(url, timeout=10)
                if resp.status_code != 200: break
            except:
                break
                
            soup = BeautifulSoup(resp.text, 'html.parser')
            articles = soup.find_all('article')
            if not articles: break 

            current_ids = set()
            for art in articles:
                oid = art.get('id') or art.get('data-id')
                if oid: current_ids.add(oid)
                
                # Złoty środek: W trybie linku wyłączamy restrykcyjne filtry, ufając temu, co wkleił użytkownik
                if extract_and_save_offer(art, cursor, now_time_str, brand_clean, model_clean, year_from, year_to, model_parts, enforce_filters=False):
                    total_found += 1
            
            overlap = len(current_ids.intersection(previous_ids))
            if page > 1 and overlap >= 20: break
            previous_ids = current_ids
            page += 1

    else:
        # Standardowy system Mikro-Koszyków (Omija system anty-botowy blokujący paginację)
        category_slug = "motocykle-i-quady" if category == "Motocykle" else "osobowe"
        b_slug = brand_clean.lower().replace(" ", "-")
        m_slug = model_clean.lower().replace(" ", "-")
        
        # Koszyki co 20 tys. PLN dbają o to, żeby na każdym przedziale była z reguły tylko jedna strona.
        price_brackets = [(i, i + 19999) for i in range(0, 300000, 20000)]
        price_brackets.extend([(300000, 499999), (500000, 5000000)])
        
        for p_min, p_max in price_brackets:
            page = 1
            previous_ids = set()
            
            while page <= 3:
                url = f"https://www.otomoto.pl/{category_slug}/{b_slug}/{m_slug}/od-{year_from}"
                params = {
                    "search[filter_float_year:to]": year_to,
                    "search[filter_float_price:from]": p_min,
                    "search[filter_float_price:to]": p_max,
                    "page": page
                }
                try:
                    time.sleep(random.uniform(0.4, 0.8))
                    resp = session.get(url, params=params, timeout=10)
                    if resp.status_code != 200: break
                except:
                    break
                    
                soup = BeautifulSoup(resp.text, 'html.parser')
                articles = soup.find_all('article')
                if not articles: break 

                current_ids = set()
                for art in articles:
                    oid = art.get('id') or art.get('data-id')
                    if oid: current_ids.add(oid)
                    
                    if extract_and_save_offer(art, cursor, now_time_str, brand_clean, model_clean, year_from, year_to, model_parts, enforce_filters=True):
                        total_found += 1
                
                # Jeśli stron zwróciła mniej niż 28 elementów, to z całą pewnością jest to ostatnia strona
                if len(articles) < 28:
                    break

                overlap = len(current_ids.intersection(previous_ids))
                if page > 1 and overlap >= 20: break
                previous_ids = current_ids
                page += 1

    limit_time = (now_time - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''
        UPDATE offers 
        SET status = 'Sprzedane' 
        WHERE LOWER(brand) = LOWER(?) AND LOWER(model) = LOWER(?) 
          AND production_year BETWEEN ? AND ? AND last_seen_at < ?
    ''', (brand_clean, model_clean, year_from, year_to, limit_time))

    cursor.execute('''
        SELECT COUNT(*) FROM offers 
        WHERE LOWER(brand) = LOWER(?) AND LOWER(model) = LOWER(?) 
          AND production_year BETWEEN ? AND ? AND status = 'Aktywne'
    ''', (brand_clean, model_clean, year_from, year_to))
    
    total_active = cursor.fetchone()[0]
    conn.commit()
    conn.close()
    
    mode = "Z Linku" if custom_url else "Baza"
    return f"Sukces ({mode}). Przeanalizowano aut: {total_found}. Łącznie aktywnych ofert w bazie: {total_active}."
