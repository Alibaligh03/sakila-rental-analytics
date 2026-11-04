-- inventory_utilization: Top 20 films by rentals per inventory copy.
SELECT
    f.title                             AS "Film Title",
    c.name                              AS "Category",
    COUNT(DISTINCT i.inventory_id)      AS "Copies in Stock",
    COUNT(DISTINCT r.rental_id)         AS "Total Rentals",
        CAST(COUNT(DISTINCT r.rental_id) AS FLOAT) / COUNT(DISTINCT i.inventory_id)  AS "Rentals per Copy"
FROM film           f
JOIN inventory      i   ON f.film_id        = i.film_id
JOIN film_category  fc  ON f.film_id        = fc.film_id
JOIN category       c   ON fc.category_id   = c.category_id
LEFT JOIN rental    r   ON i.inventory_id   = r.inventory_id
GROUP BY f.film_id
ORDER BY "Rentals per Copy" DESC, "Total Rentals" DESC, f.title
LIMIT 20
