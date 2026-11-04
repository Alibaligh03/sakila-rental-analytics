-- mtd_and_splm: Month-to-date against the same period in earlier months: the metrics block (five months).
-- Rows: five months. :day_cutoff keeps only days before today's day-of-month in every month (like-for-like);
-- in first-of-month mode it is 32 and the months are whole.
WITH base AS (                          -- one row per rental
    SELECT r.rental_id, r.customer_id, date(r.rental_date) AS day, r.rental_date, r.return_date,
           st.store_id AS store_id, c.store_id AS home_store_id,
           i.film_id, fc.category_id, f.rental_duration, f.rating,
           COALESCE(p.n_pay, 0) AS n_pay, COALESCE(p.amount, 0) AS amount, p.max_amount,
           julianday(r.return_date) - julianday(r.rental_date) AS kept_days,
           CASE WHEN r.return_date IS NULL THEN NULL
                WHEN julianday(r.return_date) - julianday(r.rental_date) > f.rental_duration THEN 1 ELSE 0 END AS late
    FROM rental r
    JOIN staff st          ON st.staff_id     = r.staff_id
    JOIN customer c        ON c.customer_id   = r.customer_id
    JOIN inventory i       ON i.inventory_id  = r.inventory_id
    JOIN film f            ON f.film_id       = i.film_id
    JOIN film_category fc  ON fc.film_id      = f.film_id
    LEFT JOIN (
        SELECT rental_id, COUNT(*) AS n_pay, SUM(amount) AS amount, MAX(amount) AS max_amount
        FROM payment WHERE rental_id IS NOT NULL GROUP BY rental_id
    ) p ON p.rental_id = r.rental_id
    WHERE date(r.rental_date) >= :m5_start AND date(r.rental_date) < :win_end AND CAST(strftime('%d', date(r.rental_date)) AS INTEGER) < :day_cutoff
)
SELECT
    strftime('%Y-%m-01', day)                                                     AS "Month",
    COUNT(*)                                                    AS "Rentals",
    COUNT(DISTINCT customer_id)                                 AS "Active Customers",
    SUM(n_pay)                                                  AS "Payments",
    SUM(amount)                                                 AS "Revenue",
    SUM(amount) * 1.0 / NULLIF(SUM(n_pay), 0)                   AS "Avg Payment",
    COUNT(DISTINCT film_id)                                     AS "Unique Films Rented",
    COUNT(DISTINCT category_id)                                 AS "Unique Categories Rented",
    COUNT(return_date)                                          AS "Returned Rentals",
    SUM(late)                                                   AS "Late Returns",
    SUM(late) * 1.0 / NULLIF(COUNT(return_date), 0)             AS "Late Return Rate",
    COUNT(*) * 1.0 / COUNT(DISTINCT customer_id)                AS "Rentals per Customer",
    SUM(amount) * 1.0 / COUNT(DISTINCT customer_id)             AS "Revenue per Customer",
    AVG(kept_days)                                              AS "Avg Days Kept",
    COUNT(*) - COUNT(return_date)                               AS "Unreturned Rentals"
FROM base
GROUP BY strftime('%Y-%m-01', day)
ORDER BY strftime('%Y-%m-01', day) DESC
