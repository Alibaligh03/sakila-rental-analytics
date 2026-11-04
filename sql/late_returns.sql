-- late_returns: Number of late returns and the average number of days overdue.
-- A return is late when the elapsed time, counting the time of day, exceeds the film's rental_duration in days.
-- overdue_by_category uses the same rule, so the two always agree.
SELECT
    COUNT(*)                            AS "Late Returns",
    AVG(
        julianday(r.return_date) - julianday(r.rental_date) - f.rental_duration
    )                              AS "Avg Days Overdue"
FROM rental     r
JOIN inventory  i   ON r.inventory_id   = i.inventory_id
JOIN film       f   ON i.film_id        = f.film_id
WHERE r.return_date IS NOT NULL
  AND (julianday(r.return_date) - julianday(r.rental_date)) > f.rental_duration
