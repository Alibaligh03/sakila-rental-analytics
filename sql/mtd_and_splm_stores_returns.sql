-- mtd_and_splm_stores_returns: Rentals returned in each month: returned, on time, late, average days kept (two months, per store).
-- variant: cte
-- Rows: two months, per store. :day_cutoff keeps only days before today's day-of-month in every month (like-for-like);
-- in first-of-month mode it is 32 and the months are whole.
WITH ret AS (                           -- one row per returned rental, windowed on the RETURN day
    SELECT date(r.return_date) AS day, st.store_id AS store_id,
           julianday(r.return_date) - julianday(r.rental_date) AS kept_days,
           CASE WHEN julianday(r.return_date) - julianday(r.rental_date) > f.rental_duration THEN 1 ELSE 0 END AS late
    FROM rental r
    JOIN staff st      ON st.staff_id    = r.staff_id
    JOIN inventory i   ON i.inventory_id = r.inventory_id
    JOIN film f        ON f.film_id      = i.film_id
    WHERE r.return_date IS NOT NULL AND date(r.return_date) >= :m2_start AND date(r.return_date) < :win_end AND CAST(strftime('%d', date(r.return_date)) AS INTEGER) < :day_cutoff
)
SELECT
    strftime('%Y-%m-01', day)                                                     AS "Month",
    'Store ' || store_id       AS "Store",
    COUNT(*)                                               AS "Returned",
    COUNT(*) - SUM(late)                                        AS "Returned On Time",
    SUM(late)                                                   AS "Returned Late",
    AVG(kept_days)                                              AS "Avg Days Kept"
FROM ret
GROUP BY strftime('%Y-%m-01', day), store_id
ORDER BY strftime('%Y-%m-01', day) DESC, store_id
