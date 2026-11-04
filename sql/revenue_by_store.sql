-- revenue_by_store: Revenue, payments and unique customers per store, with city and country.
-- A rental belongs to the store of the staff member who handled it. That is not always the store that owns the copy
-- (about half of the rentals in this data differ), so store totals depend on this definition.
SELECT
    'Store ' || s.store_id              AS "Store",
    ci.city                             AS "City",
    co.country                          AS "Country",
    SUM(p.amount)            AS "Total Revenue",
    COUNT(p.payment_id)                 AS "Number of Payments",
    COUNT(DISTINCT r.customer_id)       AS "Unique Customers"
FROM store      s
JOIN staff      st  ON s.store_id       = st.store_id
JOIN rental     r   ON st.staff_id      = r.staff_id
LEFT JOIN payment p ON r.rental_id      = p.rental_id
JOIN address    a   ON s.address_id     = a.address_id
JOIN city       ci  ON a.city_id        = ci.city_id
JOIN country    co  ON ci.country_id    = co.country_id
GROUP BY s.store_id
ORDER BY s.store_id
