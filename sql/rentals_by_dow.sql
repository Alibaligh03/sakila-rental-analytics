-- rentals_by_dow: Rentals per day of the week.
-- Leaves out the 182 rentals of the 2006-02-14 load batch (all on one Tuesday), which would make Tuesday look busiest.
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
WHERE rental_date < '2006-01-01'
GROUP BY strftime('%w', rental_date)
ORDER BY strftime('%w', rental_date)
