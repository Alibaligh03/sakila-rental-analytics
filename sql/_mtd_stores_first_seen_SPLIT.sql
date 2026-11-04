-- _mtd_stores_first_seen_SPLIT: Month-to-date part of the per-store first query (split mode); the two parts are merged after they run.
-- variant: split
WITH first_rental AS (                  -- each customer's first-ever rental, windowed on that day
    SELECT r.customer_id, date(r.rental_date) AS day, st.store_id AS store_id, c.store_id AS home_store_id
    FROM rental r
    JOIN staff st    ON st.staff_id   = r.staff_id
    JOIN customer c  ON c.customer_id = r.customer_id
    WHERE r.rental_id = (SELECT x.rental_id FROM rental x WHERE x.customer_id = r.customer_id ORDER BY x.rental_date, x.rental_id LIMIT 1)
      AND date(r.rental_date) >= :mtd_start AND date(r.rental_date) < :mtd_end AND CAST(strftime('%d', date(r.rental_date)) AS INTEGER) < :day_cutoff
)
SELECT strftime('%Y-%m-01', day) AS "Month", 'Store ' || store_id AS "Store",
    COUNT(DISTINCT CASE WHEN store_id =  home_store_id THEN customer_id END) AS "New Customers At Home Store",
    COUNT(DISTINCT CASE WHEN store_id <> home_store_id THEN customer_id END) AS "New Customers Cross-Store"
FROM first_rental GROUP BY strftime('%Y-%m-01', day), store_id ORDER BY store_id
