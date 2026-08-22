INSERT INTO machines (machine_code, machine_name, production_line, status, installed_on) VALUES
    ('ASM-01', 'Assembly Cell 01', 'Line A', 'running', '2022-04-18'),
    ('ASM-02', 'Assembly Cell 02', 'Line A', 'maintenance', '2022-04-18'),
    ('CNC-01', 'CNC Mill 01', 'Line B', 'running', '2021-09-07'),
    ('PKG-01', 'Packaging Station 01', 'Line C', 'idle', '2023-01-23');

INSERT INTO products (product_code, product_name, product_family, target_cycle_seconds) VALUES
    ('P-100', 'Control Housing', 'Electrical', 42.5),
    ('P-200', 'Drive Bracket', 'Mechanical', 68.0),
    ('P-300', 'Sensor Module', 'Electrical', 35.0);

INSERT INTO production_orders
    (order_number, product_id, machine_id, planned_quantity, produced_quantity, status, started_at, completed_at)
VALUES
    ('WO-2026-001', 1, 1, 1200, 1200, 'complete', '2026-08-18 06:00:00+00', '2026-08-18 17:20:00+00'),
    ('WO-2026-002', 2, 3, 800, 560, 'in_progress', '2026-08-20 07:00:00+00', NULL),
    ('WO-2026-003', 3, 1, 1500, 900, 'in_progress', '2026-08-21 06:30:00+00', NULL),
    ('WO-2026-004', 1, 2, 1000, 0, 'on_hold', NULL, NULL);

INSERT INTO quality_checks (order_id, checked_at, sample_size, defect_count, result, notes) VALUES
    (1, '2026-08-18 12:00:00+00', 50, 0, 'pass', 'All dimensions within tolerance'),
    (2, '2026-08-20 15:00:00+00', 50, 2, 'pass', 'Two cosmetic defects reworked'),
    (3, '2026-08-21 10:00:00+00', 50, 5, 'fail', 'Investigate sensor connector alignment');

INSERT INTO downtime_events (machine_id, started_at, ended_at, reason, severity) VALUES
    (1, '2026-08-19 09:15:00+00', '2026-08-19 09:42:00+00', 'Material replenishment', 'minor'),
    (2, '2026-08-20 08:00:00+00', NULL, 'Scheduled spindle maintenance', 'planned'),
    (3, '2026-08-20 13:20:00+00', '2026-08-20 14:05:00+00', 'Coolant pressure alarm', 'major'),
    (1, '2026-08-21 08:10:00+00', '2026-08-21 08:25:00+00', 'Sensor recalibration', 'minor');
