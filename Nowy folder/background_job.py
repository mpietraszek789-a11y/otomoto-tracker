"""
Automat uruchamiany co godzinę przez GitHub Actions.

1. Wczytuje listę śledzonych wyszukiwań z tracked_searches.json
2. Dla każdej odświeża oferty (ten sam scraper co w apce)
3. Szuka "okazji": oferty min. 20% poniżej średniej ceny (liczonej bez USA),
   o których jeszcze nie wysłano powiadomienia
4. Wysyła jednego maila z listą okazji (Gmail)

Dane do maila są pobierane ze zmiennych środowiskowych (sekrety GitHub):
EMAIL_SENDER, EMAIL_APP_PASSWORD, EMAIL_RECIPIENT
"""
import os
import json
import html
import smtplib
from datetime import datetime
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

from scraper import get_connection, init_db, scrape_and_update

CONFIG_PATH = Path(__file__).resolve().parent / "tracked_searches.json"

DISCOUNT = 0.20          # oferta musi być min. 20% poniżej średniej
MIN_SAMPLE = 5           # minimalna liczba ofert z UE w roczniku, żeby średnia coś znaczyła
MAX_DEALS_IN_EMAIL = 20  # żeby mail nie zamienił się w ścianę tekstu

USA_COUNTRY_LABELS = {"usa", "stany zjednoczone", "us", "united states", "stany zjednoczone ameryki"}
TZ = ZoneInfo("Europe/Warsaw")


def now_str():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def is_usa(country):
    return bool(country) and str(country).strip().lower() in USA_COUNTRY_LABELS


def load_searches():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def count_offers(brand, model, y_from, y_to):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """SELECT COUNT(*) FROM offers
           WHERE LOWER(brand) = LOWER(?) AND LOWER(model) = LOWER(?)
             AND production_year BETWEEN ? AND ?""",
        (brand.strip(), model.strip(), y_from, y_to),
    )
    n = cur.fetchone()[0]
    conn.close()
    return n


def mark_all_notified(brand, model, y_from, y_to):
    """Tryb startowy: istniejące oferty traktujemy jako 'już znane', żeby pierwszy
    przebieg nie zalał skrzynki mailami o wszystkim, co akurat jest na rynku."""
    conn = get_connection()
    conn.execute(
        """UPDATE offers SET notified_at = ?
           WHERE LOWER(brand) = LOWER(?) AND LOWER(model) = LOWER(?)
             AND production_year BETWEEN ? AND ? AND notified_at IS NULL""",
        (now_str(), brand.strip(), model.strip(), y_from, y_to),
    )
    conn.commit()
    conn.close()


def mark_notified(offer_ids):
    if not offer_ids:
        return
    conn = get_connection()
    conn.executemany(
        "UPDATE offers SET notified_at = ? WHERE id = ?",
        [(now_str(), oid) for oid in offer_ids],
    )
    conn.commit()
    conn.close()


def find_good_deals(brand, model, y_from, y_to):
    """Zwraca aktywne oferty (nie z USA), które są >= 20% poniżej średniej
    swojego rocznika i o których jeszcze nie powiadomiono."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """SELECT id, production_year, title, current_price, mileage_km,
                  country_origin, engine_capacity, accident_free, url, notified_at
           FROM offers
           WHERE LOWER(brand) = LOWER(?) AND LOWER(model) = LOWER(?)
             AND production_year BETWEEN ? AND ? AND status = 'Aktywne'""",
        (brand.strip(), model.strip(), y_from, y_to),
    )
    rows = cur.fetchall()
    conn.close()

    by_year = {}
    for r in rows:
        by_year.setdefault(r[1], []).append(r)

    deals = []
    for year, offers in by_year.items():
        eu_offers = [o for o in offers if not is_usa(o[5])]
        if len(eu_offers) < MIN_SAMPLE:
            continue
        avg = sum(o[3] for o in eu_offers) / len(eu_offers)
        threshold = avg * (1 - DISCOUNT)
        for o in eu_offers:
            if o[9] is None and o[3] <= threshold:
                deals.append({
                    "id": o[0], "year": o[1], "title": o[2], "price": o[3],
                    "mileage": o[4], "country": o[5], "capacity": o[6],
                    "accident_free": o[7], "url": o[8],
                    "avg": avg, "discount_pct": (1 - o[3] / avg) * 100,
                })
    deals.sort(key=lambda d: d["discount_pct"], reverse=True)
    return deals


def fmt_pln(x):
    return f"{int(x):,}".replace(",", " ") + " PLN"


def build_deals_email(label, deals):
    shown = deals[:MAX_DEALS_IN_EMAIL]
    rows = ""
    for d in shown:
        mileage = f"{int(d['mileage']):,}".replace(",", " ") + " km" if d["mileage"] else "brak danych"
        capacity = f"{d['capacity']} cm3" if d["capacity"] else "brak danych"
        rows += f"""
        <tr>
          <td style="padding:8px;border-bottom:1px solid #ddd;">
            <a href="{html.escape(d['url'])}"><b>{html.escape(d['title'])}</b></a><br>
            <span style="color:#555;">rocznik {d['year']} · {mileage} · {capacity} ·
            kraj: {html.escape(d['country'] or 'nieznany')} ·
            bezwypadkowy: {html.escape(d['accident_free'] or 'brak danych')}</span>
          </td>
          <td style="padding:8px;border-bottom:1px solid #ddd;white-space:nowrap;">
            <b style="color:#1e8449;">{fmt_pln(d['price'])}</b><br>
            <span style="color:#1e8449;">-{d['discount_pct']:.0f}% od średniej</span><br>
            <span style="color:#777;">średnia: {fmt_pln(d['avg'])}</span>
          </td>
        </tr>"""
    more = ""
    if len(deals) > len(shown):
        more = f"<p>…i jeszcze {len(deals) - len(shown)} ofert(y) w apce.</p>"
    return f"""
    <html><body style="font-family:Arial,sans-serif;">
      <h2>🚗 Nowe okazje: {html.escape(label)}</h2>
      <p>Oferty co najmniej {int(DISCOUNT * 100)}% poniżej średniej ceny
      (średnia liczona bez ofert z USA):</p>
      <table style="border-collapse:collapse;width:100%;max-width:700px;">{rows}</table>
      {more}
    </body></html>"""


def send_email(subject, html_body):
    sender = os.environ.get("EMAIL_SENDER", "").strip()
    password = os.environ.get("EMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    recipients = [r.strip() for r in os.environ.get("EMAIL_RECIPIENT", "").split(",") if r.strip()]
    if not (sender and password and recipients):
        print("⚠️ Brak danych do wysyłki maila (EMAIL_SENDER / EMAIL_APP_PASSWORD / EMAIL_RECIPIENT) - pomijam.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(sender, password)
            server.sendmail(sender, recipients, msg.as_string())
        print(f"📧 Wysłano maila: {subject}")
        return True
    except Exception as e:
        print(f"❌ Nie udało się wysłać maila: {e}")
        return False


def finalize_db():
    """Scala plik WAL z główną bazą, żeby plik otomoto.db zapisany w repozytorium
    był kompletny (bez dodatkowych plików -wal / -shm)."""
    conn = get_connection()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()


def main():
    init_db()
    searches = load_searches()
    print(f"Śledzonych wyszukiwań: {len(searches)}")

    started_searches = []   # wyszukiwania uruchomione po raz pierwszy
    email_blocks = []       # (etykieta, lista okazji)

    for s in searches:
        brand, model = s["brand"], s["model"]
        y_from, y_to = int(s["year_from"]), int(s["year_to"])
        label = f"{brand} {model} ({y_from}" + (f"-{y_to})" if y_to != y_from else ")")
        print(f"\n=== {label} ===")

        first_run = count_offers(brand, model, y_from, y_to) == 0

        try:
            result = scrape_and_update(
                s.get("category", "Osobowe"), brand, model, y_from, y_to, "",
                engine_capacity_from=s.get("engine_capacity_from"),
                engine_capacity_to=s.get("engine_capacity_to"),
                accident_filter=s.get("accident_filter", "Dowolny"),
            )
            print(result)
        except Exception as e:
            print(f"❌ Błąd przy scrapowaniu {label}: {e}")
            continue

        if first_run:
            n = count_offers(brand, model, y_from, y_to)
            mark_all_notified(brand, model, y_from, y_to)
            started_searches.append((label, n))
            continue

        deals = find_good_deals(brand, model, y_from, y_to)
        print(f"Okazji do powiadomienia: {len(deals)}")
        if deals:
            email_blocks.append((label, deals))

    # Mail z okazjami
    for label, deals in email_blocks:
        subject = f"🚗 {len(deals)} nowych okazji: {label}"
        if send_email(subject, build_deals_email(label, deals)):
            mark_notified([d["id"] for d in deals[:MAX_DEALS_IN_EMAIL]])
            # oferty ponad limit też oznaczamy, żeby nie wracały w kółko
            mark_notified([d["id"] for d in deals[MAX_DEALS_IN_EMAIL:]])

    # Mail powitalny przy pierwszym uruchomieniu wyszukiwania (zarazem test, czy mail działa)
    if started_searches:
        items = "".join(f"<li>{html.escape(l)} - znaleziono {n} ofert</li>" for l, n in started_searches)
        body = f"""<html><body style="font-family:Arial,sans-serif;">
          <h2>✅ Monitoring ofert uruchomiony</h2>
          <p>Od teraz co godzinę sprawdzam:</p><ul>{items}</ul>
          <p>Gdy pojawi się oferta min. {int(DISCOUNT * 100)}% poniżej średniej ceny
          (bez ofert z USA), dostaniesz maila.</p></body></html>"""
        send_email("✅ Monitoring ofert uruchomiony", body)

    finalize_db()
    print("\nGotowe.")


if __name__ == "__main__":
    main()
