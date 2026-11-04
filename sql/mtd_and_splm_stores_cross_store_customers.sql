-- mtd_and_splm_stores_cross_store_customers: Customers renting at a store other than their home store, per month (two months, per store).
-- variant: cte
-- Rows: two months, per store. :day_cutoff keeps only days before today's day-of-month in every month (like-for-like);
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
    WHERE date(r.rental_date) >= :m2_start AND date(r.rental_date) < :win_end AND CAST(strftime('%d', date(r.rental_date)) AS INTEGER) < :day_cutoff
)
SELECT
    strftime('%Y-%m-01', day)                                                     AS "Month",
    'Store ' || store_id       AS "Store",
    COUNT(DISTINCT CASE WHEN store_id <> home_store_id THEN customer_id END) AS "Cross-Store Customers"
FROM base
GROUP BY strftime('%Y-%m-01', day), store_id
ORDER BY strftime('%Y-%m-01', day) DESC, store_id
