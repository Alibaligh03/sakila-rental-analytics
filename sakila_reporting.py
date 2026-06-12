from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError
import pandas as pd
import numpy as np
import sqlite3 as sql
import time
import os
import sys
import atexit
from concurrent.futures import ThreadPoolExecutor, as_completed

script_start = datetime.now()
print(f"🚀 Script started at: {script_start.strftime('%Y-%m-%d %H:%M:%S')}")

# ───────────────────────────────────────────────────────────────────────
creds_path  = "your-service-account-key.json"
path_to_db  = "sqlite-sakila.db"
sheet_id    = "18LUaEHCHd-Qk94PBTIhGY_WW1EEm_OJbuLIBshSj6_g"
# ───────────────────────────────────────────────────────────────────────────────

if not os.path.exists(creds_path):
    print(f" ERROR: The file '{creds_path}' was not found.")
    print("Please make sure the JSON file is in the same folder as this script.")
    sys.exit(1)


# ──────────────────────────────────────────────────────────────────────
query_executive_summary = """
SELECT
    SUM(amount)                                                      AS "Total Revenue",
    COUNT(payment_id)                                                AS "Total Payments",
    AVG(amount)                                                      AS "Avg Payment"
FROM payment
"""

query_total_rentals = """
SELECT COUNT(rental_id) AS "Total Rentals" FROM rental
"""

query_total_customers = """
SELECT
    COUNT(customer_id)                                              AS "Total Customers",
    COUNT(CASE WHEN active = '1' THEN 1 END)                       AS "Active Customers"
FROM customer
"""

query_total_films = """
SELECT COUNT(film_id) AS "Total Films" FROM film
"""

query_unreturned = """
SELECT COUNT(rental_id) AS "Currently Unreturned"
FROM rental
WHERE return_date IS NULL
"""

query_monthly_revenue = """
SELECT
    strftime('%Y-%m', p.payment_date)   AS "Month",
    SUM(p.amount)                        AS "Revenue",
    COUNT(p.payment_id)                  AS "Number of Payments",
    COUNT(DISTINCT p.customer_id)        AS "Finically Active Customers",
    COUNT(DISTINCT i.film_id)            AS "Unique Films Rented",
    COUNT(CASE WHEN r.return_date IS NULL THEN 1 END) AS "Currently Unreturned"
FROM payment p
JOIN rental    r ON p.rental_id      = r.rental_id
JOIN inventory i ON r.inventory_id   = i.inventory_id
GROUP BY strftime('%Y-%m', p.payment_date)
ORDER BY "Month" DESC
"""

query_rentals_by_dow = """
SELECT
    CASE strftime('%w', rental_date)
        WHEN '0' THEN 'Sunday'
        WHEN '1' THEN 'Monday'
        WHEN '2' THEN 'Tuesday'
        WHEN '3' THEN 'Wednesday'
        WHEN '4' THEN 'Thursday'
        WHEN '5' THEN 'Friday'
        WHEN '6' THEN 'Saturday'
    END                                 AS "Day of Week",
    COUNT(rental_id)                    AS "Total Rentals"
FROM rental
GROUP BY strftime('%w', rental_date)
ORDER BY strftime('%w', rental_date)
"""

query_late_returns = """
SELECT
    COUNT(*)                            AS "Late Returns",
    AVG(
        julianday(r.return_date) - julianday(r.rental_date) - f.rental_duration
    )                              AS "Avg Days Overdue"
FROM rental     r
JOIN inventory  i   ON r.inventory_id   = i.inventory_id
JOIN film       f   ON i.film_id        = f.film_id
WHERE r.return_date IS NOT NULL
  AND (julianday(r.return_date) - julianday(r.rental_date)) > f.rental_duration
"""

query_revenue_by_category = """
SELECT
    c.name                              AS "Category",
    SUM(p.amount)            AS "Total Revenue",
    COUNT(p.payment_id)                 AS "Number of Rentals"
FROM payment        p
JOIN rental         r   ON p.rental_id      = r.rental_id
JOIN inventory      i   ON r.inventory_id   = i.inventory_id
JOIN film_category  fc  ON i.film_id        = fc.film_id
JOIN category       c   ON fc.category_id   = c.category_id
GROUP BY c.name
ORDER BY "Total Revenue ($)" DESC
"""

query_revenue_by_store = """
SELECT
    'Store ' || s.store_id              AS "Store",
    ci.city                             AS "City",
    co.country                          AS "Country",
    SUM(p.amount)            AS "Total Revenue",
    COUNT(p.payment_id)                 AS "Number of Payments",
    COUNT(DISTINCT r.customer_id)       AS "Unique Customers"
FROM store      s
JOIN staff      st  ON s.store_id       = st.store_id
JOIN rental     r   ON st.staff_id      = r.staff_id
LEFT JOIN payment p ON r.rental_id      = p.rental_id
JOIN address    a   ON s.address_id     = a.address_id
JOIN city       ci  ON a.city_id        = ci.city_id
JOIN country    co  ON ci.country_id    = co.country_id
GROUP BY s.store_id
ORDER BY s.store_id
"""

query_store_comparison = """
SELECT
    'Store ' || s.store_id              AS "Store",
    SUM(p.amount)            AS "Total Revenue",
    COUNT(p.payment_id)                 AS "Number of Payments",
    AVG(p.amount)            AS "Avg Payment",
    COUNT(r.rental_id)                  AS "Total Rentals",
    COUNT(DISTINCT r.customer_id)       AS "Unique Customers"
FROM store      s
JOIN staff      st  ON s.store_id       = st.store_id
JOIN rental     r   ON st.staff_id      = r.staff_id
LEFT JOIN payment p ON r.rental_id      = p.rental_id
GROUP BY s.store_id
ORDER BY s.store_id
"""

query_rentals_by_rating = """
SELECT
    f.rating                            AS "Rating",
    COUNT(r.rental_id)                  AS "Total Rentals",
    SUM(p.amount)           AS "Total Revenue ($)"
FROM rental         r
JOIN inventory      i   ON r.inventory_id   = i.inventory_id
JOIN film           f   ON i.film_id        = f.film_id
LEFT JOIN payment   p   ON r.rental_id      = p.rental_id
GROUP BY f.rating
ORDER BY "Total Rentals" DESC
"""

query_revenue_by_month_category = """
SELECT
    strftime('%Y-%m', p.payment_date)   AS "Month",
    c.name                              AS "Category",
    SUM(p.amount)          AS "Revenue"
FROM payment        p
JOIN rental         r   ON p.rental_id      = r.rental_id
JOIN inventory      i   ON r.inventory_id   = i.inventory_id
JOIN film_category  fc  ON i.film_id        = fc.film_id
JOIN category       c   ON fc.category_id   = c.category_id
GROUP BY strftime('%Y-%m', p.payment_date), c.name
ORDER BY "Month" DESC, "Revenue ($)" DESC
"""


query_top_20_films = """
SELECT
    f.title                             AS "Film Title",
    c.name                              AS "Category",
    f.rating                            AS "Rating",
    COUNT(r.rental_id)                  AS "Total Rentals",
    SUM(p.amount)          AS "Total Revenue"
FROM rental         r
JOIN inventory      i   ON r.inventory_id   = i.inventory_id
JOIN film           f   ON i.film_id        = f.film_id
JOIN film_category  fc  ON f.film_id        = fc.film_id
JOIN category       c   ON fc.category_id   = c.category_id
LEFT JOIN payment   p   ON r.rental_id      = p.rental_id
GROUP BY f.film_id, f.title
ORDER BY "Total Rentals" DESC
LIMIT 20
"""

query_dead_inventory = """
SELECT
    f.title                             AS "Film Title",
    f.rating                            AS "Rating",
    f.rental_rate                       AS "Rental Rate",
    f.replacement_cost                  AS "Replacement Cost"
FROM film           f
LEFT JOIN inventory i   ON f.film_id        = i.film_id
LEFT JOIN rental    r   ON i.inventory_id   = r.inventory_id
GROUP BY f.film_id, f.title
HAVING COUNT(r.rental_id) = 0
ORDER BY f.title
"""

query_avg_duration_by_category = """
SELECT
    c.name                                                                          AS "Category",
    AVG(julianday(r.return_date) - julianday(r.rental_date))           AS "Avg Days Kept",
    f_avg.avg_allowed_days                                                          AS "Avg Allowed Days"
FROM rental         r
JOIN inventory      i       ON r.inventory_id   = i.inventory_id
JOIN film_category  fc      ON i.film_id        = fc.film_id
JOIN category       c       ON fc.category_id   = c.category_id
JOIN (
    SELECT
        fc2.category_id,
        AVG(f2.rental_duration)   AS avg_allowed_days
    FROM film           f2
    JOIN film_category  fc2 ON f2.film_id = fc2.film_id
    GROUP BY fc2.category_id
) f_avg ON fc.category_id = f_avg.category_id
WHERE r.return_date IS NOT NULL
GROUP BY c.name
ORDER BY "Avg Days Kept" DESC
"""

query_top_actors = """
SELECT
    a.first_name || ' ' || a.last_name  AS "Actor",
    COUNT(DISTINCT fa.film_id)          AS "Films in Catalogue",
    COUNT(r.rental_id)                  AS "Total Rentals Generated",
    SUM(p.amount)             AS "Total Revenue"
FROM actor          a
JOIN film_actor     fa  ON a.actor_id       = fa.actor_id
JOIN inventory      i   ON fa.film_id       = i.film_id
JOIN rental         r   ON i.inventory_id   = r.inventory_id
LEFT JOIN payment   p   ON r.rental_id      = p.rental_id
GROUP BY a.actor_id
ORDER BY "Total Rentals Generated" DESC
LIMIT 20
"""

query_film_length_distribution = """
SELECT
    CASE
        WHEN length < 60  THEN '< 60 min'
        WHEN length < 90  THEN '60 - 89 min'
        WHEN length < 120 THEN '90 - 119 min'
        WHEN length < 150 THEN '120 - 149 min'
        ELSE                   '150+ min'
    END                                 AS "Length Bucket",
    COUNT(*)                            AS "Number of Films",
    AVG(rental_rate)        AS "Avg Rental Rate",
    AVG(replacement_cost)   AS "Avg Replacement Cost"
FROM film
GROUP BY "Length Bucket"
ORDER BY MIN(length)
"""

query_top_customers_by_spend = """
SELECT
    c.customer_id                               AS "Customer ID",
    c.first_name || ' ' || c.last_name          AS "Customer Name",
    SUM(p.amount)                  AS "Total Spent",
    COUNT(p.payment_id)                         AS "Number of Payments"
FROM payment    p
JOIN customer   c   ON p.customer_id = c.customer_id
GROUP BY c.customer_id
ORDER BY "Total Spent ($)" DESC
LIMIT 20
"""

query_top_customers_by_rentals = """
SELECT
    c.customer_id                               AS "Customer ID",
    c.first_name || ' ' || c.last_name          AS "Customer Name",
    COUNT(r.rental_id)                          AS "Total Rentals"
FROM rental     r
JOIN customer   c   ON r.customer_id = c.customer_id
GROUP BY c.customer_id
ORDER BY "Total Rentals" DESC
LIMIT 20
"""

query_customers_by_country = """
SELECT
    co.country                          AS "Country",
    COUNT(c.customer_id)                AS "Number of Customers"
FROM customer   c
JOIN address    a   ON c.address_id     = a.address_id
JOIN city       ci  ON a.city_id        = ci.city_id
JOIN country    co  ON ci.country_id    = co.country_id
GROUP BY co.country
ORDER BY "Number of Customers" DESC
"""

query_customer_segments = """
SELECT
    CASE
        WHEN rental_count <= 20 THEN '12-20 Rentals'
        WHEN rental_count <= 30 THEN '21-30 Rentals'
        WHEN rental_count <= 40 THEN '31-40 Rentals'
        ELSE                         '41+ Rentals'
    END                                 AS "Customer Segment",
    COUNT(*)                            AS "Customers",
    AVG(total_spent)         AS "Avg Total Spend",
    AVG(rental_count)         AS "Avg Rentals"
FROM (
    SELECT
        c.customer_id,
        COUNT(DISTINCT r.rental_id)     AS rental_count,
        COALESCE(SUM(p.amount), 0)      AS total_spent
    FROM customer   c
    LEFT JOIN rental    r   ON c.customer_id = r.customer_id
    LEFT JOIN payment   p   ON c.customer_id = p.customer_id
    GROUP BY c.customer_id
)
GROUP BY "Customer Segment"
ORDER BY MIN(rental_count)
"""

query_top_cities_by_customers = """
SELECT
    ci.city                             AS "City",
    co.country                          AS "Country",
    COUNT(c.customer_id)                AS "Customers",
    SUM(p.amount)           AS "Total Revenue"
FROM customer   c
JOIN address    a   ON c.address_id     = a.address_id
JOIN city       ci  ON a.city_id        = ci.city_id
JOIN country    co  ON ci.country_id    = co.country_id
LEFT JOIN payment p ON c.customer_id   = p.customer_id
GROUP BY ci.city_id
ORDER BY "Customers" DESC
LIMIT 20
"""

query_staff_performance = """
SELECT
    st.first_name || ' ' || st.last_name    AS "Staff Member",
    'Store ' || s.store_id                  AS "Store",
    COUNT(r.rental_id)                      AS "Rentals Handled",
    SUM(p.amount)               AS "Revenue Collected",
    AVG(p.amount)           AS "Avg Transaction Value"
FROM staff      st
JOIN store      s   ON st.store_id      = s.store_id
JOIN rental     r   ON st.staff_id      = r.staff_id
LEFT JOIN payment p ON r.rental_id      = p.rental_id
GROUP BY st.staff_id
ORDER BY "Revenue Collected ($)" DESC
"""

query_inventory_utilization = """
SELECT
    f.title                             AS "Film Title",
    c.name                              AS "Category",
    COUNT(DISTINCT i.inventory_id)      AS "Copies in Stock",
    COUNT(r.rental_id)                  AS "Total Rentals",
        CAST(COUNT(r.rental_id) AS FLOAT) / COUNT(DISTINCT i.inventory_id)  AS "Rentals per Copy"
FROM film           f
JOIN inventory      i   ON f.film_id        = i.film_id
JOIN film_category  fc  ON f.film_id        = fc.film_id
JOIN category       c   ON fc.category_id   = c.category_id
LEFT JOIN rental    r   ON i.inventory_id   = r.inventory_id
GROUP BY f.film_id
ORDER BY "Rentals per Copy" DESC
LIMIT 20
"""

query_inventory_by_store = """
SELECT
    'Store ' || i.store_id              AS "Store",
    c.name                              AS "Category",
    COUNT(DISTINCT i.inventory_id)      AS "Total Copies",
    COUNT(DISTINCT i.film_id)           AS "Unique Titles",
    COUNT(r.rental_id)                  AS "Total Rentals"
FROM inventory      i
JOIN film_category  fc  ON i.film_id        = fc.film_id
JOIN category       c   ON fc.category_id   = c.category_id
LEFT JOIN rental    r   ON i.inventory_id   = r.inventory_id
GROUP BY i.store_id, c.name
ORDER BY i.store_id, "Total Rentals" DESC
"""

query_overdue_by_category = """
SELECT
    c.name                              AS "Category",
    COUNT(CASE WHEN julianday(r.return_date) - julianday(r.rental_date) > f.rental_duration THEN 1 END)
                                        AS "Late Returns",
    COUNT(r.rental_id)                  AS "Total Returns",
        1.0 * COUNT(CASE WHEN julianday(r.return_date) - julianday(r.rental_date) > f.rental_duration THEN 1 END)
        / COUNT(r.rental_id)  AS "Late Return Rate (%)"
FROM rental         r
JOIN inventory      i   ON r.inventory_id   = i.inventory_id
JOIN film           f   ON i.film_id        = f.film_id
JOIN film_category  fc  ON f.film_id        = fc.film_id
JOIN category       c   ON fc.category_id   = c.category_id
WHERE r.return_date IS NOT NULL
GROUP BY c.name
ORDER BY "Late Return Rate (%)" DESC
"""


# ────────────────────────────────────────────────────────────────────────

def fetch_dataframe(name, query):
    print(f"⏳ Started:  {name}")
    print("-" * 50)
    start_time = time.time()
    engine = sql.connect(path_to_db)
    try:
        df = pd.read_sql(query, engine)
    finally:
        engine.close()
    elapsed = time.time() - start_time
    print(f"✅ Finished: {name} ({len(df):,} rows in {elapsed:.1f}s)")
    print("-" * 50)
    return name, df


def get_worksheet_with_retry(spreadsheet, sheet_name, retries=10, delay=10):
    for attempt in range(retries):
        try:
            return spreadsheet.worksheet(sheet_name)
        except APIError as e:
            if attempt < retries - 1:
                print(f"API error on '{sheet_name}', retrying in {delay} seconds... (attempt {attempt + 1})")
                time.sleep(delay)
            else:
                raise e


queries = {
    "df_executive_summary":             query_executive_summary,
    "df_total_rentals":                 query_total_rentals,
    "df_total_customers":               query_total_customers,
    "df_total_films":                   query_total_films,
    "df_unreturned":                    query_unreturned,
    "df_monthly_revenue":               query_monthly_revenue,
    "df_rentals_by_dow":                query_rentals_by_dow,
    "df_late_returns":                  query_late_returns,
    "df_revenue_by_category":           query_revenue_by_category,
    "df_revenue_by_store":              query_revenue_by_store,
    "df_store_comparison":              query_store_comparison,
    "df_rentals_by_rating":             query_rentals_by_rating,
    "df_revenue_by_month_category":     query_revenue_by_month_category,
    "df_top_20_films":                  query_top_20_films,
    "df_dead_inventory":                query_dead_inventory,
    "df_avg_duration_by_category":      query_avg_duration_by_category,
    "df_top_actors":                    query_top_actors,
    "df_film_length_distribution":      query_film_length_distribution,
    "df_top_customers_by_spend":        query_top_customers_by_spend,
    "df_top_customers_by_rentals":      query_top_customers_by_rentals,
    "df_customers_by_country":          query_customers_by_country,
    "df_customer_segments":             query_customer_segments,
    "df_top_cities_by_customers":       query_top_cities_by_customers,
    "df_staff_performance":             query_staff_performance,
    "df_inventory_utilization":         query_inventory_utilization,
    "df_inventory_by_store":            query_inventory_by_store,
    "df_overdue_by_category":           query_overdue_by_category,
}

results = {}

with ThreadPoolExecutor(max_workers=6) as executor:
    futures = {
        executor.submit(fetch_dataframe, name, query): name
        for name, query in queries.items()
    }
    for future in as_completed(futures):
        try:
            name, df = future.result()
            results[name] = df
        except Exception as e:
            name = futures[future]
            results[name] = pd.DataFrame()
            print(f"❌ Failed:   {name} → {e}")
            print("-" * 50)

all_df_names = list(queries.keys())

for name in all_df_names:
    globals()[name] = results.get(name, pd.DataFrame())

print("✨ All dataframes initialized successfully.")


# ────────────────────────────────────────────────────────────────────────

for name in all_df_names:
    df = globals().get(name)
    if isinstance(df, pd.DataFrame):
        df = df.replace([np.inf, -np.inf], 0).fillna("")
        globals()[name] = df

print("🧹 All DataFrames cleaned and formatted!")


# ────────────────────────────────────────────────────────────────

scopes = ["https://www.googleapis.com/auth/spreadsheets"]
creds  = Credentials.from_service_account_file(creds_path, scopes=scopes)
client = gspread.authorize(creds)

for attempt in range(3):
    try:
        spreadsheet = client.open_by_key(sheet_id)
        break
    except Exception as e:
        if attempt < 2:
            print(f"Attempt {attempt+1} failed, retrying...")
            time.sleep(5)
        else:
            raise e


def get_val(val):
    """Convert a cell value to a JSON-serializable Python native type."""
    if isinstance(val, pd.Timestamp):
        return val.strftime('%d/%m/%Y')
    try:
        return val.item()
    except AttributeError:
        return val


def get_df(name):
    return globals().get(name, pd.DataFrame())


def df_to_values(df):
    """Convert a DataFrame to a JSON-serializable list of rows (headers + data)."""
    def sanitize(val):
        if isinstance(val, pd.Timestamp):
            return val.strftime('%d/%m/%Y')
        try:
            return val.item()
        except AttributeError:
            return val
    rows = [[sanitize(v) for v in row] for row in df.values.tolist()]
    return [df.columns.values.tolist()] + rows


# ── SHEET 1: Executive Summary ─────────────────────────────────────────────────

sheet1 = get_worksheet_with_retry(spreadsheet, "Executive Summary")

updates = []

if not get_df("df_executive_summary").empty:
    updates.append({'range': 'C4',  'values': [[get_val(get_df("df_executive_summary").iloc[0, 0])]]})  # Total Revenue
    updates.append({'range': 'C5',  'values': [[get_val(get_df("df_executive_summary").iloc[0, 1])]]})  # Total Payments
    updates.append({'range': 'C6',  'values': [[get_val(get_df("df_executive_summary").iloc[0, 2])]]})  # Avg Payment

if not get_df("df_total_rentals").empty:
    updates.append({'range': 'C7',  'values': [[get_val(get_df("df_total_rentals").iloc[0, 0])]]})      # Total Rentals
if not get_df("df_total_customers").empty:
    updates.append({'range': 'C8',  'values': [[get_val(get_df("df_total_customers").iloc[0, 0])]]})    # Total Customers
    updates.append({'range': 'C9',  'values': [[get_val(get_df("df_total_customers").iloc[0, 1])]]})    # Active Customers
if not get_df("df_total_films").empty:
    updates.append({'range': 'C10', 'values': [[get_val(get_df("df_total_films").iloc[0, 0])]]})        # Total Films
if not get_df("df_unreturned").empty:
    updates.append({'range': 'C11', 'values': [[get_val(get_df("df_unreturned").iloc[0, 0])]]})         # Currently Unreturned

updates.append({'range': 'C12', 'values': [[datetime.now().strftime('%Y-%m-%d %H:%M:%S')]]})           # Last Updated

if not get_df("df_monthly_revenue").empty:
    months        = get_df("df_monthly_revenue")["Month"].tolist()
    revenue       = get_df("df_monthly_revenue")["Revenue"].tolist()
    payments      = get_df("df_monthly_revenue")["Number of Payments"].tolist()
    active_cust   = get_df("df_monthly_revenue")["Finically Active Customers"].tolist()
    films_rented  = get_df("df_monthly_revenue")["Unique Films Rented"].tolist()
    unreturned    = get_df("df_monthly_revenue")["Currently Unreturned"].tolist()

    final_values = [
        ["Month"]                  + months,
        ["Revenue"]                + revenue,
        ["Number of Payments"]     + payments,
        ["Finically Active Customers"]       + active_cust,
        ["Unique Films Rented"]    + films_rented,
        ["Currently Unreturned"]   + unreturned,
    ]
    updates.append({'range': 'B16', 'values': final_values})

if not get_df("df_rentals_by_dow").empty:
    updates.append({'range': 'B25', 'values': df_to_values(get_df("df_rentals_by_dow"))})

if not get_df("df_late_returns").empty:
    updates.append({'range': 'C37', 'values': [[get_val(get_df("df_late_returns").iloc[0, 0])]]})   # Late Returns count
    updates.append({'range': 'C38', 'values': [[get_val(get_df("df_late_returns").iloc[0, 1])]]})   # Avg Days Overdue

if updates:
    sheet1.batch_update(updates, value_input_option='USER_ENTERED')
    print("✅ sheet1 (Executive Summary) updated successfully")
else:
    print("⚠️ No data to update for sheet1")


# ── SHEET 2: Revenue & Finance ─────────────────────────────────────────────────

sheet2 = get_worksheet_with_retry(spreadsheet, "Revenue & Finance")

updates = []

if not get_df("df_revenue_by_category").empty:
    updates.append({'range': 'A1', 'values': [["Revenue by Category"]]})
    updates.append({'range': 'B3', 'values': df_to_values(get_df("df_revenue_by_category"))})

if not get_df("df_rentals_by_rating").empty:
    updates.append({'range': 'A21',     'values': [["Rentals & Revenue by Rating"]]})
    updates.append({'range': 'B23', 'values': df_to_values(get_df("df_rentals_by_rating"))})

if not get_df("df_revenue_by_store").empty:
    updates.append({'range': 'F1', 'values': [["Revenue by Store"]]})
    updates.append({'range': 'F3', 'values': df_to_values(get_df("df_revenue_by_store"))})


if not get_df("df_store_comparison").empty:
    updates.append({'range': 'F7',     'values': [["Store Comparison"]]})
    updates.append({'range': 'F9', 'values': df_to_values(get_df("df_store_comparison"))})

if not get_df("df_revenue_by_month_category").empty:
    updates.append({'range': 'M1', 'values': [["Revenue by Month & Category"]]})
    updates.append({'range': 'M3', 'values': df_to_values(get_df("df_revenue_by_month_category"))})

if updates:
    sheet2.batch_update(updates, value_input_option='USER_ENTERED')
    print("✅ sheet2 (Revenue & Finance) updated successfully")
else:
    print("⚠️ No data to update for sheet2")


# ── SHEET 3: Film Catalogue ─────────────────────────────────────────────────────

sheet3 = get_worksheet_with_retry(spreadsheet, "Film Catalogue")

updates = []

if not get_df("df_top_20_films").empty:
    updates.append({'range': 'A1', 'values': [["Top 20 Films by Rentals"]]})
    updates.append({'range': 'B3', 'values': df_to_values(get_df("df_top_20_films"))})


if not get_df("df_film_length_distribution").empty:
    updates.append({'range': 'A26', 'values': [["Film Length Distribution"]]})
    updates.append({'range': 'B28', 'values': df_to_values(get_df("df_film_length_distribution"))})

if not get_df("df_avg_duration_by_category").empty:
    updates.append({'range': 'A35',  'values': [["Avg Rental Duration by Category"]]})
    updates.append({'range': 'B37', 'values': df_to_values(get_df("df_avg_duration_by_category"))})

if not get_df("df_top_actors").empty:
    updates.append({'range': 'H1', 'values': [["Top 20 Actors by Rentals"]]})
    updates.append({'range': 'H3', 'values': df_to_values(get_df("df_top_actors"))})

if not get_df("df_dead_inventory").empty:
    updates.append({'range': 'M1', 'values': [["Dead Inventory (Never Rented)"]]})
    updates.append({'range': 'M3', 'values': df_to_values(get_df("df_dead_inventory"))})

if updates:
    sheet3.batch_update(updates, value_input_option='USER_ENTERED')
    print("✅ sheet3 (Film Catalogue) updated successfully")
else:
    print("⚠️ No data to update for sheet3")


# ── SHEET 4: Customers & Geography ─────────────────────────────────────────────

sheet4 = get_worksheet_with_retry(spreadsheet, "Customers & Geography")

updates = []

if not get_df("df_top_customers_by_spend").empty:
    updates.append({'range': 'A1', 'values': [["Top 20 Customers by Spend"]]})
    updates.append({'range': 'B3', 'values': df_to_values(get_df("df_top_customers_by_spend"))})

if not get_df("df_top_customers_by_rentals").empty:
    updates.append({'range': 'G1', 'values': [["Top 20 Customers by Rentals"]]})
    updates.append({'range': 'G3', 'values': df_to_values(get_df("df_top_customers_by_rentals"))})

if not get_df("df_customer_segments").empty:
    updates.append({'range': 'K1', 'values': [["Customer Segments by Rental Volume"]]})
    updates.append({'range': 'K3', 'values': df_to_values(get_df("df_customer_segments"))})

if not get_df("df_customers_by_country").empty:
    updates.append({'range': 'P1', 'values': [["Customers by Country"]]})
    updates.append({'range': 'P3', 'values': df_to_values(get_df("df_customers_by_country"))})

if not get_df("df_top_cities_by_customers").empty:
    updates.append({'range': 'T1', 'values': [["Top 20 Cities by Customers"]]})
    updates.append({'range': 'T3', 'values': df_to_values(get_df("df_top_cities_by_customers"))})
if updates:
    sheet4.batch_update(updates, value_input_option='USER_ENTERED')
    print("✅ sheet4 (Customers & Geography) updated successfully")
else:
    print("⚠️ No data to update for sheet4")


# ── SHEET 5: Operations ─────────────────────────────────────────────────────────

sheet5 = get_worksheet_with_retry(spreadsheet, "Operations")

updates = []

if not get_df("df_staff_performance").empty:
    updates.append({'range': 'A1', 'values': [["Staff Performance"]]})
    updates.append({'range': 'B3', 'values': df_to_values(get_df("df_staff_performance"))})


if not get_df("df_overdue_by_category").empty:
    updates.append({'range': 'A8',  'values': [["Late Return Rate by Category"]]})
    updates.append({'range': 'B10', 'values': df_to_values(get_df("df_overdue_by_category"))})

if not get_df("df_inventory_utilization").empty:
    updates.append({'range': 'H1', 'values': [["Top 20 Films by Rentals per Copy"]]})
    updates.append({'range': 'H3', 'values': df_to_values(get_df("df_inventory_utilization"))})

if not get_df("df_inventory_by_store").empty:
    updates.append({'range': 'N1', 'values': [["Inventory by Store & Category"]]})
    updates.append({'range': 'N3', 'values': df_to_values(get_df("df_inventory_by_store"))})

if updates:
    sheet5.batch_update(updates, value_input_option='USER_ENTERED')
    print("✅ sheet5 (Operations) updated successfully")
else:
    print("⚠️ No data to update for sheet5")


# ─────────────────────────────────────────────────────────────────────────

script_end = datetime.now()
elapsed    = script_end - script_start
print(f"✅ Script finished at: {script_end.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"⏱️ Total runtime: {str(elapsed).split('.')[0]}")