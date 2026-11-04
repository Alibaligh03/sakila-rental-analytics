-- films_never_stocked: Films in the catalogue that have no inventory copy in any store, so they can never be rented.
-- This replaces the old "dead inventory (never rented)" list. Those 42 films were never rented because they were never
-- stocked, which is not the same as stocked and unrented: only 1 of 4,581 inventory copies has never been rented.
SELECT
    f.title                             AS "Film Title",
    f.rating                            AS "Rating",
    f.rental_rate                       AS "Rental Rate",
    f.replacement_cost                  AS "Replacement Cost"
FROM film f
WHERE NOT EXISTS (SELECT 1 FROM inventory i WHERE i.film_id = f.film_id)
ORDER BY f.title
