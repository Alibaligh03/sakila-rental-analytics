-- daily_stores_first_seen: Customers whose first-ever rental was on that day, split by whether it was at their home store, per store.
-- First-ever means over the whole history; only the output days are limited to the window.
WITH first_rental AS (                  -- each customer's first-ever rental, windowed on that day
    SELECT r.customer_id, date(r.rental_date) AS day, st.store_id AS store_id, c.store_id AS home_store_id
    FROM rental r
    JOIN staff st    ON st.staff_id   = r.staff_id
    JOIN customer c  ON c.customer_id = r.customer_id
    WHERE r.rental_id = (SELECT x.rental_id FROM rental x WHERE x.customer_id = r.customer_id ORDER BY x.rental_date, x.rental_id LIMIT 1)
      AND date(r.rental_date) >= date(:as_of, '-' || :lookback_days || ' days') AND date(r.rental_date) < date(:as_of)
)
SELECT
    day                                                         AS "Day",
    'Store ' || store_id       AS "Store",
    COUNT(DISTINCT CASE WHEN store_id =  home_store_id THEN customer_id END) AS "New Customers At Home Store",
    COUNT(DISTINCT CASE WHEN store_id <> home_store_id THEN customer_id END)      AS "New Customers Cross-Store"
FROM first_rental
GROUP BY day, store_id
ORDER BY day DESC, store_id
