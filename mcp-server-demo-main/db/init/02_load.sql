-- Load the Olist CSVs (mounted read-only at /olist-data) into the schema.
-- COPY (server-side) runs as the superuser during initialization.
-- Column lists match the CSV column order; the header row is skipped via HEADER.

\echo 'Loading product_category_translation...'
COPY product_category_translation (product_category_name, product_category_name_english)
FROM '/olist-data/product_category_name_translation.csv'
WITH (FORMAT csv, HEADER true);

-- The Olist source data ships two product categories that are present in
-- olist_products_dataset.csv but absent from the translation file. They are
-- added explicitly here so the products.category FK can be enforced.
INSERT INTO product_category_translation (product_category_name, product_category_name_english) VALUES
    ('pc_gamer', 'PC_gamer'),
    ('portateis_cozinha_e_preparadores_de_alimentos', 'portable_kitchen_and_food_preparation');

\echo 'Loading customers...'
COPY customers (customer_id, customer_unique_id, customer_zip_code_prefix, customer_city, customer_state)
FROM '/olist-data/olist_customers_dataset.csv'
WITH (FORMAT csv, HEADER true);

\echo 'Loading sellers...'
COPY sellers (seller_id, seller_zip_code_prefix, seller_city, seller_state)
FROM '/olist-data/olist_sellers_dataset.csv'
WITH (FORMAT csv, HEADER true);

\echo 'Loading products...'
COPY products (product_id, product_category_name, product_name_length, product_description_length,
               product_photos_qty, product_weight_g, product_length_cm, product_height_cm, product_width_cm)
FROM '/olist-data/olist_products_dataset.csv'
WITH (FORMAT csv, HEADER true);

\echo 'Loading orders...'
COPY orders (order_id, customer_id, order_status, order_purchase_timestamp, order_approved_at,
             order_delivered_carrier_date, order_delivered_customer_date, order_estimated_delivery_date)
FROM '/olist-data/olist_orders_dataset.csv'
WITH (FORMAT csv, HEADER true);

\echo 'Loading order_items...'
COPY order_items (order_id, order_item_id, product_id, seller_id, shipping_limit_date, price, freight_value)
FROM '/olist-data/olist_order_items_dataset.csv'
WITH (FORMAT csv, HEADER true);

\echo 'Loading order_payments...'
COPY order_payments (order_id, payment_sequential, payment_type, payment_installments, payment_value)
FROM '/olist-data/olist_order_payments_dataset.csv'
WITH (FORMAT csv, HEADER true);

\echo 'Loading order_reviews...'
COPY order_reviews (review_id, order_id, review_score, review_comment_title,
                    review_comment_message, review_creation_date, review_answer_timestamp)
FROM '/olist-data/olist_order_reviews_dataset.csv'
WITH (FORMAT csv, HEADER true);

\echo 'Loading geolocation...'
COPY geolocation (geolocation_zip_code_prefix, geolocation_lat, geolocation_lng,
                  geolocation_city, geolocation_state)
FROM '/olist-data/olist_geolocation_dataset.csv'
WITH (FORMAT csv, HEADER true);