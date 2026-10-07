-- Initial directory schema. See docs/provider-schema-proposal.md.

-- Lookup tables ------------------------------------------------------------
create table governorates (
  code    text primary key,
  name_ar text not null,
  name_en text not null
);

create table cities (
  id               serial primary key,
  governorate_code text not null references governorates,
  name_ar          text not null,
  name_en          text
);

create table specialties (
  code    text primary key,        -- child_psychiatry, dev_pediatrics, speech_therapy, ...
  name_ar text not null,
  name_en text not null
);

create table services (
  code    text primary key,        -- autism_diagnosis, aba, ot, parent_training, ...
  name_ar text not null,
  name_en text not null
);

create table sources (
  id         serial primary key,
  name       text not null,
  url        text,
  terms_note text
);

-- Enums --------------------------------------------------------------------
create type provider_type  as enum ('doctor','therapist','psychologist','center','school','hospital_unit');
create type verification   as enum ('unverified','needs_confirmation','phone_confirmed','provider_confirmed','syndicate_verified');
create type listing_status as enum ('active','possibly_stale','closed','removed_on_request');

-- Providers (people and organisations) -------------------------------------
create table providers (
  id                    uuid primary key default gen_random_uuid(),
  slug                  text unique not null,
  type                  provider_type not null,
  name_ar               text not null,
  name_en               text,
  name_search           text not null,      -- NFKC + Arabic letter normalisation, no titles
  gender                text check (gender in ('female','male')),
  professional_title_ar text,
  professional_title_en text,
  academic_title_ar     text,
  supervisor_id         uuid references providers,
  syndicate_number      text,
  licence_verified_at   timestamptz,
  languages             text[] not null default '{ar}',
  age_min_months        int,
  age_max_months        int,
  age_notes_ar          text,
  autism_evidence_ar    text,
  verification          verification not null default 'unverified',
  provider_confirmed_at timestamptz,
  claimed_by_provider   boolean not null default false,
  opt_out               boolean not null default false,
  status                listing_status not null default 'active',
  first_seen_at         timestamptz,
  last_seen_at          timestamptz,
  content_hash          text,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  check (age_min_months is null or age_max_months is null or age_min_months <= age_max_months)
);
create index providers_name_search_trgm on providers using gin (name_search gin_trgm_ops);
create index providers_status on providers (status) where not opt_out;

create table provider_specialties (
  provider_id    uuid references providers on delete cascade,
  specialty_code text references specialties,
  primary key (provider_id, specialty_code)
);

create table provider_services (
  provider_id  uuid references providers on delete cascade,
  service_code text references services,
  primary key (provider_id, service_code)
);

-- Facilities (places) ------------------------------------------------------
create table facilities (
  id               uuid primary key default gen_random_uuid(),
  name_ar          text,
  name_en          text,
  facility_type    text not null,           -- clinic, center, hospital, ...
  governorate_code text references governorates,
  city_id          int references cities,
  district_ar      text,
  address_ar       text,
  address_en       text,
  geo              geography(Point, 4326),
  status           listing_status not null default 'active',
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index facilities_geo on facilities using gist (geo);
create index facilities_governorate on facilities (governorate_code);

create table provider_locations (
  provider_id    uuid references providers on delete cascade,
  facility_id    uuid references facilities on delete cascade,
  branch_name_ar text,
  visit_modes    text[],                    -- in_person, online, home
  fee_min        numeric,
  fee_max        numeric,
  currency       text not null default 'EGP',
  fee_checked_at date,
  booking_url    text,
  primary key (provider_id, facility_id)
);

create table contacts (
  id          uuid primary key default gen_random_uuid(),
  provider_id uuid references providers on delete cascade,
  facility_id uuid references facilities on delete cascade,
  kind        text not null check (kind in ('phone','mobile','whatsapp','email','website','facebook')),
  value       text not null,                -- phones in +20 format
  source_id   int references sources,
  verified_at timestamptz,
  check (provider_id is not null or facility_id is not null)
);

-- Provenance, warnings, ratings --------------------------------------------
create table observations (               -- per-field provenance; the daily job writes here
  id          bigserial primary key,
  provider_id uuid references providers on delete cascade,
  facility_id uuid references facilities on delete cascade,
  source_id   int references sources,
  source_url  text,
  field       text not null,
  value       jsonb,
  observed_at timestamptz not null default now()
);
create index observations_provider on observations (provider_id, field, observed_at desc);

create table warnings (
  id          bigserial primary key,
  provider_id uuid not null references providers on delete cascade,
  code        text not null,               -- admin_title_unconfirmed, platform_phone_only, ...
  note_ar     text,
  note_en     text
);

create table external_ratings (           -- shown as an outbound link, not copied numbers
  id           bigserial primary key,
  provider_id  uuid not null references providers on delete cascade,
  platform     text not null,
  value        numeric,
  scale        int,
  review_count int,
  checked_at   date,
  url          text
);

create table change_events (
  id            bigserial primary key,
  provider_id   uuid references providers on delete cascade,
  facility_id   uuid references facilities on delete cascade,
  field         text,
  old_value     jsonb,
  new_value     jsonb,
  detected_at   timestamptz not null default now(),
  review_status text not null default 'pending' check (review_status in ('pending','applied','rejected'))
);
create index change_events_pending on change_events (detected_at) where review_status = 'pending';

create table provider_embeddings (
  provider_id uuid primary key references providers on delete cascade,
  embedding   vector(1024),
  model       text,
  updated_at  timestamptz not null default now()
);

-- updated_at trigger -------------------------------------------------------
create function set_updated_at() returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

create trigger providers_updated_at  before update on providers  for each row execute function set_updated_at();
create trigger facilities_updated_at before update on facilities for each row execute function set_updated_at();

-- Row level security -------------------------------------------------------
-- Supabase exposes public tables through its REST API. RLS on with no policies
-- means only the backend (direct Postgres connection / service role) can read
-- or write. Add read policies later if the web app queries Supabase directly.
do $$
declare t text;
begin
  foreach t in array array[
    'governorates','cities','specialties','services','sources','providers',
    'provider_specialties','provider_services','facilities','provider_locations',
    'contacts','observations','warnings','external_ratings','change_events',
    'provider_embeddings'
  ] loop
    execute format('alter table %I enable row level security', t);
  end loop;
end $$;
