-- film_length_distribution: Number of films per length bucket.
SELECT
    CASE
        WHEN length < 60  THEN '< 60 min'
        WHEN length < 90  THEN '60 - 89 min'
        WHEN length < 120 THEN '90 - 119 min'
        WHEN length < 150 THEN '120 - 149 min'
        ELSE                   '150+ min'
    END                                 AS "Length Bucket",
    COUNT(*)                            AS "Number of Films",
    AVG(rental_rate)        AS "Avg Rental Rate",
    AVG(replacement_cost)   AS "Avg Replacement Cost"
FROM film
GROUP BY "Length Bucket"
ORDER BY MIN(length)
