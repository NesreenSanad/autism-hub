# Provider record: feedback and proposed schema

Target: Supabase PostgreSQL with PostGIS and pgvector.

## What the suggested fields get right
- `autism_service_evidence` and `warnings_ar`: they say *why* a provider is listed and what is uncertain. Keep both.
- `verification_status`, `provider_confirmed_at` and `source_checked_at` separate "we saw it online" from "the provider confirmed it".
- `phone` is left null instead of storing Vezeeta's hotline (16676). That's the right call.
- `rating` stores its platform and `checked_at` date, and the warnings say it is not about autism outcomes.

## What to change
1. **Arabic text copied from PDFs uses "presentation forms"** (e.g. ﻧﻬﺎل instead of نهال). Search and dedupe break on these. Run Unicode NFKC normalisation on every text field before saving.
2. **No ids.** Add a `uuid` primary key per provider and a stable `slug` for URLs.
3. **One flat record mixes the person and the place.** One doctor often has several clinics, and one centre has many staff. Split into `providers`, `facilities` and `provider_locations` (see below). That replaces `facility_name_ar`, `branch_name_ar` and `address_ar` on the provider.
4. **Free Arabic text where codes are needed.** `provider_type`, `facility_type`, `specialty_ar`, `governorate_ar` and `city_ar` should be lookup tables or enums with `code`, `label_ar` and `label_en`. For example, use `facility_type = 'clinic'` instead of "عيادة", and governorate codes for Egypt's 27 governorates. Then filtering works and English comes free.
5. **Sentence-style values.** For example, `age_range` = "children and adolescents; limits not published". Use `age_min_months`, `age_max_months` (nullable) plus a `notes_ar` field. Do the same for `warnings_ar`: a `code` (e.g. `admin_title_unconfirmed`, `platform_phone_only`) plus text.
6. **Single `phone`.** Use a `contacts` table instead: type (clinic, mobile, WhatsApp, email, website, Facebook), value in `+20…` format, source and verified flag.
7. **Provenance per field, not per record.** In the sample, the address, rating and admin title come from different sources with different confidence. An `observations` table (field, value, source, seen_at) handles this and is also what the daily change check compares.

## What is missing
- English name (`name_en`) and a normalised search name (`name_search`).
- Location: `geo geography(Point,4326)` for "near me" (PostGIS).
- Gender of provider (many families ask for it), and languages spoken.
- Fees: `fee_min`, `fee_max`, `currency`, `fee_checked_at`.
- Visit modes: in person, online, home visit.
- Structured autism services: diagnosis/assessment (e.g. ADOS-2), speech, OT, ABA, behaviour, parent training, school support.
- Licence: syndicate number and whether it was verified.
- Listing state: `status` (active / possibly_stale / closed / removed_on_request), `first_seen_at`, `last_seen_at`, `content_hash`, `created_at`, `updated_at`.
- Provider control: `claimed_by_provider`, `opt_out` (Egypt's data protection law 151/2020).
- pgvector: put embeddings in their own table, not on the main row.

## About Vezeeta as the source
The sample record comes entirely from Vezeeta (booking link, rating, title, services). Linking to a Vezeeta profile as a booking link is fine. Copying their content and ratings in bulk with an automated job conflicts with their terms. Safer: use Vezeeta as a lead, confirm the details with the clinic or hospital (e.g. the Abbasia autism unit role), and store only what you confirmed yourself. If you want their data at scale, ask them for a partnership. Show third-party ratings as an outbound link, not as copied numbers.

## Proposed tables (sketch)
```sql
create extension if not exists postgis;
create extension if not exists vector;
create extension if not exists pg_trgm;

create table governorates (code text primary key, name_ar text not null, name_en text not null);
create table cities (id serial primary key, governorate_code text references governorates, name_ar text not null, name_en text);
create table specialties (code text primary key, name_ar text not null, name_en text not null);   -- child_psychiatry, dev_pediatrics, speech_therapy, ...
create table services (code text primary key, name_ar text not null, name_en text not null);      -- autism_diagnosis, aba, ot, parent_training, ...

create type provider_type as enum ('doctor','therapist','psychologist','center','school','hospital_unit');
create type verification as enum ('unverified','needs_confirmation','phone_confirmed','provider_confirmed','syndicate_verified');
create type listing_status as enum ('active','possibly_stale','closed','removed_on_request');

create table providers (
  id uuid primary key default gen_random_uuid(),
  slug text unique not null,
  type provider_type not null,
  name_ar text not null,
  name_en text,
  name_search text not null,               -- NFKC + Arabic letter normalisation, no titles
  gender text check (gender in ('female','male')),
  professional_title_ar text, professional_title_en text,
  academic_title_ar text,
  supervisor_id uuid references providers,  -- for therapists working under a doctor
  syndicate_number text, licence_verified_at timestamptz,
  languages text[] default '{ar}',
  age_min_months int, age_max_months int, age_notes_ar text,
  autism_evidence_ar text,
  verification verification not null default 'unverified',
  provider_confirmed_at timestamptz,
  claimed_by_provider boolean default false,
  opt_out boolean default false,
  status listing_status not null default 'active',
  first_seen_at timestamptz, last_seen_at timestamptz,
  content_hash text,
  created_at timestamptz default now(), updated_at timestamptz default now()
);
create index on providers using gin (name_search gin_trgm_ops);

create table provider_specialties (provider_id uuid references providers, specialty_code text references specialties, primary key (provider_id, specialty_code));
create table provider_services   (provider_id uuid references providers, service_code text references services, primary key (provider_id, service_code));

create table facilities (
  id uuid primary key default gen_random_uuid(),
  name_ar text, name_en text,
  facility_type text not null,             -- clinic, center, hospital, ...
  governorate_code text references governorates,
  city_id int references cities,
  district_ar text,
  address_ar text, address_en text,
  geo geography(Point,4326),
  status listing_status not null default 'active'
);
create index on facilities using gist (geo);

create table provider_locations (
  provider_id uuid references providers, facility_id uuid references facilities,
  branch_name_ar text,
  visit_modes text[],                      -- in_person, online, home
  fee_min numeric, fee_max numeric, currency text default 'EGP', fee_checked_at date,
  booking_url text,
  primary key (provider_id, facility_id)
);

create table contacts (
  id uuid primary key default gen_random_uuid(),
  provider_id uuid references providers, facility_id uuid references facilities,
  kind text not null,                      -- phone, whatsapp, email, website, facebook
  value text not null,                     -- phones in +20 format
  verified_at timestamptz
);

create table sources (id serial primary key, name text not null, url text, terms_note text);

create table observations (              -- per-field provenance; the daily job writes here
  id bigserial primary key,
  provider_id uuid references providers, facility_id uuid references facilities,
  source_id int references sources, source_url text,
  field text not null, value jsonb,
  observed_at timestamptz not null default now()
);

create table warnings (provider_id uuid references providers, code text not null, note_ar text, note_en text);

create table external_ratings (provider_id uuid references providers, platform text, value numeric, scale int, review_count int, checked_at date, url text);

create table change_events (
  id bigserial primary key, provider_id uuid, facility_id uuid,
  field text, old_value jsonb, new_value jsonb,
  detected_at timestamptz default now(),
  review_status text default 'pending'      -- pending, applied, rejected
);

create table provider_embeddings (provider_id uuid primary key references providers, embedding vector(1024), model text, updated_at timestamptz);
```
