-- customers_by_country: Customers and revenue per country.
SELECT
    co.country                          AS "Country",
    COUNT(c.customer_id)                AS "Number of Customers"
FROM customer   c
JOIN address    a   ON c.address_id     = a.address_id
JOIN city       ci  ON a.city_id        = ci.city_id
JOIN country    co  ON ci.country_id    = co.country_id
GROUP BY co.country
ORDER BY "Number of Customers" DESC, co.country
