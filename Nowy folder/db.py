import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "otomoto.db"


def get_connection():
    """Zwraca połączenie z jedną, stałą bazą SQLite."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Tworzy tabele i uzupełnia brakujące kolumny starej bazy."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            otomoto_id TEXT UNIQUE NOT NULL,
            brand TEXT NOT NULL,
            model TEXT NOT NULL,
            production_year INTEGER NOT NULL,
            title TEXT NOT NULL,
            mileage_km INTEGER DEFAULT 0,
            current_price REAL NOT NULL DEFAULT 0,
            url TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Aktywne',
            publication_date TEXT DEFAULT 'Brak danych',
            first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            offer_id INTEGER NOT NULL,
            price REAL NOT NULL,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(offer_id) REFERENCES offers(id) ON DELETE CASCADE
        )
    """)

    columns = {row[1] for row in cursor.execute("PRAGMA table_info(offers)").fetchall()}
    if "publication_date" not in columns:
        cursor.execute(
            "ALTER TABLE offers ADD COLUMN publication_date TEXT DEFAULT 'Brak danych'"
        )

    cursor.execute("UPDATE offers SET status = 'Aktywne' WHERE LOWER(status) = 'active'")
    cursor.execute("UPDATE offers SET status = 'Sprzedane' WHERE LOWER(status) = 'sold'")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Baza danych zainicjalizowana: {DB_PATH}")
