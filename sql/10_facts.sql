-- LAYER: derived. Denormalised fact tables the semantic layer points at, rebuilt from raw_* only.
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
  c.customer_unique_id,
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
LEFT JOIN core_category_map t ON t.product_category_name = p.product_category_name
LEFT JOIN raw_sellers s USING (seller_id);

-- one row per order
CREATE TABLE fact_orders AS
SELECT
  o.order_id, o.order_status,
  (o.order_status NOT IN ('canceled','unavailable')) AS is_valid_sale,
  o.order_purchase_timestamp::date AS order_date,
  date_trunc('month', o.order_purchase_timestamp)::date AS order_month,
  c.customer_unique_id,
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
CREATE INDEX ON fact_order_items (seller_id);
CREATE INDEX ON fact_order_items (product_id);
CREATE INDEX ON fact_orders (order_date);
CREATE INDEX ON fact_payments (order_date);
CREATE INDEX ON fact_reviews (order_date);
CREATE INDEX ON fact_reviews (order_id);

-- one row per order, everything the Orders screens and order APIs show. Lateness uses exactly the
-- fact-table rule (delivered timestamp > estimated timestamp); days are rounded UP, so a late order
-- is always at least 1 day late. Open orders past their estimate get days_overdue relative to the
-- dataset's as_of_date (2018-08-31).
DROP TABLE IF EXISTS core_order_summary CASCADE;
CREATE TABLE core_order_summary AS
WITH it AS (
  SELECT i.order_id, count(*)::int AS items, sum(i.price)::float8 AS items_value,
         sum(i.freight_value)::float8 AS freight,
         array_agg(DISTINCT i.seller_id ORDER BY i.seller_id) AS seller_ids,
         array_agg(DISTINCT COALESCE(m.product_category_name_english, p.product_category_name, 'unknown')) AS categories
  FROM raw_order_items i
  LEFT JOIN raw_products p USING (product_id)
  LEFT JOIN core_category_map m ON m.product_category_name = p.product_category_name
  GROUP BY i.order_id),
rv AS (SELECT DISTINCT ON (order_id) order_id, review_score
       FROM raw_reviews ORDER BY order_id, review_creation_date DESC, review_id),
pay AS (SELECT order_id, sum(payment_value)::float8 AS payment_total,
               (array_agg(payment_type ORDER BY payment_sequential))[1] AS payment_type
        FROM raw_payments GROUP BY order_id)
SELECT
  o.order_id, upper(left(o.order_id, 8)) AS order_ref, o.order_status,
  (o.order_status NOT IN ('canceled', 'unavailable')) AS is_valid_sale,
  o.order_purchase_timestamp AS purchased_at, o.order_purchase_timestamp::date AS order_date,
  o.order_estimated_delivery_date AS estimated_delivery_at, o.order_delivered_customer_date AS delivered_at,
  c.customer_unique_id, c.customer_city, c.customer_state, br_region(c.customer_state) AS customer_region,
  COALESCE(it.items, 0) AS items, COALESCE(it.items_value, 0) AS items_value, COALESCE(it.freight, 0) AS freight,
  COALESCE(it.seller_ids, '{}') AS seller_ids, COALESCE(it.categories, '{}') AS categories,
  pay.payment_total, pay.payment_type, rv.review_score,
  CASE WHEN o.order_delivered_customer_date IS NOT NULL
       THEN EXTRACT(EPOCH FROM (o.order_delivered_customer_date - o.order_purchase_timestamp)) / 86400.0 END AS delivery_days,
  CASE WHEN o.order_delivered_customer_date IS NULL THEN NULL
       ELSE o.order_delivered_customer_date > o.order_estimated_delivery_date END AS is_late,
  CASE WHEN o.order_delivered_customer_date IS NULL THEN NULL
       ELSE GREATEST(0, CEIL(EXTRACT(EPOCH FROM (o.order_delivered_customer_date - o.order_estimated_delivery_date)) / 86400.0))::int
       END AS days_late,
  CASE WHEN o.order_delivered_customer_date IS NULL AND o.order_status NOT IN ('canceled', 'unavailable')
       THEN GREATEST(0, DATE '2018-08-31' - o.order_estimated_delivery_date::date) END AS days_overdue
FROM raw_orders o
JOIN raw_customers c USING (customer_id)
LEFT JOIN it USING (order_id)
LEFT JOIN rv USING (order_id)
LEFT JOIN pay USING (order_id);
ALTER TABLE core_order_summary ADD PRIMARY KEY (order_id);
CREATE INDEX ON core_order_summary (order_ref);
CREATE INDEX ON core_order_summary (order_date);
CREATE INDEX ON core_order_summary (customer_state);
CREATE INDEX ON core_order_summary (customer_unique_id);
CREATE INDEX ON core_order_summary USING gin (seller_ids);
CREATE INDEX ON core_order_summary USING gin (categories);

-- ---------------------------------------------------------------- data dictionary (read by the API)
-- Each comment starts with the table's layer: original | derived | synthetic | system.
COMMENT ON TABLE raw_customers IS 'original: Olist customers, one row per order-customer (customer_unique_id identifies a person)';
COMMENT ON TABLE raw_orders IS 'original: Olist orders with status and purchase/approval/delivery/estimated dates';
COMMENT ON TABLE raw_order_items IS 'original: Olist order lines with product, seller, price and freight (BRL)';
COMMENT ON TABLE raw_payments IS 'original: Olist payments; payment_value includes freight, so it is not revenue';
COMMENT ON TABLE raw_reviews IS 'original: Olist customer reviews (score 1-5, optional comment); key is (review_id, order_id)';
COMMENT ON TABLE raw_products IS 'original: Olist products with Portuguese category name and dimensions';
COMMENT ON TABLE raw_sellers IS 'original: Olist sellers with city and state';
COMMENT ON TABLE raw_category_translation IS 'original: Olist category name translation (Portuguese -> English)';
COMMENT ON TABLE raw_marketing_qualified_leads IS 'original: Olist marketing funnel, seller leads with first contact date and origin channel';
COMMENT ON TABLE raw_closed_deals IS 'original: Olist marketing funnel, leads that became sellers (joins raw_sellers on seller_id)';
COMMENT ON TABLE core_category_map IS 'derived: category translation = Olist rows + 2 AppPilot translations for untranslated categories';
COMMENT ON TABLE core_order_summary IS 'derived: one row per order with value, freight, sellers, categories, payment, review and lateness';
COMMENT ON TABLE fact_order_items IS 'derived: one row per order line, denormalised with customer, seller, category and delivery outcome';
COMMENT ON TABLE fact_orders IS 'derived: one row per order with status, customer location and delivery outcome';
COMMENT ON TABLE fact_payments IS 'derived: one row per payment with order date and customer location';
COMMENT ON TABLE fact_reviews IS 'derived: one row per review with order date and customer location';
