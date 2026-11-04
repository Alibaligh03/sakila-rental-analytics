-- customer_snapshots: Customers who had rented at least once before each of the last five monthly snapshot dates.
WITH n(k) AS (SELECT 0 UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4),
snap AS (SELECT date(:as_of, '-' || k || ' months') AS snapshot_date FROM n)
SELECT
    s.snapshot_date                                             AS "Snapshot Date",
    (SELECT COUNT(DISTINCT r.customer_id) FROM rental r WHERE date(r.rental_date) < s.snapshot_date) AS "Customers Who Have Rented"
FROM snap s
ORDER BY s.snapshot_date DESC
