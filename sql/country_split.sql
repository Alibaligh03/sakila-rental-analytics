-- country_split: Top five customer countries per month by active customers (like the other month blocks, month-to-date aware).
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
),
per AS (
    SELECT strftime('%Y-%m-01', b.day) AS month, co.country AS country, COUNT(DISTINCT b.customer_id) AS customers, COUNT(*) AS rentals
    FROM base b
    JOIN customer c  ON c.customer_id = b.customer_id
    JOIN address a   ON a.address_id  = c.address_id
    JOIN city ci     ON ci.city_id    = a.city_id
    JOIN country co  ON co.country_id = ci.country_id
    GROUP BY 1, 2
),
ranked AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY month ORDER BY customers DESC, rentals DESC, country) AS rk FROM per)
SELECT month AS "Month", country AS "Country", customers AS "Customers", rentals AS "Rentals"
FROM ranked WHERE rk <= 5
ORDER BY month DESC, rk
