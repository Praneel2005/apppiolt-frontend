-- Two product categories in olist_products_dataset.csv have no row in
-- product_category_name_translation.csv (checked on the real data, 2026-10-08).
-- These English names are OUR translations, not part of the original dataset.
INSERT INTO raw_category_translation (product_category_name, product_category_name_english) VALUES
  ('pc_gamer', 'pc_gamer'),
  ('portateis_cozinha_e_preparadores_de_alimentos', 'portable_kitchen_food_processors')
ON CONFLICT (product_category_name) DO NOTHING;
