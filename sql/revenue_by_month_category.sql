-- revenue_by_month_category: Revenue per month and category.
-- Leaves out the 2006-02-14 load batch (payments dated 2006-02), which is not a real month of activity.
SELECT
    strftime('%Y-%m', p.payment_date)   AS "Month",
    c.name                              AS "Category",
    SUM(p.amount)          AS "Revenue"
FROM payment        p
JOIN rental         r   ON p.rental_id      = r.rental_id
JOIN inventory      i   ON r.inventory_id   = i.inventory_id
JOIN film_category  fc  ON i.film_id        = fc.film_id
JOIN category       c   ON fc.category_id   = c.category_id
WHERE p.payment_date < '2006-01-01'
GROUP BY strftime('%Y-%m', p.payment_date), c.name
ORDER BY "Month" DESC, "Revenue" DESC
