import sqlite3
import requests
from bs4 import BeautifulSoup
import re
import time
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlparse, parse_qsl


def get_connection():
    return sqlite3.connect('otomoto.db')


def ensure_country_column(cursor):
    """Dodaje kolumnę country_origin, jeśli jej jeszcze nie ma - sprawdzone wprost
    przez PRAGMA, a nie przez łapanie wyjątku (bardziej niezawodne między środowiskami)."""
    cursor.execute("PRAGMA table_info(offers)")
    existing_cols = [row[1] for row in cursor.fetchall()]
    if 'country_origin' not in existing_cols:
        cursor.execute("ALTER TABLE offers ADD COLUMN country_origin TEXT")


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
            country_origin TEXT,
            first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    ensure_country_column(cursor)
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


def fetch_country_origin(session, offer_url):
    """
    Wchodzi na stronę pojedynczej oferty i wyciąga 'Kraj pochodzenia' ze specyfikacji.
    To pole nie jest dostępne na liście wyników, tylko na stronie szczegółowej -
    stąd dodatkowe zapytanie. Zwraca None, jeśli nie uda się znaleźć (np. sprzedający
    nie wypełnił tego pola).
    """
    if not offer_url:
        return None
    try:
        resp = session.get(offer_url, timeout=10)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, 'html.parser')
        # Etykieta "Kraj pochodzenia" i wartość (np. "Polska") to sąsiadujące <p>,
        # więc szukamy po tekście etykiety, niezależnie od losowych nazw klas CSS.
        label = soup.find(lambda tag: tag.name == 'p' and tag.get_text(strip=True) == 'Kraj pochodzenia')
        if not label:
            return None
        value_tag = label.find_next('p')
        if not value_tag:
            return None
        value = value_tag.get_text(strip=True)
        return value if value else None
    except Exception:
        return None


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
    ensure_country_column(cursor)
    conn.commit()

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)',
        'Accept-Language': 'pl-PL,pl;q=0.9',
    })

    now_time = datetime.now(ZoneInfo("Europe/Warsaw"))
    now_time_str = now_time.strftime("%Y-%m-%d %H:%M:%S")

    new_inserts = 0
    updates = 0
    processed_this_run = set()

    diag_total_articles = 0
    diag_rejected_price = 0
    diag_rejected_year = 0
    diag_rejected_model_mismatch = 0

    cat_slug = "motocykle-i-quady" if category == "Motocykle" else "osobowe"
    b_slug, m_slug = build_otomoto_slugs(brand, model)

    base_url = f"https://www.otomoto.pl/{cat_slug}/{b_slug}/{m_slug}/od-{year_from}"

    base_params = {
        "search[filter_float_year:to]": year_to
    }

    # Koszyki cenowe (wymuszają pokazanie innych aut bez potrzeby paginacji po całym zakresie cen)
    price_brackets = [(i, i + 19999) for i in range(0, 300000, 20000)]
    price_brackets.append((300000, 5000000))

    MAX_PAGES_PER_BRACKET = 15  # zabezpieczenie przed nieskończoną pętlą

    for p_min, p_max in price_brackets:
        page = 1

        while page <= MAX_PAGES_PER_BRACKET:
            params = base_params.copy()
            params["search[filter_float_price:from]"] = p_min
            params["search[filter_float_price:to]"] = p_max
            params["page"] = page

            try:
                time.sleep(random.uniform(0.3, 0.7))
                resp = session.get(base_url, params=params, timeout=10)
                if resp.status_code != 200:
                    break
            except Exception:
                break

            soup = BeautifulSoup(resp.text, 'html.parser')
            articles = soup.find_all('article')

            # Brak ogłoszeń na tej stronie = koniec wyników dla tego koszyka cenowego
            if not articles:
                break

            found_new_on_page = False
            plausible_match_this_page = False

            for art in articles:
                oid = art.get('id') or art.get('data-id')
                if not oid:
                    continue
                if oid in processed_this_run:
                    # To ogłoszenie już przetworzyliśmy (np. duplikat na styku stron) - pomijamy,
                    # ale to NIE oznacza końca wyników, więc nie przerywamy pętli.
                    continue

                found_new_on_page = True
                diag_total_articles += 1

                # Tytuł ogłoszenia siedzi w <a aria-label="..."> - to też najbardziej
                # wiarygodne miejsce do weryfikacji, czy oferta faktycznie dotyczy
                # szukanego modelu (a nie np. dealera/rekomendacji w tym samym <article>).
                title_link = art.find('a', attrs={'aria-label': True})
                title_text = title_link.get('aria-label', '').strip() if title_link else ""
                if not title_text:
                    title_text = f"{brand} {model}"
                title_lower = title_text.lower()

                model_tokens = [t for t in model.strip().lower().split() if len(t) > 1]
                if model_tokens and not any(tok in title_lower for tok in model_tokens):
                    diag_rejected_model_mismatch += 1
                    continue

                plausible_match_this_page = True

                # Cena: siedzi w <h3><span translate="no">104 900</span></h3>. Uwaga: obok może
                # być przekreślona "cena przed obniżką" w <del> - nie bierzemy jej pod uwagę,
                # bo szukamy konkretnie <h3>, nie <del>.
                price = 0.0
                price_h3 = art.find('h3')
                if price_h3:
                    price_span = price_h3.find('span')
                    price_raw = (price_span.get_text(strip=True) if price_span
                                 else price_h3.get_text(strip=True))
                    price_digits = re.sub(r'\D', '', price_raw)
                    if price_digits:
                        try:
                            price = float(price_digits)
                        except ValueError:
                            price = 0.0

                if not (3000 < price < 5000000):
                    diag_rejected_price += 1
                    continue

                # Rocznik: precyzyjnie z <dd data-parameter="year">2022</dd>, a nie zgadywanie
                # z całego tekstu (gdzie łatwo pomylić rok z pojemnością silnika typu "1991 cm3").
                year = None
                year_dd = art.find('dd', attrs={'data-parameter': 'year'})
                if year_dd:
                    y_match = re.search(r'(19\d{2}|20\d{2})', year_dd.get_text(strip=True))
                    if y_match:
                        y_val = int(y_match.group(1))
                        if int(year_from) <= y_val <= int(year_to):
                            year = y_val

                if not year:
                    diag_rejected_year += 1
                    continue

                # Przebieg: z <dd data-parameter="mileage">...70 001 km</dd>
                mileage = 0
                mileage_dd = art.find('dd', attrs={'data-parameter': 'mileage'})
                if mileage_dd:
                    mileage_digits = re.sub(r'\D', '', mileage_dd.get_text(strip=True))
                    if mileage_digits:
                        try:
                            mileage_val = int(mileage_digits)
                            if 0 < mileage_val < 1500000:
                                mileage = mileage_val
                        except ValueError:
                            mileage = 0

                title = title_text
                offer_url = title_link['href'] if title_link and title_link.has_attr('href') else ""

                processed_this_run.add(oid)

                # Zapis do bazy - UPSERT (INSERT ... ON CONFLICT DO UPDATE) zamiast
                # osobnego SELECT+INSERT/UPDATE, żeby nie było możliwości naruszenia
                # unikalności otomoto_id nawet przy równoległych odświeżeniach (np.
                # Streamlit uruchamiający skrypt ponownie w trakcie działania).
                cursor.execute("SELECT current_price, country_origin FROM offers WHERE otomoto_id = ?", (oid,))
                existing = cursor.fetchone()

                if existing:
                    old_price, existing_country = existing
                    country_origin = existing_country
                    if not existing_country:
                        country_origin = fetch_country_origin(session, offer_url)
                        time.sleep(random.uniform(0.3, 0.6))
                    price_changed = (old_price != price)
                    updates += 1
                else:
                    old_price = None
                    country_origin = fetch_country_origin(session, offer_url)
                    time.sleep(random.uniform(0.3, 0.6))
                    price_changed = True
                    new_inserts += 1

                cursor.execute('''
                    INSERT INTO offers (otomoto_id, brand, model, production_year, title, mileage_km, current_price, url, status, publication_date, country_origin, first_seen_at, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Aktywne', 'Brak danych', ?, ?, ?)
                    ON CONFLICT(otomoto_id) DO UPDATE SET
                        current_price = excluded.current_price,
                        mileage_km = excluded.mileage_km,
                        status = 'Aktywne',
                        last_seen_at = excluded.last_seen_at,
                        country_origin = excluded.country_origin
                ''', (oid, brand.strip(), model.strip(), year, title, mileage, price, offer_url, country_origin, now_time_str, now_time_str))

                cursor.execute("SELECT id FROM offers WHERE otomoto_id = ?", (oid,))
                offer_db_id = cursor.fetchone()[0]

                if price_changed:
                    cursor.execute("INSERT INTO price_history (offer_id, price) VALUES (?, ?)", (offer_db_id, price))

            # Jeśli strona nie dała ANI JEDNEGO ogłoszenia pasującego do modelu - to prawdopodobnie
            # weszliśmy w strefę rekomendacji/podobnych ofert Otomoto, a nie kolejną stronę realnych
            # wyników. Przerywamy dla tego koszyka, żeby nie zbierać śmieciowych danych.
            if not plausible_match_this_page:
                break

            page += 1

    # Sprzedane auta
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
    return (
        f"Pomyślnie zgrano {total_processed} prawidłowych ofert. W bazie znajduje się teraz: {active_in_db} aut. "
        f"[DIAGNOSTYKA: znalezionych artykułów={diag_total_articles}, "
        f"niezgodnych z modelem={diag_rejected_model_mismatch}, "
        f"odrzuconych przez cenę={diag_rejected_price}, odrzuconych przez rocznik={diag_rejected_year}]"
    )
