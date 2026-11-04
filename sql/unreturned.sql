-- unreturned: Rentals that have not been returned yet.
SELECT COUNT(rental_id) AS "Currently Unreturned"
FROM rental
WHERE return_date IS NULL
