# 🎬 Sakila Video Rental — Automated Analytics Pipeline

An end-to-end data pipeline that queries a SQLite database with **27 analytical SQL queries**, processes the results with pandas, and pushes a fully formatted report to a **live Google Sheets dashboard** — automatically.

---

## 📊 Dashboard Sheets

| Sheet | What it covers |
|---|---|
| **Executive Summary** | KPIs, monthly revenue trend, rentals by day of week, late returns |
| **Revenue & Finance** | Revenue by category, store, rating, and month × category breakdown |
| **Film Catalogue** | Top 20 films, dead inventory, actor performance, rental duration by category |
| **Customers & Geography** | Top customers by spend & rentals, segments, country & city breakdowns |
| **Operations** | Staff performance, inventory utilisation, overdue rates by category |

> 📎 **[View the live dashboard on Google Sheets](https://docs.google.com/spreadsheets/d/18LUaEHCHd-Qk94PBTIhGY_WW1EEm_OJbuLIBshSj6_g/edit?gid=0#gid=0)**

---

## 🛠️ Tech Stack

| Tool | Purpose |
|---|---|
| **Python 3** | Core scripting language |
| **SQLite** | Local relational database (Sakila dataset) |
| **SQL** | 27 analytical queries — joins, aggregations, CASE logic, subqueries |
| **pandas / numpy** | Data cleaning and transformation |
| **gspread** | Google Sheets API wrapper |
| **google-auth** | Service account authentication |
| **ThreadPoolExecutor** | Concurrent query execution for faster runtime |

---

## 🗂️ Project Structure

```
sakila-rental-analytics/
├── sakila_reporting.py      # Main pipeline script
├── sqlite-sakila.db         # Sakila SQLite database
├── requirements.txt         # Python dependencies
└── .gitignore               # Excludes credentials and junk files
```

---

## ⚙️ How It Works

1. **Connects** to the local SQLite Sakila database
2. **Runs 27 SQL queries concurrently** using a thread pool (6 workers)
3. **Cleans** all DataFrames — handles nulls and infinities
4. **Authenticates** with Google Sheets via a service account
5. **Writes** all results to a pre-formatted Google Sheets dashboard using batch updates

---

## 🚀 Setup & Usage

### 1. Clone the repo
```bash
git clone https://github.com/YOUR_USERNAME/sakila-rental-analytics.git
cd sakila-rental-analytics
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Add your Google credentials
Place your Google service account JSON key in the project root and update the path at the top of `sakila_reporting.py`:
```python
creds_path = "your-service-account-key.json"
```
> ⚠️ Never commit your credentials file. It is excluded by `.gitignore`.

### 4. Google Sheet ID
The sheet ID is already hardcoded in `sakila_reporting.py`:
```python
sheet_id = "18LUaEHCHd-Qk94PBTIhGY_WW1EEm_OJbuLIBshSj6_g"
```
This points to the [live dashboard](https://docs.google.com/spreadsheets/d/18LUaEHCHd-Qk94PBTIhGY_WW1EEm_OJbuLIBshSj6_g/edit?gid=0#gid=0). To use your own sheet, replace the ID with yours.

### 5. Run the pipeline
```bash
python sakila_reporting.py
```

---

## 📁 Dataset

The **Sakila** database is a sample dataset originally created by MySQL, representing a DVD rental store. It includes films, actors, customers, inventory, rentals, payments, and store data across a normalised relational schema.

- 1,000 films across 16 categories
- 599 customers across multiple countries
- 16,044 rental transactions
- 2 store locations

---

## 💡 Key SQL Techniques Used

- Multi-table `JOIN`s across 6+ tables
- Aggregations with `GROUP BY`, `HAVING`
- `CASE WHEN` bucketing and conditional counts
- Correlated subqueries for per-category averages
- Date arithmetic with `julianday()` and `strftime()`
- `LEFT JOIN` for dead inventory detection (zero-rental films)
