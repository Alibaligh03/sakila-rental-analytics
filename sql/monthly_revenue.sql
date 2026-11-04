-- monthly_revenue: Revenue, payments, financially active customers, unique films rented and unreturned rentals per month.
-- Months are payment months. Only payments that join to a rental and an inventory item are counted, and
-- "Currently Unreturned" counts the paid rentals that have not been returned yet.
SELECT
    strftime('%Y-%m', p.payment_date)   AS "Month",
    SUM(p.amount)                        AS "Revenue",
    COUNT(p.payment_id)                  AS "Number of Payments",
    COUNT(DISTINCT p.customer_id)        AS "Financially Active Customers",
    COUNT(DISTINCT i.film_id)            AS "Unique Films Rented",
    COUNT(CASE WHEN r.return_date IS NULL THEN 1 END) AS "Currently Unreturned"
FROM payment p
JOIN rental    r ON p.rental_id      = r.rental_id
JOIN inventory i ON r.inventory_id   = i.inventory_id
GROUP BY strftime('%Y-%m', p.payment_date)
ORDER BY "Month" DESC
