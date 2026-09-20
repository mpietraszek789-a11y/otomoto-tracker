import streamlit as st
import pandas as pd
from scraper import get_connection, init_db, scrape_and_update

st.set_page_config(page_title="Motocyklowy kolektor cen rynkowych", layout="wide")

try:
    init_db()
except Exception:
    pass

LOGO_BASE64 = "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"

col_logo, col_title = st.columns([1, 8])
with col_logo:
    st.markdown(
        f"<img src='data:image/png;base64,{LOGO_BASE64}' width='70'>",
        unsafe_allow_html=True
    )
with col_title:
    st.title("🏍️ Motocyklowy kolektor cen rynkowych")  # <- wstaw tu SWÓJ dokładny tytuł

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
