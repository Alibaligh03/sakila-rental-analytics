-- overdue_by_category: Late return rate per category.
SELECT
    c.name                              AS "Category",
    COUNT(CASE WHEN julianday(r.return_date) - julianday(r.rental_date) > f.rental_duration THEN 1 END)
                                        AS "Late Returns",
    COUNT(r.rental_id)                  AS "Total Returns",
        1.0 * COUNT(CASE WHEN julianday(r.return_date) - julianday(r.rental_date) > f.rental_duration THEN 1 END)
        / COUNT(r.rental_id)  AS "Late Return Rate (%)"
FROM rental         r
JOIN inventory      i   ON r.inventory_id   = i.inventory_id
JOIN film           f   ON i.film_id        = f.film_id
JOIN film_category  fc  ON f.film_id        = fc.film_id
JOIN category       c   ON fc.category_id   = c.category_id
WHERE r.return_date IS NOT NULL
GROUP BY c.name
ORDER BY "Late Return Rate (%)" DESC, c.name
