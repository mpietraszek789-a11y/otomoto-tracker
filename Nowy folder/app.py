import streamlit as st
import pandas as pd
from scraper import get_connection, init_db, scrape_and_update

st.set_page_config(page_title="Otomoto Tracker dla Taty", layout="wide")

try:
    init_db()
except Exception:
    pass

st.title("🚗 Otomoto Tracker - Podgląd Ofert")

# Panel boczny
st.sidebar.header("Kryteria Wyszukiwania")
brand = st.sidebar.text_input("Marka", "Toyota")
model = st.sidebar.text_input("Model", "Yaris")

col1, col2 = st.sidebar.columns(2)
year_from = col1.number_input("Rocznik Od", 1990, 2026, 2018)
year_to = col2.number_input("Rocznik Do", 1990, 2026, 2023)

if st.sidebar.button("Pobierz / Odśwież dane z Otomoto", type="primary"):
    with st.spinner("Aktualizuję oferty (szukam nowych i zmian cen)..."):
        msg = scrape_and_update(brand, model, year_from, year_to)
        st.sidebar.success(msg)

# Odczyt danych z bazy
try:
    conn = get_connection()
    query = '''
        SELECT id, otomoto_id, production_year as Rocznik, title as Oferta, 
               current_price as "Cena (PLN)", mileage_km as "Przebieg (km)", 
               status as Status, url as Link, first_seen_at
        FROM offers 
        WHERE LOWER(brand) = LOWER(?) 
          AND LOWER(model) = LOWER(?)
          AND production_year BETWEEN ? AND ?
        ORDER BY first_seen_at DESC, "Cena (PLN)" ASC
    '''
    df = pd.read_sql(query, conn, params=(brand.strip(), model.strip(), int(year_from), int(year_to)))

    # Sprawdzamy czy cena się zmieniła w stosunku do pierwszej zanotowanej ceny w historii
    if not df.empty:
        dynamic_statuses = []
        cursor = conn.cursor()
        for idx, row in df.iterrows():
            if row['Status'] == 'Sprzedane':
                dynamic_statuses.append('Sprzedane')
                continue

            cursor.execute("SELECT price FROM price_history WHERE offer_id = ? ORDER BY recorded_at ASC LIMIT 1",
                           (row['id'],))
            first_row = cursor.fetchone()

            if first_row:
                first_price = first_row[0]
                if first_price != row['Cena (PLN)']:
                    dynamic_statuses.append('Zmieniono cenę')
                else:
                    dynamic_statuses.append('Aktywne')
            else:
                dynamic_statuses.append(row['Status'])

        df['Dynamic_Status'] = dynamic_statuses
    else:
        df['Dynamic_Status'] = []

    conn.close()
except Exception as e:
    df = pd.DataFrame()

if df.empty:
    st.info("Brak danych w bazie. Kliknij przycisk po lewej stronie, aby pobrać najświeższe oferty!")
else:
    years = sorted(df['Rocznik'].unique(), reverse=True)
    tabs = st.tabs([f"Rocznik {y}" for y in years] + ["🕒 Historia Ofert (Wszystkie)"])

    # 1. ZAKŁADKI ROCZNIKÓW (Tylko aktywne)
    for idx, year in enumerate(years):
        with tabs[idx]:
            active_year_raw = df[(df['Rocznik'] == year) & (df['Status'] == 'Aktywne')]

            avg_price = active_year_raw["Cena (PLN)"].mean() if not active_year_raw.empty else 0
            avg_mileage = active_year_raw["Przebieg (km)"].mean() if not active_year_raw.empty else 0

            m1, m2 = st.columns(2)
            m1.metric("💰 Średnia Cena", f"{avg_price:,.0f} PLN".replace(",", " "))
            m2.metric("🚘 Średni Przebieg", f"{avg_mileage:,.0f} km".replace(",", " "))

            st.divider()

            if active_year_raw.empty:
                st.info("Brak aktywnych ofert dla tego rocznika.")
            else:
                disp_df = active_year_raw[['Oferta', 'Cena (PLN)', 'Przebieg (km)', 'Link']].copy()
                disp_df['Cena (PLN)'] = disp_df['Cena (PLN)'].apply(
                    lambda x: f"{int(x):,} PLN".replace(",", " ") if x > 0 else "Brak ceny")
                disp_df['Przebieg (km)'] = disp_df['Przebieg (km)'].apply(
                    lambda x: f"{int(x):,} km".replace(",", " ") if x > 0 else "Brak danych")
                st.table(disp_df)

    # 2. ZAKŁADKA: HISTORIA OFERT (Wszystkie wpisy z kolorami statusów)
    with tabs[-1]:
        st.subheader("Wszystkie oferty w jednej tabeli – podgląd zmian i statusów")

        # Tworzymy kopię i od razu zmieniamy nazwę kolumny, żeby uniknąć błędów
        hist_disp = df[['Rocznik', 'Oferta', 'Cena (PLN)', 'Przebieg (km)', 'Dynamic_Status', 'Link']].copy()
        hist_disp.rename(columns={'Dynamic_Status': 'Status'}, inplace=True)


        def highlight_all(row):
            # Tutaj sprawdzamy już kolumnę 'Status', a nie 'Dynamic_Status'
            status = row['Status']
            if status == 'Sprzedane':
                return ['background-color: rgba(255, 60, 60, 0.2); color: #ff6666;'] * len(row)
            elif status == 'Zmieniono cenę':
                return ['background-color: rgba(255, 204, 0, 0.2); color: #ffcc00;'] * len(row)
            return [''] * len(row)


        hist_disp['Cena (PLN)'] = hist_disp['Cena (PLN)'].apply(
            lambda x: f"{int(x):,} PLN".replace(",", " ") if x > 0 else "Brak ceny")
        hist_disp['Przebieg (km)'] = hist_disp['Przebieg (km)'].apply(
            lambda x: f"{int(x):,} km".replace(",", " ") if x > 0 else "Brak danych")

        styled_hist = hist_disp.style.apply(highlight_all, axis=1)
        st.table(styled_hist)