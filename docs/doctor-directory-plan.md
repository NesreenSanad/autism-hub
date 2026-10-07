# Autism doctor directory (Egypt): feedback and recommended stack

## 1. Feedback on the plan

**The idea is right; reframe the first feature.** "An API that searches online" is really two things:
- an **ingestion pipeline** (a scheduled job that pulls from sources, cleans, dedupes, and records changes), and
- a **read API** that the app calls to search doctors.
Keeping them separate means the app never waits on scraping, and a broken source never breaks search.

**Define "autism specialist" before collecting data.** Families need more than doctors. Use a small taxonomy from day one:
child psychiatrist, developmental/behavioural paediatrician, paediatric neurologist, speech therapist, occupational therapist,
ABA / behaviour therapist, psychologist (diagnosis/assessment), special-education centre, autism centre/clinic.
Tag each record with one or more of these plus services (diagnosis, therapy, school support), languages, price range, governorate/city.

**Data sources, ranked by how safe and useful they are:**
| Source | Notes |
|---|---|
| Google Places API | Legal and reliable for name, address, phone, hours, website, location. Paid per request. Its terms only allow storing `place_id` long-term; other fields must be refreshed (cache max ~30 days), which fits a daily/weekly refresh. |
| Doctors/centres registering themselves | Best quality long-term. Add a "claim/update your profile" form early. |
| NGOs and hospitals | Egyptian Autistic Society, ADVANCE Society, university child-psychiatry units (Kasr Al Ainy, Ain Shams, Alexandria), MoH psychiatric hospitals. Often publish lists; ask them for partnership/data. |
| Egyptian Medical Syndicate | Useful to *verify* a doctor is licensed, not as a bulk source. |
| Vezeeta, Altibbi, Doctor Online, etc. | Richest data, but their terms forbid scraping and the data is their business. Do not scrape; approach them for a partnership or link out to them instead. |
| Facebook pages/groups | Where many Egyptian centres live, but scraping is against Meta's terms. Use for manual leads only. |

**Data quality is the real product.** A wrong phone number for a stressed parent is worse than no listing. Store per record:
`source`, `source_url`, `first_seen_at`, `last_seen_at`, `last_verified_at`, `verification_status` (unverified / phone-confirmed / syndicate-verified / self-claimed), and keep a human review queue.
Dedupe across sources by normalising phones to `+20…` format and normalising Arabic names (unify أ/إ/آ→ا, ة→ه, ى→ي, strip tashkeel and titles like "د." / "دكتور"), then fuzzy-match.

**Change detection (the daily job):**
1. Fetch each source and save the raw result (snapshot) per run.
2. Normalise into the same fields and compute a hash per record.
3. Compare with the last snapshot: new, changed (which fields), or missing.
4. Write a `change_events` row for each difference. Auto-apply low-risk changes (hours); send risky ones (phone, address, "closed") to review.
5. Mark a record "possibly stale" after it is missing for several runs, not on the first miss.
Daily is fine to start, but most of this data changes slowly; weekly for most sources keeps API costs down.

**Legal and privacy.** Egypt's Personal Data Protection Law (No. 151 of 2020) covers doctors' contact details too. Collect only professional data, say where it came from, and let any doctor correct or remove their listing. When you later store anything about children or families, that is sensitive health data: plan for consent, encryption and minimal collection from the start.

**Bilingual from day one.** Store `name_ar` and `name_en` (and address in both where possible), build the UI right-to-left aware, and make search work with Arabic, English and Franco-Arabic spellings.

**Good use of AI here:** many pages are messy Arabic text. An LLM (e.g. Claude) can extract structured fields and classify specialties from a page, which beats hand-written parsers per site. Keep a human check on its output.

## 2. Recommended stack

**Principle:** small team, one language where possible, managed services, cheap to run.

**Backend: Python + FastAPI**
- Python has the best tools for data collection (httpx, Playwright, BeautifulSoup) and AI extraction.
- FastAPI gives a fast, typed REST API with automatic docs (OpenAPI), which the web and mobile apps both consume.
- **Database: PostgreSQL** with **PostGIS** ("doctors near me") and **pg_trgm** (fuzzy Arabic/English name search). Supabase is an easy managed option (also gives auth and storage later).
- **Scheduler:** start with a simple cron job (e.g. GitHub Actions schedule or the host's cron) running the ingestion script; move to Celery/RQ workers only if volume grows.
- **Admin/review panel:** SQLAdmin on FastAPI, or Supabase's table editor at first.
- **Hosting:** Render, Railway or Fly.io for the API; managed Postgres. Low cost at this stage.

**Frontend: TypeScript**
- **Web and desktop: Next.js**, built as a responsive PWA (installable on desktop and phones). Search engines can index it, which matters: many parents start by Googling "دكتور توحد في القاهرة".
- **Mobile apps: React Native with Expo** for Android and iOS when you need native features (notifications, offline, app stores). Android first; it dominates in Egypt.
- Share types and the API client between both in one monorepo.
- UI: Tailwind CSS (built-in RTL support) and next-intl / i18next for Arabic/English.

**Alternative:** Flutter gives one codebase for Android, iOS, web and desktop with excellent RTL support. Choose it if your team already knows Dart; the trade-off is weaker web SEO.

## 3. Suggested first milestones
1. Database schema (doctors, locations, specialties, sources, snapshots, change_events, verification).
2. Ingestion from Google Places for the main governorates plus a manual CSV import for NGO/hospital lists.
3. Read API: search by specialty, city, language, near me.
4. Simple bilingual web app (Next.js PWA) with "report wrong info" and "claim your profile".
5. Daily/weekly change-detection job and review queue.
6. Mobile app (Expo) once the web version is validated with families.
