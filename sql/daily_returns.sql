-- daily_returns: Rentals returned that day, on time and late.
-- Grouped by the day the rental was RETURNED. Late = kept longer than the film's rental_duration.
WITH ret AS (                           -- one row per returned rental, windowed on the RETURN day
    SELECT date(r.return_date) AS day, st.store_id AS store_id,
           julianday(r.return_date) - julianday(r.rental_date) AS kept_days,
           CASE WHEN julianday(r.return_date) - julianday(r.rental_date) > f.rental_duration THEN 1 ELSE 0 END AS late
    FROM rental r
    JOIN staff st      ON st.staff_id    = r.staff_id
    JOIN inventory i   ON i.inventory_id = r.inventory_id
    JOIN film f        ON f.film_id      = i.film_id
    WHERE r.return_date IS NOT NULL AND date(r.return_date) >= date(:as_of, '-' || :lookback_days || ' days') AND date(r.return_date) < date(:as_of)
)
SELECT
    day                                                         AS "Day",
    COUNT(*)                                               AS "Returned",
    COUNT(*) - SUM(late)                                        AS "Returned On Time",
    SUM(late)                                                   AS "Returned Late"
FROM ret
GROUP BY day
ORDER BY day DESC
