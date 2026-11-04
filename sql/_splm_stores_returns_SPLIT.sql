-- _splm_stores_returns_SPLIT: Same period last month part of the per-store returns query (split mode); the two parts are merged after they run.
-- variant: split
WITH ret AS (                           -- one row per returned rental, windowed on the RETURN day
    SELECT date(r.return_date) AS day, st.store_id AS store_id,
           julianday(r.return_date) - julianday(r.rental_date) AS kept_days,
           CASE WHEN julianday(r.return_date) - julianday(r.rental_date) > f.rental_duration THEN 1 ELSE 0 END AS late
    FROM rental r
    JOIN staff st      ON st.staff_id    = r.staff_id
    JOIN inventory i   ON i.inventory_id = r.inventory_id
    JOIN film f        ON f.film_id      = i.film_id
    WHERE r.return_date IS NOT NULL AND date(r.return_date) >= :splm_start AND date(r.return_date) < :splm_end AND CAST(strftime('%d', date(r.return_date)) AS INTEGER) < :day_cutoff
)
SELECT strftime('%Y-%m-01', day) AS "Month", 'Store ' || store_id AS "Store", COUNT(*) AS "Returned", COUNT(*) - SUM(late) AS "Returned On Time", SUM(late) AS "Returned Late", AVG(kept_days) AS "Avg Days Kept"
FROM ret GROUP BY strftime('%Y-%m-01', day), store_id ORDER BY store_id
