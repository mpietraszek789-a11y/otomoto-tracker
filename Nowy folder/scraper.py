import hashlib
import random
import re
import time
from datetime import datetime, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo

from db import get_connection, init_db

BASE_URL = "https://www.otomoto.pl"
MAX_PAGES = 100
STOP_AFTER_EMPTY_NEW_ID_PAGES = 2


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\xa0", " ").replace("\u202f", " ")).strip()


def _parse_number(value: str) -> int | None:
    if not value:
        return None
    digits = re.sub(r"[^\d]", "", value)
    return int(digits) if digits else None


def _extract_source_total(text: str) -> int | None:
    patterns = [
        r"Liczba ogłoszeń:\s*([\d\s\u00a0\u202f]+)",
        r"Pokaż\s*([\d\s\u00a0\u202f]+)\s*Ogłoszeń",
        r"Wszystkie\s*\(\s*([\d\s\u00a0\u202f]+)\s*\)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            number = _parse_number(match.group(1))
            if number is not None:
                return number
    return None


def _extract_offer_id(article) -> str | None:
    for attr in ("data-id", "data-advert-id", "data-offer-id", "data-listing-id", "id"):
        value = article.get(attr)
        if value:
            return str(value)

    for a in article.find_all("a", href=True):
        href = a.get("href", "")
        if "/oferta/" in href:
            absolute = urljoin(BASE_URL, href)
            digest = hashlib.sha1(absolute.encode("utf-8")).hexdigest()[:20]
            return f"url-{digest}"
    return None


def _extract_price(article_text: str, article) -> float:
    price_selectors = [
        "[data-testid*='price']",
        "[data-cy*='price']",
        "[class*='price']",
        "[aria-label*='cena']",
    ]
    candidate_texts = []
    for selector in price_selectors:
        for elem in article.select(selector):
            txt = _normalize_text(elem.get_text(" ", strip=True))
            if txt:
                candidate_texts.append(txt)

    if not candidate_texts:
        candidate_texts = [article_text]

    best = 0.0
    for text in candidate_texts:
        matches = re.findall(
            r"(\d{1,3}(?:[\s\u00a0\u202f]\d{3})+|\d{4,7})\s*(?:PLN|zł|zl)\b",
            text,
            re.IGNORECASE,
        )
        for raw in matches:
            value = _parse_number(raw)
            if value is not None and 3000 < value < 5_000_000:
                best = max(best, float(value))
    return best


def _extract_year(article_text: str, year_from: int, year_to: int) -> int | None:
    years = [int(x) for x in re.findall(r"\b(19\d{2}|20\d{2})\b", article_text)]
    valid = [y for y in years if year_from <= y <= year_to]
    return valid[0] if valid else None


def _extract_mileage(article_text: str) -> int:
    matches = re.findall(
        r"\b(\d{1,3}(?:[\s\u00a0\u202f]\d{3})*|\d{1,7})\s*km\b",
        article_text,
        re.IGNORECASE,
    )
    values = []
    for raw in matches:
        value = _parse_number(raw)
        if value is not None and value >= 0:
            values.append(value)
    return max(values) if values else 0


def _extract_title(article, fallback: str) -> str:
    for selector in ("h1", "h2", "h3", "h4", "h5", "h6"):
        elem = article.select_one(selector)
        if elem:
            title = _normalize_text(elem.get_text(" ", strip=True))
            if title:
                return title

    for a in article.find_all("a", href=True):
        title = _normalize_text(a.get_text(" ", strip=True))
        if title and len(title) > 4:
            return title
    return fallback


def _extract_url(article) -> str:
    for a in article.find_all("a", href=True):
        href = a.get("href", "")
        if "/oferta/" in href:
            return urljoin(BASE_URL, href)

    a = article.find("a", href=True)
    return urljoin(BASE_URL, a["href"]) if a else ""


def _extract_publication_date(article_text: str) -> str:
    match = re.search(
        r"(Dzisiaj|Wczoraj|\d{1,2}\s+"
        r"(?:stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|sierpnia|"
        r"września|października|listopada|grudnia)\s+\d{4})",
        article_text,
        re.IGNORECASE,
    )
    return match.group(1).capitalize() if match else "Brak danych"


def _find_articles(soup):
    articles = soup.find_all("article")
    if articles:
        return articles
    fallback = soup.select("[data-id][data-testid], [data-advert-id], [data-offer-id]")
    return list(dict.fromkeys(fallback))


def _build_search_url(category: str, brand: str, model: str, year_from: int, year_to: int, page: int) -> str:
    category_slug = "motocykle-i-quady" if category == "Motocykle" else "osobowe"
    brand_slug = re.sub(r"\s+", "-", brand.strip().lower())
    model_slug = re.sub(r"\s+", "-", model.strip().lower())
    return (
        f"{BASE_URL}/{category_slug}/{brand_slug}/{model_slug}/od-{int(year_from)}"
        f"?search%5Bfilter_float_year%3Ato%5D={int(year_to)}&page={page}"
    )


def scrape_and_update(category, brand, model, year_from, year_to):
    init_db()

    year_from = int(year_from)
    year_to = int(year_to)
    if year_from > year_to:
        raise ValueError("Rocznik 'Od' nie może być większy niż 'Do'.")

    brand_value = brand.strip()
    model_value = model.strip()
    if not brand_value or not model_value:
        raise ValueError("Marka i model nie mogą być puste.")

    conn = get_connection()
    cursor = conn.cursor()

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    })

    now = datetime.now(ZoneInfo("Europe/Warsaw"))
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    seen_ids = set()
    pages_scraped = 0
    pages_without_new_ids = 0
    source_total = None
    request_errors = []

    try:
        for page in range(1, MAX_PAGES + 1):
            url = _build_search_url(category, brand_value, model_value, year_from, year_to, page)

            try:
                time.sleep(random.uniform(0.7, 1.3))
                response = session.get(url, timeout=20)
            except requests.RequestException as exc:
                request_errors.append(f"strona {page}: {exc}")
                break

            if response.status_code in (403, 429):
                request_errors.append(
                    f"strona {page}: Otomoto zwróciło HTTP {response.status_code}"
                )
                break
            if response.status_code != 200:
                request_errors.append(f"strona {page}: HTTP {response.status_code}")
                break

            soup = BeautifulSoup(response.text, "html.parser")
            page_text = _normalize_text(soup.get_text(" ", strip=True))
            if source_total is None:
                source_total = _extract_source_total(page_text)

            articles = _find_articles(soup)
            if not articles:
                break

            page_new_ids = 0
            for article in articles:
                try:
                    otomoto_id = _extract_offer_id(article)
                    if not otomoto_id or otomoto_id in seen_ids:
                        continue

                    text = _normalize_text(article.get_text(" ", strip=True))
                    year = _extract_year(text, year_from, year_to)
                    if year is None:
                        continue

                    title = _extract_title(article, f"{brand_value} {model_value}")
                    mileage = _extract_mileage(text)
                    price = _extract_price(text, article)
                    offer_url = _extract_url(article)
                    pub_date = _extract_publication_date(text)

                    seen_ids.add(otomoto_id)
                    page_new_ids += 1

                    cursor.execute(
                        "SELECT id, current_price FROM offers WHERE otomoto_id = ?",
                        (otomoto_id,),
                    )
                    row = cursor.fetchone()

                    if row:
                        offer_db_id, old_price = row
                        if float(old_price) != float(price):
                            cursor.execute(
                                "INSERT INTO price_history (offer_id, price) VALUES (?, ?)",
                                (offer_db_id, price),
                            )
                        cursor.execute(
                            """
                            UPDATE offers
                            SET brand = ?, model = ?, production_year = ?, title = ?,
                                mileage_km = ?, current_price = ?, url = ?,
                                status = 'Aktywne', publication_date = ?, last_seen_at = ?
                            WHERE id = ?
                            """,
                            (
                                brand_value, model_value, year, title, mileage,
                                price, offer_url, pub_date, now_str, offer_db_id,
                            ),
                        )
                    else:
                        cursor.execute(
                            """
                            INSERT INTO offers (
                                otomoto_id, brand, model, production_year, title,
                                mileage_km, current_price, url, status,
                                publication_date, first_seen_at, last_seen_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Aktywne', ?, ?, ?)
                            """,
                            (
                                otomoto_id, brand_value, model_value, year, title,
                                mileage, price, offer_url, pub_date,
                                now_str, now_str,
                            ),
                        )
                        new_id = cursor.lastrowid
                        cursor.execute(
                            "INSERT INTO price_history (offer_id, price) VALUES (?, ?)",
                            (new_id, price),
                        )
                except Exception:
                    continue

            pages_scraped += 1
            if source_total is not None and len(seen_ids) >= source_total:
                break

            if page_new_ids == 0:
                pages_without_new_ids += 1
            else:
                pages_without_new_ids = 0

            if pages_without_new_ids >= STOP_AFTER_EMPTY_NEW_ID_PAGES:
                break

        # Nie oznaczamy ofert jako sprzedane po awarii/banie/zerowym wyniku.
        if seen_ids:
            sold_before = (now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute(
                """
                UPDATE offers
                SET status = 'Sprzedane'
                WHERE LOWER(brand) = LOWER(?)
                  AND LOWER(model) = LOWER(?)
                  AND production_year BETWEEN ? AND ?
                  AND last_seen_at < ?
                """,
                (brand_value, model_value, year_from, year_to, sold_before),
            )

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM offers
            WHERE LOWER(brand) = LOWER(?)
              AND LOWER(model) = LOWER(?)
              AND production_year BETWEEN ? AND ?
              AND status = 'Aktywne'
            """,
            (brand_value, model_value, year_from, year_to),
        )
        total_active = cursor.fetchone()[0]
        conn.commit()
    finally:
        conn.close()
        session.close()

    if not seen_ids:
        error_text = "; ".join(request_errors) if request_errors else "brak poprawnie odczytanych kart"
        raise RuntimeError(
            "Nie udało się odczytać żadnych ofert z Otomoto. "
            f"Powód: {error_text}. Nie zmieniono statusów istniejących ofert na 'Sprzedane'."
        )

    source_label = str(source_total) if source_total is not None else "nieznana"
    warning = ""
    if request_errors:
        warning = " Uwaga: " + "; ".join(request_errors) + "."

    return (
        f"Pobrano {len(seen_ids)} unikalnych ofert z {pages_scraped} stron. "
        f"Otomoto zgłosiło {source_label} ofert. "
        f"Aktywnych w bazie po aktualizacji: {total_active}.{warning}"
    )
