-- DRAFT: denormalised tables the semantic layer points at. Re-check after loading real data.
CREATE OR REPLACE FUNCTION br_region(s text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE
    WHEN s IN ('AC','AP','AM','PA','RO','RR','TO') THEN 'North'
    WHEN s IN ('AL','BA','CE','MA','PB','PE','PI','RN','SE') THEN 'Northeast'
    WHEN s IN ('DF','GO','MT','MS') THEN 'Central-West'
    WHEN s IN ('ES','MG','RJ','SP') THEN 'Southeast'
    WHEN s IN ('PR','RS','SC') THEN 'South'
    ELSE 'Unknown' END
$$;

DROP TABLE IF EXISTS fact_order_items, fact_orders, fact_payments, fact_reviews CASCADE;

-- one row per order line; revenue-type metrics ignore canceled/unavailable orders via is_valid_sale
CREATE TABLE fact_order_items AS
SELECT
  oi.order_id, oi.order_item_id, oi.product_id, oi.seller_id,
  oi.price::float8 AS price, oi.freight_value::float8 AS freight_value,
  o.order_status,
  (o.order_status NOT IN ('canceled','unavailable')) AS is_valid_sale,
  o.order_purchase_timestamp::date AS order_date,
  date_trunc('month', o.order_purchase_timestamp)::date AS order_month,
  c.customer_state,
  br_region(c.customer_state) AS customer_region,
  s.seller_state,
  COALESCE(t.product_category_name_english, p.product_category_name, 'unknown') AS product_category,
  CASE WHEN o.order_delivered_customer_date IS NOT NULL
       THEN EXTRACT(EPOCH FROM (o.order_delivered_customer_date - o.order_purchase_timestamp)) / 86400.0 END
       AS delivery_days,
  CASE WHEN o.order_delivered_customer_date IS NULL THEN NULL
       WHEN o.order_delivered_customer_date > o.order_estimated_delivery_date THEN 1 ELSE 0 END AS is_late
FROM raw_order_items oi
JOIN raw_orders o USING (order_id)
JOIN raw_customers c USING (customer_id)
LEFT JOIN raw_products p USING (product_id)
LEFT JOIN raw_category_translation t ON t.product_category_name = p.product_category_name
LEFT JOIN raw_sellers s USING (seller_id);

-- one row per order
CREATE TABLE fact_orders AS
SELECT
  o.order_id, o.order_status,
  (o.order_status NOT IN ('canceled','unavailable')) AS is_valid_sale,
  o.order_purchase_timestamp::date AS order_date,
  date_trunc('month', o.order_purchase_timestamp)::date AS order_month,
  c.customer_state, br_region(c.customer_state) AS customer_region,
  CASE WHEN o.order_delivered_customer_date IS NOT NULL
       THEN EXTRACT(EPOCH FROM (o.order_delivered_customer_date - o.order_purchase_timestamp)) / 86400.0 END
       AS delivery_days,
  CASE WHEN o.order_delivered_customer_date IS NULL THEN NULL
       WHEN o.order_delivered_customer_date > o.order_estimated_delivery_date THEN 1 ELSE 0 END AS is_late
FROM raw_orders o JOIN raw_customers c USING (customer_id);

-- one row per payment
CREATE TABLE fact_payments AS
SELECT
  p.order_id, p.payment_type, p.payment_installments, p.payment_value::float8 AS payment_value,
  o.order_purchase_timestamp::date AS order_date,
  date_trunc('month', o.order_purchase_timestamp)::date AS order_month,
  c.customer_state, br_region(c.customer_state) AS customer_region
FROM raw_payments p JOIN raw_orders o USING (order_id) JOIN raw_customers c USING (customer_id);

-- one row per review
CREATE TABLE fact_reviews AS
SELECT
  r.review_id, r.order_id, r.review_score,
  o.order_purchase_timestamp::date AS order_date,
  date_trunc('month', o.order_purchase_timestamp)::date AS order_month,
  c.customer_state, br_region(c.customer_state) AS customer_region
FROM raw_reviews r JOIN raw_orders o USING (order_id) JOIN raw_customers c USING (customer_id);

CREATE INDEX ON fact_order_items (order_date);
CREATE INDEX ON fact_order_items (customer_state);
CREATE INDEX ON fact_orders (order_date);
CREATE INDEX ON fact_payments (order_date);
CREATE INDEX ON fact_reviews (order_date);
