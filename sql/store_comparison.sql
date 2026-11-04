-- store_comparison: Side-by-side store totals: revenue, payments, average payment, rentals, customers.
-- Stores are attributed by the staff member who handled the rental (see revenue_by_store).
SELECT
    'Store ' || s.store_id              AS "Store",
    SUM(p.amount)            AS "Total Revenue",
    COUNT(p.payment_id)                 AS "Number of Payments",
    AVG(p.amount)            AS "Avg Payment",
    COUNT(DISTINCT r.rental_id)         AS "Total Rentals",
    COUNT(DISTINCT r.customer_id)       AS "Unique Customers"
FROM store      s
JOIN staff      st  ON s.store_id       = st.store_id
JOIN rental     r   ON st.staff_id      = r.staff_id
LEFT JOIN payment p ON r.rental_id      = p.rental_id
GROUP BY s.store_id
ORDER BY s.store_id
