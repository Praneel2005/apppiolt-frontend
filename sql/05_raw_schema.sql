-- DRAFT: column names/types are from memory of the Olist CSVs. Verify against the real CSV headers
-- (head -1 each file) before loading, and fix this file if anything differs.
DROP TABLE IF EXISTS raw_customers, raw_orders, raw_order_items, raw_payments, raw_reviews,
                     raw_products, raw_sellers, raw_category_translation CASCADE;

CREATE TABLE raw_customers (
  customer_id text PRIMARY KEY, customer_unique_id text,
  customer_zip_code_prefix text, customer_city text, customer_state text);

CREATE TABLE raw_orders (
  order_id text PRIMARY KEY, customer_id text, order_status text,
  order_purchase_timestamp timestamp, order_approved_at timestamp,
  order_delivered_carrier_date timestamp, order_delivered_customer_date timestamp,
  order_estimated_delivery_date timestamp);

CREATE TABLE raw_order_items (
  order_id text, order_item_id int, product_id text, seller_id text,
  shipping_limit_date timestamp, price numeric(12,2), freight_value numeric(12,2));

CREATE TABLE raw_payments (
  order_id text, payment_sequential int, payment_type text,
  payment_installments int, payment_value numeric(12,2));

-- review_id is not unique in the source data (do not add a primary key)
CREATE TABLE raw_reviews (
  review_id text, order_id text, review_score int, review_comment_title text,
  review_comment_message text, review_creation_date timestamp, review_answer_timestamp timestamp);

CREATE TABLE raw_products (
  product_id text PRIMARY KEY, product_category_name text,
  product_name_lenght int, product_description_lenght int, product_photos_qty int,
  product_weight_g int, product_length_cm int, product_height_cm int, product_width_cm int);

CREATE TABLE raw_sellers (
  seller_id text PRIMARY KEY, seller_zip_code_prefix text, seller_city text, seller_state text);

CREATE TABLE raw_category_translation (
  product_category_name text PRIMARY KEY, product_category_name_english text);
