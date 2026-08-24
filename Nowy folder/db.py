import sqlite3


def get_connection():
    """Tworzy i zwraca połączenie z plikiem bazy danych."""
    return sqlite3.connect('otomoto.db')


def init_db():
    """Tworzy niezbędne tabele w bazie danych, jeśli jeszcze nie istnieją."""
    conn = get_connection()
    cursor = conn.cursor()

    # 1. Tabela z historią ofert
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            otomoto_id TEXT UNIQUE NOT NULL,      -- Unikalny identyfikator ogłoszenia
            brand TEXT NOT NULL,                   -- Marka (np. Audi)
            model TEXT NOT NULL,                   -- Model (np. A4)
            production_year INTEGER NOT NULL,      -- Rocznik
            title TEXT NOT NULL,                   -- Nazwa oferty
            mileage_km INTEGER,                    -- Przebieg w km
            current_price REAL NOT NULL,           -- Aktualna cena
            url TEXT NOT NULL,                     -- Bezpośredni link do oferty
            status TEXT DEFAULT 'active',          -- Status: 'active' (dostępne) lub 'sold' (sprzedane/wycofane)
            first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- Kiedy pierwszy raz pobrano
            last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP  -- Kiedy ostatnio widzieliśmy ofertę
        )
    ''')

    # 2. Tabela z historią zmian cen
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            offer_id INTEGER,                      -- Identyfikator powiązanej oferty z tabeli 'offers'
            price REAL NOT NULL,                   -- Zanotowana cena
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- Data i godzina rejestracji ceny
            FOREIGN KEY(offer_id) REFERENCES offers(id) ON DELETE CASCADE
        )
    ''')

    conn.commit()
    conn.close()


if __name__ == '__main__':
    init_db()
    print("Baza danych 'otomoto.db' została pomyślnie zainicjowana!")