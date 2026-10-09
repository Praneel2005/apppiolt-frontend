-- LAYER: derived. Category name mapping used by the fact tables.
-- The original raw_category_translation table is NOT modified: two product categories in
-- olist_products_dataset.csv have no translation (checked 2026-10-08), so this table adds OUR
-- translations for them on top of the source rows. `source` says which is which.
DROP TABLE IF EXISTS core_category_map CASCADE;
CREATE TABLE core_category_map AS
SELECT product_category_name, product_category_name_english, 'olist'::text AS source
FROM raw_category_translation
UNION ALL
SELECT v.name, v.english, 'apppilot'
FROM (VALUES
  ('pc_gamer', 'pc_gamer'),
  ('portateis_cozinha_e_preparadores_de_alimentos', 'portable_kitchen_food_processors')
) AS v(name, english)
WHERE NOT EXISTS (SELECT 1 FROM raw_category_translation t WHERE t.product_category_name = v.name);
ALTER TABLE core_category_map ADD PRIMARY KEY (product_category_name);
