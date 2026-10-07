-- Extensions used by the directory.
create extension if not exists postgis;     -- "near me" search on facilities.geo
create extension if not exists vector;      -- pgvector, for provider_embeddings
create extension if not exists pg_trgm;     -- fuzzy name search
