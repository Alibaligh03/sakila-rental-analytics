-- top_cities_by_customers: Top 20 cities by number of customers, with the revenue those customers generated.
-- Spend is pre-aggregated per customer before joining, so each customer is counted once. Joining payments directly
-- onto customers and then counting customers repeats a customer once per payment (a city showed 50 customers
-- when the real maximum is 2). In this dataset almost every city has exactly one customer, so ties are broken by revenue.
SELECT
    ci.city                             AS "City",
    co.country                          AS "Country",
    COUNT(*)                            AS "Customers",
    SUM(spend.total_spent)              AS "Total Revenue"
FROM customer c
JOIN address a      ON c.address_id     = a.address_id
JOIN city ci        ON a.city_id        = ci.city_id
JOIN country co     ON ci.country_id    = co.country_id
LEFT JOIN (
    SELECT customer_id, SUM(amount) AS total_spent
    FROM payment
    GROUP BY customer_id
) spend             ON spend.customer_id = c.customer_id
GROUP BY ci.city_id
ORDER BY "Customers" DESC, "Total Revenue" DESC, ci.city
LIMIT 20
