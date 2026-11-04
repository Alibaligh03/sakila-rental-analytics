-- top_customers_by_rentals: Top 20 customers by number of rentals.
SELECT
    c.customer_id                               AS "Customer ID",
    c.first_name || ' ' || c.last_name          AS "Customer Name",
    COUNT(r.rental_id)                          AS "Total Rentals"
FROM rental     r
JOIN customer   c   ON r.customer_id = c.customer_id
GROUP BY c.customer_id
ORDER BY "Total Rentals" DESC, c.customer_id
LIMIT 20
