import sqlite3
import requests
from bs4 import BeautifulSoup
import re
import time
import random
from datetime import datetime, timedelta

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

def scrape_and_update(brand, model, year_from, year_to):
    init_db()
    conn = get_connection()
    cursor = conn.cursor()
    
    brand_clean = brand.strip().lower()
    model_clean = model.strip().lower().replace(" ", "-")
    
    # Lepsze nagłówki udające prawdziwą przeglądarkę użytkownika
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Connection': 'keep-alive'
    }
    
    now_time = datetime.now()
    now_time_str = now_time.strftime("%Y-%m-%d %H:%M:%S")
    
    page = 1
    max_pages = 30
    
    while page <= max_pages:
        url = f"https://www.otomoto.pl/osobowe/{brand_clean}/{model_clean}/{str(page)}?search%5Bfilter_float_year%3Afrom%5D={year_from}&search%5Bfilter_float_year%3Ato%5D={year_to}"
        
        try:
            # Krótka pauza (od 1 do 2 sekund), żeby Otomoto nie uznało nas za robota atakującego serwer
            time.sleep(random.uniform(1.0, 2.0))
            
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code != 200:
                break
        except Exception:
            break
            
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = soup.find_all('article')
        
        if not articles:
            break 

        page_found = 0
        for art in articles:
            try:
                otomoto_id = art.get('id') or art.get('data-id')
                if not otomoto_id:
                    continue

                raw_text = art.get_text(" ", strip=True).replace('\xa0', ' ').replace('\u202f', ' ')
                
                # CENA
                price = 0.0
                price_matches = re.findall(r'\b(\d{1,3}(?:\s\d{3})+|\d{3,7})\s*(?:PLN|EUR)', raw_text)
                prices = [float(p.replace(' ', '')) for p in price_matches]
                valid_prices = [p for p in prices if 3000 < p < 5000000]
                if valid_prices:
                    price = min(valid_prices)

                # PRZEBIEG
                mileage = 0
                mileage_matches = re.findall(r'\b(\d{1,3}(?:\s\d{3})+|\d{1,7})\s*km\b', raw_text)
                mileages = [int(m.replace(' ', '')) for m in mileage_matches]
                valid_mileages = [m for m in mileages if m > 500]
                if valid_mileages:
                    mileage = max(valid_mileages)
                elif mileages:
                    mileage = mileages[0]

                # ROCZNIK
                year = year_from
                year_match = re.search(r'\b(19\d{2}|20\d{2})\b', raw_text)
                if year_match:
                    year = int(year_match.group(1))

                if not (int(year_from) <= year <= int(year_to)):
                    continue

                title_elem = art.find('h2') or art.find('a')
                title = title_elem.text.strip() if title_elem else f"{brand} {model}"
                
                link_elem = art.find('a', href=True)
                offer_url = link_elem['href'] if link_elem else ""

                pub_date = "Brak danych"
                date_match = re.search(r'(?:Dodano|Wystawiono|dzisiaj|wczoraj|\d{2}\.\d{2}\.\d{4})', raw_text, re.IGNORECASE)
                if date_match:
                    pub_date = date_match.group(0)

                if price > 0:
                    page_found += 1
                    
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
                    
            except Exception:
                continue
        
        # Jeśli na stronie nie było nowych unikalnych ofert lub strona jest pusta, kończymy
        if page_found == 0:
            break
        page += 1

    limit_time = (now_time - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''
        UPDATE offers 
        SET status = 'Sprzedane' 
        WHERE LOWER(brand) = LOWER(?) 
          AND LOWER(model) = LOWER(?) 
          AND production_year BETWEEN ? AND ? 
          AND last_seen_at < ?
    ''', (brand_clean, model_clean, year_from, year_to, limit_time))

    cursor.execute('''
        SELECT COUNT(*) FROM offers 
        WHERE LOWER(brand) = LOWER(?) AND LOWER(model) = LOWER(?) AND production_year BETWEEN ? AND ? AND status = 'Aktywne'
    ''', (brand_clean, model_clean, year_from, year_to))
    total_active = cursor.fetchone()[0]

    conn.commit()
    conn.close()
    return f"Sukces! Przeszukano {page-1} stron. Liczba aktywnych ofert w bazie: {total_active}."
