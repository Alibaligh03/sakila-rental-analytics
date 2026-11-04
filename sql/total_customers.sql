-- total_customers: Total and active customer counts.
SELECT
    COUNT(customer_id)                                              AS "Total Customers",
    COUNT(CASE WHEN CAST(active AS INTEGER) = 1 THEN 1 END)                       AS "Active Customers"
FROM customer
