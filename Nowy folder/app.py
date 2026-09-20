import streamlit as st
import pandas as pd
from scraper import get_connection, init_db, scrape_and_update

st.set_page_config(page_title="Motocyklowy kolektor cen rynkowych", layout="wide")

try:
    init_db()
except Exception:
    pass

st.title("Motocyklowy kolektor cen rynkowych")

# Panel boczny
st.sidebar.header("Kryteria Wyszukiwania")
category = st.sidebar.radio("Kategoria", ["Motocykle", "Osobowe"])

default_brand = "Yamaha" if category == "Motocykle" else "Mercedes-Benz"
default_model = "MT-07" if category == "Motocykle" else "CLA"

brand = st.sidebar.text_input("Marka (do bazy)", default_brand)
model = st.sidebar.text_input("Model (do bazy)", default_model)

col1, col2 = st.sidebar.columns(2)
year_from = col1.number_input("Rocznik Od", 1990, 2026, 2022)
year_to = col2.number_input("Rocznik Do", 1990, 2026, 2022)

st.sidebar.markdown("---")
col3, col4 = st.sidebar.columns(2)
engine_capacity_from = col3.number_input("Silnik Od (cm3)", 0, 10000, 0, step=100)
engine_capacity_to = col4.number_input("Silnik Do (cm3)", 0, 10000, 0, step=100)
accident_filter = st.sidebar.selectbox("Stan uszkodzeń", ["Dowolny", "Tylko bezwypadkowe", "Tylko uszkodzone"])

st.sidebar.markdown("---")
st.sidebar.subheader("🔗 Alternatywa: Gotowy link")
st.sidebar.caption("Jeśli masz specyficzne filtry, wklej tu pełny link z przeglądarki.")
custom_url = st.sidebar.text_input("Wklej gotowy link z Otomoto:")

if st.sidebar.button("Pobierz / Odśwież dane z Otomoto", type="primary"):
    with st.spinner("Pobieram oferty metodą mikro-koszyków... To potrwa chwilę."):
        msg = scrape_and_update(
            category, brand, model, int(year_from), int(year_to), custom_url,
            engine_capacity_from=int(engine_capacity_from) if engine_capacity_from else None,
            engine_capacity_to=int(engine_capacity_to) if engine_capacity_to else None,
            accident_filter=accident_filter,
        )
    st.sidebar.success(msg)

st.sidebar.markdown("---")
if st.sidebar.button("🗑️ Wyczyść bazę danych (Reset)", type="secondary"):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS price_history")
        cursor.execute("DROP TABLE IF EXISTS offers")
        conn.commit()
        conn.close()
        init_db()
        st.sidebar.success("Baza została wyczyszczona!")
        st.rerun()
    except Exception:
        st.sidebar.error("Błąd podczas czyszczenia bazy.")


# Kraje traktowane jako import z USA (bez cła/akcyzy - wykluczane ze średniej)
USA_COUNTRY_LABELS = {"usa", "stany zjednoczone", "us", "united states", "stany zjednoczone ameryki"}


def is_usa_origin(country):
    if pd.isna(country):
        return False
    return str(country).strip().lower() in USA_COUNTRY_LABELS


def format_price(x):
    return f"{int(x):,} PLN".replace(",", " ") if x and x > 0 else "Brak ceny"


def format_mileage(x):
    return f"{int(x):,} km".replace(",", " ") if x and x > 0 else "Brak danych"


def format_country(x):
    if pd.isna(x):
        return "Nieznany"
    return f"🇺🇸 {x} (wykluczone ze średniej)" if is_usa_origin(x) else x


def format_capacity(x):
    return f"{int(x)} cm3" if pd.notna(x) and x and x > 0 else "Brak danych"


def format_accident(x):
    if pd.isna(x) or not x:
        return "Brak danych"
    return x


def make_row_highlighter(numeric_price_series, country_series, threshold, price_col_name, country_col_name):
    """Zwraca funkcję do Styler.apply(axis=1):
    - koloruje cenę na zielono, jeśli jest min. 20% poniżej średniej (bez USA),
    - koloruje kolumnę 'Kraj pochodzenia' na czerwono/pomarańczowo dla ofert z USA,
      żeby było od razu widać, które oferty są wykluczone ze średniej i dlaczego."""
    def _highlight(row):
        styles = [''] * len(row)
        try:
            if is_usa_origin(country_series.loc[row.name]):
                pos = row.index.get_loc(country_col_name)
                styles[pos] = 'background-color: rgba(231, 76, 60, 0.25); color: #c0392b; font-weight: 600;'
        except Exception:
            pass
        if threshold and threshold > 0:
            try:
                price_val = numeric_price_series.loc[row.name]
                if price_val and 0 < price_val <= threshold:
                    pos = row.index.get_loc(price_col_name)
                    styles[pos] = 'background-color: rgba(46, 204, 113, 0.25); color: #1e8449; font-weight: 600;'
            except Exception:
                pass
        return styles
    return _highlight


# Odczyt danych z bazy
try:
    conn = get_connection()
    query = '''
        SELECT id, otomoto_id, production_year as Rocznik, title as Oferta,
               current_price as "Cena (PLN)", mileage_km as "Przebieg (km)",
               country_origin as "Kraj pochodzenia",
               engine_capacity as "Pojemność (cm3)",
               accident_free as "Bezwypadkowy",
               status as Status, publication_date as "Data publikacji",
               last_seen_at as "Ostatnia aktualizacja", url as Link
        FROM offers
        WHERE LOWER(brand) = LOWER(?)
          AND LOWER(model) = LOWER(?)
          AND production_year BETWEEN ? AND ?
        ORDER BY first_seen_at DESC, "Cena (PLN)" ASC
    '''
    df = pd.read_sql(query, conn, params=(brand.strip(), model.strip(), int(year_from), int(year_to)))

    if not df.empty:
        dynamic_statuses = []
        cursor = conn.cursor()
        for idx, row in df.iterrows():
            if row['Status'] == 'Sprzedane':
                dynamic_statuses.append('Sprzedane')
                continue
            cursor.execute("SELECT price FROM price_history WHERE offer_id = ? ORDER BY recorded_at ASC LIMIT 1", (row['id'],))
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
except Exception:
    df = pd.DataFrame()

if df.empty:
    st.info("Brak danych w bazie. Ustaw parametry po lewej stronie i kliknij pobieranie!")
else:
    years = sorted(df['Rocznik'].unique(), reverse=True)
    tabs = st.tabs([f"Rocznik {y}" for y in years] + ["🕒 Historia Ofert (Filtry i Sortowanie)"])

    for idx, year in enumerate(years):
        with tabs[idx]:
            active_year_raw = df[(df['Rocznik'] == year) & (df['Dynamic_Status'] != 'Sprzedane')]

            # Średnia liczona z pominięciem TYLKO potwierdzonych ofert z USA -
            # te nie zawierają polskiego podatku/akcyzy i zaniżałyby porównanie.
            eu_subset = active_year_raw[~active_year_raw['Kraj pochodzenia'].apply(is_usa_origin)]
            avg_price = eu_subset["Cena (PLN)"].mean() if not eu_subset.empty else 0
            avg_mileage = eu_subset["Przebieg (km)"].mean() if not eu_subset.empty else 0
            price_threshold = avg_price * 0.8 if avg_price else 0

            usa_excluded_count = int(active_year_raw['Kraj pochodzenia'].apply(is_usa_origin).sum())

            m1, m2 = st.columns(2)
            m1.metric("💰 Średnia Cena (bez USA)", f"{avg_price:,.0f} PLN".replace(",", " "))
            m2.metric("🛣️ Średni Przebieg (bez USA)", f"{avg_mileage:,.0f} km".replace(",", " "))
            if usa_excluded_count:
                st.caption(f"🇺🇸 {usa_excluded_count} ofert(y) z USA pominięto przy liczeniu średniej (nadal widoczne w tabeli poniżej).")

            st.divider()

            if active_year_raw.empty:
                st.info("Brak aktywnych ofert dla tego rocznika.")
            else:
                disp_df = active_year_raw[['otomoto_id', 'Oferta', 'Cena (PLN)', 'Przebieg (km)', 'Kraj pochodzenia', 'Pojemność (cm3)', 'Bezwypadkowy', 'Data publikacji', 'Ostatnia aktualizacja', 'Link']].copy()
                disp_df.rename(columns={'otomoto_id': 'ID Oferty'}, inplace=True)

                numeric_price = disp_df['Cena (PLN)'].copy()
                country_raw = disp_df['Kraj pochodzenia'].copy()

                disp_df['Cena (PLN)'] = disp_df['Cena (PLN)'].apply(format_price)
                disp_df['Przebieg (km)'] = disp_df['Przebieg (km)'].apply(format_mileage)
                disp_df['Kraj pochodzenia'] = disp_df['Kraj pochodzenia'].apply(format_country)
                disp_df['Pojemność (cm3)'] = disp_df['Pojemność (cm3)'].apply(format_capacity)
                disp_df['Bezwypadkowy'] = disp_df['Bezwypadkowy'].apply(format_accident)

                highlighter = make_row_highlighter(numeric_price, country_raw, price_threshold, 'Cena (PLN)', 'Kraj pochodzenia')
                styled = disp_df.style.apply(highlighter, axis=1)
                st.dataframe(styled, use_container_width=True)

    with tabs[-1]:
        st.subheader("Wszystkie oferty – Filtrowanie i Sortowanie")
        col_f1, col_f2 = st.columns(2)
        status_filter = col_f1.multiselect("Filtruj po statusie:", options=['Aktywne', 'Zmieniono cenę', 'Sprzedane'], default=['Aktywne', 'Zmieniono cenę'])
        search_query = col_f2.text_input("Szukaj w tytule oferty:", "")

        filtered_df = df[df['Dynamic_Status'].isin(status_filter)]
        if search_query:
            filtered_df = filtered_df[filtered_df['Oferta'].str.contains(search_query, case=False, na=False)]

        hist_disp = filtered_df[['otomoto_id', 'Rocznik', 'Oferta', 'Cena (PLN)', 'Przebieg (km)', 'Kraj pochodzenia', 'Pojemność (cm3)', 'Bezwypadkowy', 'Dynamic_Status', 'Data publikacji', 'Ostatnia aktualizacja', 'Link']].copy()
        hist_disp.rename(columns={'otomoto_id': 'ID Oferty', 'Dynamic_Status': 'Status'}, inplace=True)
        hist_country_raw = filtered_df['Kraj pochodzenia'].copy()

        hist_disp['Cena (PLN)'] = hist_disp['Cena (PLN)'].apply(format_price)
        hist_disp['Przebieg (km)'] = hist_disp['Przebieg (km)'].apply(format_mileage)
        hist_disp['Kraj pochodzenia'] = hist_disp['Kraj pochodzenia'].apply(format_country)
        hist_disp['Pojemność (cm3)'] = hist_disp['Pojemność (cm3)'].apply(format_capacity)
        hist_disp['Bezwypadkowy'] = hist_disp['Bezwypadkowy'].apply(format_accident)

        def highlight_all(row):
            status = row['Status']
            if status == 'Sprzedane':
                styles = ['background-color: rgba(255, 60, 60, 0.2); color: #ff6666;'] * len(row)
            elif status == 'Zmieniono cenę':
                styles = ['background-color: rgba(255, 204, 0, 0.2); color: #ffcc00;'] * len(row)
            else:
                styles = [''] * len(row)
            try:
                if is_usa_origin(hist_country_raw.loc[row.name]):
                    pos = row.index.get_loc('Kraj pochodzenia')
                    styles[pos] = 'background-color: rgba(231, 76, 60, 0.3); color: #c0392b; font-weight: 600;'
            except Exception:
                pass
            return styles

        styled_hist = hist_disp.style.apply(highlight_all, axis=1)
        st.dataframe(styled_hist, use_container_width=True)
