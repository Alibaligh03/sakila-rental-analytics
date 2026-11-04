-- avg_duration_by_category: Average days a film is kept per category, against the average allowed rental duration.
SELECT
    c.name                                                                          AS "Category",
    AVG(julianday(r.return_date) - julianday(r.rental_date))           AS "Avg Days Kept",
    f_avg.avg_allowed_days                                                          AS "Avg Allowed Days"
FROM rental         r
JOIN inventory      i       ON r.inventory_id   = i.inventory_id
JOIN film_category  fc      ON i.film_id        = fc.film_id
JOIN category       c       ON fc.category_id   = c.category_id
JOIN (
    SELECT
        fc2.category_id,
        AVG(f2.rental_duration)   AS avg_allowed_days
    FROM film           f2
    JOIN film_category  fc2 ON f2.film_id = fc2.film_id
    GROUP BY fc2.category_id
) f_avg ON fc.category_id = f_avg.category_id
WHERE r.return_date IS NOT NULL
GROUP BY c.name
ORDER BY "Avg Days Kept" DESC
