-- top_customers_by_spend: Top 20 customers by total spend.
SELECT
    c.customer_id                               AS "Customer ID",
    c.first_name || ' ' || c.last_name          AS "Customer Name",
    SUM(p.amount)                  AS "Total Spent",
    COUNT(p.payment_id)                         AS "Number of Payments"
FROM payment    p
JOIN customer   c   ON p.customer_id = c.customer_id
GROUP BY c.customer_id
ORDER BY "Total Spent" DESC, c.customer_id
LIMIT 20
