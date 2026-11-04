-- revenue_by_month_category: Revenue per month and category.
SELECT
    strftime('%Y-%m', p.payment_date)   AS "Month",
    c.name                              AS "Category",
    SUM(p.amount)          AS "Revenue"
FROM payment        p
JOIN rental         r   ON p.rental_id      = r.rental_id
JOIN inventory      i   ON r.inventory_id   = i.inventory_id
JOIN film_category  fc  ON i.film_id        = fc.film_id
JOIN category       c   ON fc.category_id   = c.category_id
GROUP BY strftime('%Y-%m', p.payment_date), c.name
ORDER BY "Month" DESC, "Revenue" DESC
