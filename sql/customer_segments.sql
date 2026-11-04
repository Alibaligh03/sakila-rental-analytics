-- customer_segments: Customers grouped into segments by rental volume, with their average spend and rentals.
-- Rentals and spend are computed per customer in separate correlated subqueries. Joining both rentals and payments
-- onto the customer in one query would repeat every payment once per rental and inflate the spend about 18 times.
SELECT
    CASE
        WHEN rental_count <= 20 THEN '12-20 Rentals'
        WHEN rental_count <= 30 THEN '21-30 Rentals'
        WHEN rental_count <= 40 THEN '31-40 Rentals'
        ELSE                         '41+ Rentals'
    END                                 AS "Customer Segment",
    COUNT(*)                            AS "Customers",
    AVG(total_spent)                    AS "Avg Total Spend",
    AVG(rental_count)                   AS "Avg Rentals"
FROM (
    SELECT
        c.customer_id,
        (SELECT COUNT(*) FROM rental r WHERE r.customer_id = c.customer_id)                  AS rental_count,
        (SELECT COALESCE(SUM(p.amount), 0) FROM payment p WHERE p.customer_id = c.customer_id) AS total_spent
    FROM customer c
)
GROUP BY "Customer Segment"
ORDER BY MIN(rental_count)
