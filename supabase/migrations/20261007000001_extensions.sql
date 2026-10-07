-- Extensions used by the directory. Supabase keeps extensions in the
-- "extensions" schema, which is on the default search_path.
create extension if not exists postgis with schema extensions;  -- "near me" search on facilities.geo
create extension if not exists vector  with schema extensions;  -- pgvector, for provider_embeddings
create extension if not exists pg_trgm with schema extensions;  -- fuzzy name search
