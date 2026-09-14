import sqlite3
import requests
from bs4 import BeautifulSoup
import re
import time
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

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

def update_url_page(url, page):
    """Profesjonalne nadpisywanie numeru strony w linku, bez złośliwego duplikowania parametrów"""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query['page'] = [str(page)]
    new_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=new_query))

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

    # Jeśli użytkownik nie wklei gotowego linku, budujemy go czysto
    if not custom_url:
        brand_clean = brand.strip().lower().replace(" ", "-")
        model_clean = model.strip().lower().replace(" ", "-")
        cat_slug = "motocykle-i-quady" if category == "Motocykle" else "osobowe"
        custom_url = f"https://www.otomoto.pl/{cat_slug}/{brand_clean}/{model_clean}?search%5Bfilter_float_year%3Afrom%5D={year_from}&search%5Bfilter_float_year%3Ato%5D={year_to}"

    page = 1
    new_inserts = 0
    updates = 0
    previous_ids = set()
    processed_this_run = set() # Zabezpieczenie przed podwójnym zliczaniem
    
    while page <= 25:
        url = update_url_page(custom_url, page)
        
        try:
            time.sleep(random.uniform(0.5, 1.2))
            resp = session.get(url, timeout=10)
            if resp.status_code != 200: 
                break
        except:
            break
            
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # BŁĄD NAPRAWIONY: Bierzemy tylko faktyczne kafelki ogłoszeń, ignorując banery i artykuły
        articles = soup.find_all('article', attrs={"data-testid": "listing-ad"})
        if not articles: 
            break 
            
        current_ids = set()
        for art in articles:
            oid = art.get('id') or art.get('data-id')
            if not oid: continue
            current_ids.add(oid)
            
            # Weryfikacja duplikatów: jeśli na tej stronie znów są te same promowane oferty, ignorujemy!
            if oid in processed_this_run:
                continue
            
            title_elem = art.find('h1') or art.find('h2') or art.find('h6')
            if not title_elem:
                continue
            title = title_elem.text.strip()
            
            a_elem = art.find('a', href=True)
            offer_url = a_elem['href'] if a_elem else ""
            
            # Cena - szukamy konkretnie bloku z ceną, omijając raty miesięczne
            price = 0.0
            price_text_elem = art.find('span', string=re.compile(r'PLN|EUR', re.IGNORECASE))
            if price_text_elem:
                p_text = price_text_elem.text.replace(' ', '')
                match = re.search(r'(\d+)', p_text)
                if match:
                    val = float(match.group(1))
                    if 3000 < val < 5000000:
                        price = val
            
            if price == 0:
                raw_text = art.get_text(" ", strip=True).replace('\xa0', ' ').replace('\u202f', ' ')
                p_matches = re.findall(r'(\d{1,3}(?: \d{3})*|\d{4,7})\s*(PLN|EUR)', raw_text, re.IGNORECASE)
                if p_matches:
                    vals = [float(p[0].replace(' ', '')) for p in p_matches]
                    valid_vals = [v for v in vals if 3000 < v < 5000000]
                    if valid_vals: price = max(valid_vals)
                    
            if price == 0:
                continue

            # BŁĄD NAPRAWIONY: Szukamy Rocznika i Przebiegu TYLKO w specjalnych komórkach parametrów, 
            # a nie w całym zlepku słów.
            year = year_from
            mileage = 0
            
            params_elements = art.find_all(['dd', 'li'])
            for elem in params_elements:
                txt = elem.text.strip().replace(' ', '')
                if 'km' in txt.lower():
                    m_match = re.search(r'(\d+)', txt)
                    if m_match: mileage = int(m_match.group(1))
                # Dokładne sprawdzenie, czy ta komórka to wyłącznie 4 cyfry
                elif re.fullmatch(r'19\d{2}|20\d{2}', txt):
                    year = int(txt)
                    
            # Weryfikacja roku – jeśli to ułuda z promowanych ofert, wyrzucamy auto.
            if not (int(year_from) <= year <= int(year_to)):
                continue

            # Auto przeszło filtry! Dodajemy do przetworzonych
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

        # Zabezpieczenie anty-botowe (jeśli strona 2 ma 90% identycznych aut co strona 1, kończymy)
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
    return f"✅ Raport: Pobrano dokładnie {total_processed} unikalnych ofert (Dodano nowych: {new_inserts}, Zaktualizowano: {updates}). Aktualnie w bazie dla tych kryteriów: {active_in_db} aut."
