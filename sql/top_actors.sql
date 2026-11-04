-- top_actors: Top 20 actors by rentals generated, with the number of films they appear in and the revenue.
-- "Films in Catalogue" counts every film the actor is credited in. It comes from its own subquery because the joins
-- below (inventory, rental) only reach films that were stocked and rented, which would understate it.
SELECT
    a.first_name || ' ' || a.last_name  AS "Actor",
    (SELECT COUNT(*) FROM film_actor x WHERE x.actor_id = a.actor_id)  AS "Films in Catalogue",
    COUNT(DISTINCT r.rental_id)         AS "Total Rentals Generated",
    SUM(p.amount)                       AS "Total Revenue"
FROM actor          a
JOIN film_actor     fa  ON a.actor_id       = fa.actor_id
JOIN inventory      i   ON fa.film_id       = i.film_id
JOIN rental         r   ON i.inventory_id   = r.inventory_id
LEFT JOIN payment   p   ON r.rental_id      = p.rental_id
GROUP BY a.actor_id
ORDER BY "Total Rentals Generated" DESC, "Total Revenue" DESC, "Actor"
LIMIT 20
