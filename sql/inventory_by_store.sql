-- inventory_by_store: Inventory copies per store and category.
SELECT
    'Store ' || i.store_id              AS "Store",
    c.name                              AS "Category",
    COUNT(DISTINCT i.inventory_id)      AS "Total Copies",
    COUNT(DISTINCT i.film_id)           AS "Unique Titles",
    COUNT(r.rental_id)                  AS "Total Rentals"
FROM inventory      i
JOIN film_category  fc  ON i.film_id        = fc.film_id
JOIN category       c   ON fc.category_id   = c.category_id
LEFT JOIN rental    r   ON i.inventory_id   = r.inventory_id
GROUP BY i.store_id, c.name
ORDER BY i.store_id, "Total Rentals" DESC
