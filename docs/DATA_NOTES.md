# Olist data notes (card T2) — measured on the real files, 2026-10-08

Source: *Brazilian E-Commerce Public Dataset by Olist* (Kaggle), uploaded as `archive.zip` (9 CSVs, 126 MB unpacked), unpacked to `data/olist/` (gitignored).
**License:** Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0) — Olist / Kaggle.

## Files
| File | Rows | Notes |
|---|---:|---|
| olist_customers_dataset.csv | 99,441 | 27 customer states |
| olist_orders_dataset.csv | 99,441 | purchase 2016-09-04 .. 2018-10-17; nulls: approved_at 160, delivered_carrier 1,783, delivered_customer 2,965 |
| olist_order_items_dataset.csv | 112,650 | 775 orders have no items |
| olist_order_payments_dataset.csv | 103,886 | credit_card 76,795 · boleto 19,784 · voucher 5,775 · debit_card 1,529 · not_defined 3 |
| olist_order_reviews_dataset.csv | 99,224 | **814 duplicate review_id** (no primary key); scores 1–5 |
| olist_products_dataset.csv | 32,951 | 610 products without category; header spelling `product_name_lenght`, `product_description_lenght` (as in source) |
| olist_sellers_dataset.csv | 3,095 | 23 seller states |
| product_category_name_translation.csv | 71 | file starts with a UTF-8 byte-order mark; 2 categories missing → added by `sql/06_category_fixes.sql` (our translations) |
| olist_geolocation_dataset.csv | 1,000,163 | not loaded (unused) |

Headers match `sql/05_raw_schema.sql` exactly. Join integrity: no orphan orders, items, payments or reviews.

## Orders per month (purchase timestamp)
2016-09: 4 · 2016-10: 324 · **2016-11: 0** · 2016-12: 1 · 2017-01: 800 · 02: 1,780 · 03: 2,682 · 04: 2,404 · 05: 3,700 · 06: 3,245 · 07: 4,026 · 08: 4,331 · 09: 4,285 · 10: 4,631 · **11: 7,544** · 12: 5,673 · 2018-01: 7,269 · 02: 6,728 · 03: 7,211 · 04: 6,939 · 05: 6,873 · 06: 6,167 · 07: 6,292 · **08: 6,512** · 09: 16 · 10: 4

## Decisions
- **`as_of_date = 2018-08-31`** (last complete month; Sep/Oct 2018 have 20 stray orders). All relative dates resolve against it.
- Trends should start 2017-01 (2016 is sparse, Nov 2016 empty).
- Nov 2017 spike (7,544 vs 4,631 in Oct) is a real event usable for "why did it change" tasks (likely Black Friday — an inference, not stated in the data).
- Revenue metrics exclude `canceled` and `unavailable` orders.

## Measured totals (loaded DB)
- Item revenue, all statuses: 13,591,643.70 · freight 2,251,909.54 · revenue excluding canceled/unavailable: 13,494,400.74
- Revenue by region (valid sales): Southeast 8,822,531 · South 1,938,021 · Northeast 1,538,209 · Central-West 861,825 · North 333,814
- Late-delivery rate 8.11 % · average delivery 12.56 days (delivered orders)
- Category fallback: 1,603 order lines have category `unknown` (product without category)
