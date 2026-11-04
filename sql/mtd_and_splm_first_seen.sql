-- mtd_and_splm_first_seen: Customers whose first-ever rental fell in each month, at the home store or cross-store (five months).
-- Rows: five months. :day_cutoff keeps only days before today's day-of-month in every month (like-for-like);
-- in first-of-month mode it is 32 and the months are whole.
WITH first_rental AS (                  -- each customer's first-ever rental, windowed on that day
    SELECT r.customer_id, date(r.rental_date) AS day, st.store_id AS store_id, c.store_id AS home_store_id
    FROM rental r
    JOIN staff st    ON st.staff_id   = r.staff_id
    JOIN customer c  ON c.customer_id = r.customer_id
    WHERE r.rental_id = (SELECT x.rental_id FROM rental x WHERE x.customer_id = r.customer_id ORDER BY x.rental_date, x.rental_id LIMIT 1)
      AND date(r.rental_date) >= :m5_start AND date(r.rental_date) < :win_end AND CAST(strftime('%d', date(r.rental_date)) AS INTEGER) < :day_cutoff
)
SELECT
    strftime('%Y-%m-01', day)                                                     AS "Month",
    COUNT(DISTINCT CASE WHEN store_id =  home_store_id THEN customer_id END) AS "New Customers At Home Store",
    COUNT(DISTINCT CASE WHEN store_id <> home_store_id THEN customer_id END)      AS "New Customers Cross-Store"
FROM first_rental
GROUP BY strftime('%Y-%m-01', day)
ORDER BY strftime('%Y-%m-01', day) DESC
