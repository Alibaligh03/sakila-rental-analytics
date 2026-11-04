-- rentals_by_dow: Rentals per day of the week.
SELECT
    CASE strftime('%w', rental_date)
        WHEN '0' THEN 'Sunday'
        WHEN '1' THEN 'Monday'
        WHEN '2' THEN 'Tuesday'
        WHEN '3' THEN 'Wednesday'
        WHEN '4' THEN 'Thursday'
        WHEN '5' THEN 'Friday'
        WHEN '6' THEN 'Saturday'
    END                                 AS "Day of Week",
    COUNT(rental_id)                    AS "Total Rentals"
FROM rental
GROUP BY strftime('%w', rental_date)
ORDER BY strftime('%w', rental_date)
