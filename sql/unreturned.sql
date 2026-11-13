-- unreturned: Rentals that have not been returned yet.
-- Leaves out the 2006-02-14 load batch: 182 of the database's 183 unreturned rentals are that batch.
SELECT COUNT(rental_id) AS "Currently Unreturned"
FROM rental
WHERE return_date IS NULL
  AND rental_date < '2006-01-01'
