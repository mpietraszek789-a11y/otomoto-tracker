import sqlite3
import requests
from bs4 import BeautifulSoup
import re
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

    url = f"https://www.otomoto.pl/osobowe/{brand_clean}/{model_clean}?search%5Bfilter_float_year%3Afrom%5D={year_from}&search%5Bfilter_float_year%3Ato%5D={year_to}"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
    except Exception:
        return "Błąd połączenia z siecią."

    soup = BeautifulSoup(response.text, 'html.parser')
    articles = soup.find_all('article')

    if not articles:
        return f"Brak ofert na stronie."

    found_count = 0
    now_time = datetime.now()
    now_time_str = now_time.strftime("%Y-%m-%d %H:%M:%S")

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

            if price > 0:
                found_count += 1
                cursor.execute("SELECT id, current_price FROM offers WHERE otomoto_id = ?", (otomoto_id,))
                row = cursor.fetchone()

                if row:
                    offer_db_id, old_price = row
                    if old_price != price:
                        cursor.execute("INSERT INTO price_history (offer_id, price) VALUES (?, ?)",
                                       (offer_db_id, price))

                    # Jeśli auto znów się pojawiło, od razu dostaje status 'Aktywne' i aktualizujemy datę
                    cursor.execute("""
                        UPDATE offers 
                        SET current_price = ?, mileage_km = ?, status = 'Aktywne', last_seen_at = ? 
                        WHERE id = ?
                    """, (price, mileage, now_time_str, offer_db_id))
                else:
                    cursor.execute('''
                        INSERT INTO offers (otomoto_id, brand, model, production_year, title, mileage_km, current_price, url, status, first_seen_at, last_seen_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Aktywne', ?, ?)
                    ''', (otomoto_id, brand.strip(), model.strip(), year, title, mileage, price, offer_url,
                          now_time_str, now_time_str))

                    new_id = cursor.lastrowid
                    cursor.execute("INSERT INTO price_history (offer_id, price) VALUES (?, ?)", (new_id, price))

        except Exception:
            continue

    # BEZPIECZNE OZNACZANIE JAKO SPRZEDANE:
    # Auto uznajemy za sprzedane TYLKO wtedy, gdy minęły ponad 3 dni od ostatniego momentu,
    # kiedy widzimy je na Otomoto. Zapobiega to fałszywym alarmom, gdy auto chwilowo spadnie na dalszą stronę.
    limit_time = (now_time - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute('''
        UPDATE offers 
        SET status = 'Sprzedane' 
        WHERE LOWER(brand) = LOWER(?) 
          AND LOWER(model) = LOWER(?) 
          AND production_year BETWEEN ? AND ? 
          AND last_seen_at < ?
    ''', (brand_clean, model_clean, year_from, year_to, limit_time))

    conn.commit()
    conn.close()
    return f"Sukces! Odświeżono dane. Aktywnych ofert: {found_count}."