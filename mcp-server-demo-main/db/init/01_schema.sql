-- Olist Brazilian E-Commerce relational schema.
--
-- Tables mirror the repository-provided CSVs in data/olist/.
-- Referential data loads after this file (02_load.sql).

-- ---------------------------------------------------------------------------
-- Reference dimensions
-- ---------------------------------------------------------------------------

CREATE TABLE product_category_translation (
    product_category_name        TEXT PRIMARY KEY,
    product_category_name_english TEXT NOT NULL
);

CREATE TABLE customers (
    customer_id        TEXT PRIMARY KEY,
    customer_unique_id TEXT NOT NULL,
    customer_zip_code_prefix TEXT NOT NULL,
    customer_city      TEXT NOT NULL,
    customer_state     CHAR(2) NOT NULL
);

CREATE TABLE sellers (
    seller_id           TEXT PRIMARY KEY,
    seller_zip_code_prefix TEXT NOT NULL,
    seller_city         TEXT NOT NULL,
    seller_state        CHAR(2) NOT NULL
);

CREATE TABLE products (
    product_id                  TEXT PRIMARY KEY,
    product_category_name       TEXT REFERENCES product_category_translation (product_category_name),
    product_name_length         INTEGER CHECK (product_name_length IS NULL OR product_name_length >= 0),
    product_description_length  INTEGER CHECK (product_description_length IS NULL OR product_description_length >= 0),
    product_photos_qty          INTEGER CHECK (product_photos_qty IS NULL OR product_photos_qty >= 0),
    product_weight_g            INTEGER CHECK (product_weight_g IS NULL OR product_weight_g >= 0),
    product_length_cm           INTEGER CHECK (product_length_cm IS NULL OR product_length_cm >= 0),
    product_height_cm           INTEGER CHECK (product_height_cm IS NULL OR product_height_cm >= 0),
    product_width_cm            INTEGER CHECK (product_width_cm IS NULL OR product_width_cm >= 0)
);

-- ---------------------------------------------------------------------------
-- Facts
-- ---------------------------------------------------------------------------

CREATE TABLE orders (
    order_id                     TEXT PRIMARY KEY,
    customer_id                  TEXT NOT NULL REFERENCES customers (customer_id),
    order_status                 TEXT NOT NULL CHECK (order_status IN
        ('created','approved','invoiced','processing','shipped','delivered','canceled','unavailable')),
    order_purchase_timestamp     TIMESTAMP NOT NULL,
    order_approved_at            TIMESTAMP,
    order_delivered_carrier_date TIMESTAMP,
    order_delivered_customer_date TIMESTAMP,
    order_estimated_delivery_date TIMESTAMP,
    CHECK (order_approved_at IS NULL OR order_approved_at >= order_purchase_timestamp)
);

CREATE TABLE order_items (
    order_id          TEXT NOT NULL REFERENCES orders (order_id),
    order_item_id     INTEGER NOT NULL,
    product_id        TEXT NOT NULL REFERENCES products (product_id),
    seller_id         TEXT NOT NULL REFERENCES sellers (seller_id),
    shipping_limit_date TIMESTAMP NOT NULL,
    price             NUMERIC(10, 2) NOT NULL CHECK (price >= 0),
    freight_value     NUMERIC(10, 2) NOT NULL CHECK (freight_value >= 0),
    PRIMARY KEY (order_id, order_item_id)
);

CREATE TABLE order_payments (
    order_id           TEXT NOT NULL REFERENCES orders (order_id),
    payment_sequential INTEGER NOT NULL CHECK (payment_sequential >= 1),
    payment_type       TEXT NOT NULL CHECK (payment_type IN
        ('credit_card','boleto','voucher','debit_card','not_defined')),
    payment_installments INTEGER NOT NULL CHECK (payment_installments >= 0),
    payment_value      NUMERIC(10, 2) NOT NULL CHECK (payment_value >= 0),
    PRIMARY KEY (order_id, payment_sequential)
);

CREATE TABLE order_reviews (
    order_id              TEXT NOT NULL REFERENCES orders (order_id),
    review_id             TEXT NOT NULL,
    review_score          SMALLINT NOT NULL CHECK (review_score BETWEEN 1 AND 5),
    review_comment_title  TEXT,
    review_comment_message TEXT,
    review_creation_date  TIMESTAMP NOT NULL,
    review_answer_timestamp TIMESTAMP NOT NULL,
    PRIMARY KEY (order_id, review_id)
);

-- Geolocation has no natural primary key: many distinct coordinates share the
-- same zip prefix. Retained as a dimensional lookup on zip prefix only.
CREATE TABLE geolocation (
    geolocation_zip_code_prefix TEXT NOT NULL,
    geolocation_lat DOUBLE PRECISION NOT NULL,
    geolocation_lng DOUBLE PRECISION NOT NULL,
    geolocation_city        TEXT NOT NULL,
    geolocation_state       CHAR(2) NOT NULL
);

-- ---------------------------------------------------------------------------
-- Indexes (FK lookup + common analytics access paths)
-- ---------------------------------------------------------------------------

CREATE INDEX idx_orders_customer_id        ON orders (customer_id);
CREATE INDEX idx_orders_purchase_timestamp ON orders (order_purchase_timestamp);
CREATE INDEX idx_products_category         ON products (product_category_name);

CREATE INDEX idx_order_items_product_id    ON order_items (product_id);
CREATE INDEX idx_order_items_seller_id     ON order_items (seller_id);
CREATE INDEX idx_order_payments_type       ON order_payments (payment_type);
CREATE INDEX idx_order_reviews_score       ON order_reviews (review_score);

CREATE INDEX idx_geolocation_zip           ON geolocation (geolocation_zip_code_prefix);