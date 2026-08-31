import pandas as pd
import streamlit as st

from db import get_connection, init_db
from scraper import scrape_and_update

st.set_page_config(page_title="Otomoto Tracker dla Taty", layout="wide")
init_db()

st.title("Otomoto Tracker - Podgląd Ofert")

st.sidebar.header("Kryteria wyszukiwania")
category = st.sidebar.radio("Kategoria", ["Motocykle", "Osobowe"])
default_brand = "Yamaha" if category == "Motocykle" else "Toyota"
default_model = "MT-07" if category == "Motocykle" else "Yaris"
brand = st.sidebar.text_input("Marka", default_brand)
model = st.sidebar.text_input("Model", default_model)
col1, col2 = st.sidebar.columns(2)
year_from = col1.number_input("Rocznik od", 1990, 2026, 2018)
year_to = col2.number_input("Rocznik do", 1990, 2026, 2023)

if st.sidebar.button("Pobierz / Odśwież dane z Otomoto", type="primary"):
    with st.spinner(f"Pobieram oferty z kategorii: {category}..."):
        try:
            msg = scrape_and_update(category, brand, model, year_from, year_to)
            st.sidebar.success(msg)
        except Exception as exc:
            st.sidebar.error(str(exc))

try:
    conn = get_connection()
    query = """
        SELECT id, otomoto_id, production_year AS Rocznik, title AS Oferta,
               current_price AS "Cena (PLN)", mileage_km AS "Przebieg (km)",
               status AS Status, publication_date AS "Data publikacji",
               last_seen_at AS "Ostatnia aktualizacja", url AS Link
        FROM offers
        WHERE LOWER(brand) = LOWER(?)
          AND LOWER(model) = LOWER(?)
          AND production_year BETWEEN ? AND ?
        ORDER BY first_seen_at DESC, "Cena (PLN)" ASC
    """
    df = pd.read_sql(query, conn, params=(brand.strip(), model.strip(), int(year_from), int(year_to)))

    if not df.empty:
        first_price_df = pd.read_sql(
            """
            SELECT offer_id, price
            FROM price_history
            WHERE id IN (
                SELECT MIN(id) FROM price_history GROUP BY offer_id
            )
            """,
            conn,
        )
        first_prices = dict(zip(first_price_df["offer_id"], first_price_df["price"]))

        def dynamic_status(row):
            if row["Status"] == "Sprzedane":
                return "Sprzedane"
            first_price = first_prices.get(row["id"])
            if first_price is not None and float(first_price) != float(row["Cena (PLN)"]):
                return "Zmieniono cenę"
            return "Aktywne"

        df["Dynamic_Status"] = df.apply(dynamic_status, axis=1)
    conn.close()
except Exception as exc:
    st.error(f"Błąd odczytu bazy: {exc}")
    df = pd.DataFrame()

if df.empty:
    st.info("Brak danych w bazie. Sprawdź markę, model i rocznik, a następnie kliknij „Pobierz / Odśwież dane z Otomoto”.")
else:
    active_count = int((df["Status"] == "Aktywne").sum())
    all_count = len(df)
    m1, m2 = st.columns(2)
    m1.metric("Aktywne oferty", active_count)
    m2.metric("Wszystkie zapisane oferty", all_count)

    years = sorted(df["Rocznik"].unique(), reverse=True)
    tabs = st.tabs([f"Rocznik {year}" for year in years] + ["🕒 Historia Ofert (Filtry i Sortowanie)"])

    for idx, year in enumerate(years):
        with tabs[idx]:
            active_year = df[(df["Rocznik"] == year) & (df["Status"] == "Aktywne")].copy()
            avg_price = active_year["Cena (PLN)"].mean() if not active_year.empty else 0
            avg_mileage = active_year["Przebieg (km)"].mean() if not active_year.empty else 0
            c1, c2 = st.columns(2)
            c1.metric("Średnia cena", f"{avg_price:,.0f} PLN".replace(",", " "))
            c2.metric("Średni przebieg", f"{avg_mileage:,.0f} km".replace(",", " "))
            st.divider()

            if active_year.empty:
                st.info("Brak aktywnych ofert dla tego rocznika.")
            else:
                disp_df = active_year[[
                    "otomoto_id", "Oferta", "Cena (PLN)", "Przebieg (km)",
                    "Data publikacji", "Ostatnia aktualizacja", "Link"
                ]].copy()
                disp_df.rename(columns={"otomoto_id": "ID Oferty"}, inplace=True)
                disp_df["Cena (PLN)"] = disp_df["Cena (PLN)"].apply(
                    lambda x: f"{int(x):,} PLN".replace(",", " ") if x and x > 0 else "Brak ceny"
                )
                disp_df["Przebieg (km)"] = disp_df["Przebieg (km)"].apply(
                    lambda x: f"{int(x):,} km".replace(",", " ") if x and x > 0 else "Brak danych"
                )
                st.dataframe(disp_df, use_container_width=True, hide_index=True)

    with tabs[-1]:
        st.subheader("Wszystkie oferty - filtrowanie i sortowanie")
        col_f1, col_f2 = st.columns(2)
        status_filter = col_f1.multiselect(
            "Filtruj po statusie:",
            options=["Aktywne", "Zmieniono cenę", "Sprzedane"],
            default=["Aktywne", "Zmieniono cenę", "Sprzedane"],
        )
        search_query = col_f2.text_input("Szukaj w tytule oferty:", "")
        filtered_df = df[df["Dynamic_Status"].isin(status_filter)].copy()
        if search_query:
            filtered_df = filtered_df[filtered_df["Oferta"].str.contains(search_query, case=False, na=False)]

        hist_disp = filtered_df[[
            "otomoto_id", "Rocznik", "Oferta", "Cena (PLN)", "Przebieg (km)",
            "Dynamic_Status", "Data publikacji", "Ostatnia aktualizacja", "Link"
        ]].copy()
        hist_disp.rename(columns={"otomoto_id": "ID Oferty", "Dynamic_Status": "Status"}, inplace=True)
        hist_disp["Cena (PLN)"] = hist_disp["Cena (PLN)"].apply(
            lambda x: f"{int(x):,} PLN".replace(",", " ") if x and x > 0 else "Brak ceny"
        )
        hist_disp["Przebieg (km)"] = hist_disp["Przebieg (km)"].apply(
            lambda x: f"{int(x):,} km".replace(",", " ") if x and x > 0 else "Brak danych"
        )
        st.dataframe(hist_disp, use_container_width=True, hide_index=True)
