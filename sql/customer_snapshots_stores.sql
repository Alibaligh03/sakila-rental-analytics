-- customer_snapshots_stores: The same two most recent snapshots, per home store.
WITH n(k) AS (SELECT 0 UNION ALL SELECT 1),
snap AS (SELECT date(:as_of, '-' || k || ' months') AS snapshot_date FROM n)
SELECT
    s.snapshot_date                                             AS "Snapshot Date",
    'Store ' || st.store_id                                     AS "Store",
    (SELECT COUNT(DISTINCT r.customer_id) FROM rental r JOIN customer c ON c.customer_id = r.customer_id
      WHERE c.store_id = st.store_id AND date(r.rental_date) < s.snapshot_date) AS "Customers Who Have Rented"
FROM snap s CROSS JOIN store st
ORDER BY s.snapshot_date DESC, st.store_id
