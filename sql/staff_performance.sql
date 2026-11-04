-- staff_performance: Rentals handled and revenue collected per staff member.
SELECT
    st.first_name || ' ' || st.last_name    AS "Staff Member",
    'Store ' || s.store_id                  AS "Store",
    COUNT(DISTINCT r.rental_id)             AS "Rentals Handled",
    SUM(p.amount)               AS "Revenue Collected",
    AVG(p.amount)           AS "Avg Transaction Value"
FROM staff      st
JOIN store      s   ON st.store_id      = s.store_id
JOIN rental     r   ON st.staff_id      = r.staff_id
LEFT JOIN payment p ON r.rental_id      = p.rental_id
GROUP BY st.staff_id
ORDER BY "Revenue Collected" DESC
