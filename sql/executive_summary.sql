-- executive_summary: Total revenue, number of payments and average payment across the whole history.
SELECT
    SUM(amount)                                                      AS "Total Revenue",
    COUNT(payment_id)                                                AS "Total Payments",
    AVG(amount)                                                      AS "Avg Payment"
FROM payment
