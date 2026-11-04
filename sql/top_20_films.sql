-- top_20_films: Top 20 films by number of rentals, with revenue.
SELECT
    f.title                             AS "Film Title",
    c.name                              AS "Category",
    f.rating                            AS "Rating",
    COUNT(DISTINCT r.rental_id)         AS "Total Rentals",
    SUM(p.amount)          AS "Total Revenue"
FROM rental         r
JOIN inventory      i   ON r.inventory_id   = i.inventory_id
JOIN film           f   ON i.film_id        = f.film_id
JOIN film_category  fc  ON f.film_id        = fc.film_id
JOIN category       c   ON fc.category_id   = c.category_id
LEFT JOIN payment   p   ON r.rental_id      = p.rental_id
GROUP BY f.film_id, f.title
ORDER BY "Total Rentals" DESC, "Total Revenue" DESC, f.title
LIMIT 20
