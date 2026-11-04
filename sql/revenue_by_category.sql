-- revenue_by_category: Revenue and number of rentals per film category.
SELECT
    c.name                              AS "Category",
    SUM(p.amount)            AS "Total Revenue",
    COUNT(p.payment_id)                 AS "Number of Rentals"
FROM payment        p
JOIN rental         r   ON p.rental_id      = r.rental_id
JOIN inventory      i   ON r.inventory_id   = i.inventory_id
JOIN film_category  fc  ON i.film_id        = fc.film_id
JOIN category       c   ON fc.category_id   = c.category_id
GROUP BY c.name
ORDER BY "Total Revenue" DESC
