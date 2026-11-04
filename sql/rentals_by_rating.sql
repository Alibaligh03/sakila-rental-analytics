-- rentals_by_rating: Rentals and revenue per MPAA rating.
SELECT
    f.rating                            AS "Rating",
    COUNT(DISTINCT r.rental_id)         AS "Total Rentals",
    SUM(p.amount)           AS "Total Revenue ($)"
FROM rental         r
JOIN inventory      i   ON r.inventory_id   = i.inventory_id
JOIN film           f   ON i.film_id        = f.film_id
LEFT JOIN payment   p   ON r.rental_id      = p.rental_id
GROUP BY f.rating
ORDER BY "Total Rentals" DESC
