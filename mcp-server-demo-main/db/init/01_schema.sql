CREATE TABLE machines (
    machine_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    machine_code TEXT NOT NULL UNIQUE,
    machine_name TEXT NOT NULL,
    production_line TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'idle', 'maintenance')),
    installed_on DATE NOT NULL
);

CREATE TABLE products (
    product_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_code TEXT NOT NULL UNIQUE,
    product_name TEXT NOT NULL,
    product_family TEXT NOT NULL,
    target_cycle_seconds NUMERIC(8, 2) NOT NULL CHECK (target_cycle_seconds > 0)
);

CREATE TABLE production_orders (
    order_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_number TEXT NOT NULL UNIQUE,
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    machine_id INTEGER NOT NULL REFERENCES machines(machine_id),
    planned_quantity INTEGER NOT NULL CHECK (planned_quantity > 0),
    produced_quantity INTEGER NOT NULL DEFAULT 0 CHECK (produced_quantity >= 0),
    status TEXT NOT NULL CHECK (status IN ('planned', 'in_progress', 'complete', 'on_hold')),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

CREATE TABLE quality_checks (
    quality_check_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES production_orders(order_id),
    checked_at TIMESTAMPTZ NOT NULL,
    sample_size INTEGER NOT NULL CHECK (sample_size > 0),
    defect_count INTEGER NOT NULL CHECK (defect_count >= 0),
    result TEXT NOT NULL CHECK (result IN ('pass', 'fail')),
    notes TEXT
);

CREATE TABLE downtime_events (
    downtime_event_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    machine_id INTEGER NOT NULL REFERENCES machines(machine_id),
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    reason TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('planned', 'minor', 'major'))
);
