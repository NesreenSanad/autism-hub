-- Tables and reference data for the doctor collector (backend/collector, backend/jobs).

-- Reference data -----------------------------------------------------------
insert into specialties (code, name_ar, name_en) values
  ('child_psychiatry',     'الطب النفسي للأطفال',          'Child psychiatry'),
  ('dev_pediatrics',       'طب الأطفال النمائي والسلوكي',   'Developmental and behavioural paediatrics'),
  ('pediatric_neurology',  'مخ وأعصاب أطفال',               'Paediatric neurology'),
  ('speech_therapy',       'تخاطب',                         'Speech therapy'),
  ('occupational_therapy', 'علاج وظيفي',                    'Occupational therapy'),
  ('behaviour_therapy',    'تعديل سلوك',                    'Behaviour therapy (ABA)'),
  ('psychology',           'علم النفس والتقييم',            'Psychology and assessment'),
  ('special_education',    'تربية خاصة',                    'Special education')
on conflict (code) do nothing;

insert into services (code, name_ar, name_en) values
  ('autism_diagnosis',  'تشخيص التوحد',        'Autism diagnosis and assessment'),
  ('speech',            'جلسات تخاطب',         'Speech sessions'),
  ('ot',                'علاج وظيفي',          'Occupational therapy'),
  ('aba',               'تحليل السلوك التطبيقي', 'ABA'),
  ('behaviour',         'تعديل سلوك',          'Behaviour modification'),
  ('sensory',           'تكامل حسي',           'Sensory integration'),
  ('parent_training',   'تدريب الأهل',         'Parent training'),
  ('school_support',    'دمج ودعم مدرسي',      'School inclusion support'),
  ('special_education', 'تربية خاصة',          'Special education')
on conflict (code) do nothing;

create unique index if not exists sources_name on sources (name);

insert into sources (name, url, terms_note) values
  ('google_places', 'https://developers.google.com/maps/documentation/places/web-service',
   'Only place_id may be stored indefinitely; other Google content must be refreshed (the job refreshes each place weekly).')
on conflict (name) do nothing;

-- One row per record a source gave us (a Google place, a CSV row) ----------
create table source_records (
  id              bigserial primary key,
  source_id       int not null references sources,
  external_id     text not null,              -- Google place_id, or the CSV row key
  source_url      text,
  facility_id     uuid references facilities on delete set null,
  provider_id     uuid references providers on delete set null,
  data            jsonb not null,             -- latest normalised fields from this source
  content_hash    text not null,
  review_status   text not null default 'pending'
                  check (review_status in ('pending','approved','rejected')),
  first_seen_at   timestamptz not null default now(),
  last_seen_at    timestamptz not null default now(),
  last_checked_at timestamptz not null default now(),
  missed_runs     int not null default 0,
  unique (source_id, external_id)
);
create index source_records_review on source_records (review_status) where review_status <> 'rejected';
create index source_records_facility on source_records (facility_id);

-- One row per job run, for monitoring and for spacing out paid API calls --
create table ingest_runs (
  id          bigserial primary key,
  job         text not null,                  -- google_discovery, google_refresh, csv_import, daily_check
  source_id   int references sources,
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  status      text not null default 'running' check (status in ('running','ok','failed')),
  stats       jsonb not null default '{}',
  error       text
);
create index ingest_runs_job on ingest_runs (job, started_at desc);

-- change_events: link to the source record, say how risky the change is, and
-- let a reviewer mark a pending change 'approved' so the next run applies it.
alter table change_events
  add column source_id        int references sources,
  add column source_record_id bigint references source_records on delete cascade,
  add column risk             text check (risk in ('low','high'));
alter table change_events drop constraint change_events_review_status_check;
alter table change_events add constraint change_events_review_status_check
  check (review_status in ('pending','approved','applied','rejected'));
create index change_events_approved on change_events (id) where review_status = 'approved';

alter table observations add column source_record_id bigint references source_records on delete cascade;

alter table source_records enable row level security;
alter table ingest_runs    enable row level security;
