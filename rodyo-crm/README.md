# RODYO CRM

Next.js + Supabase V2 for the Balaclava sales dashboard.

This app is intentionally separate from the current Streamlit app. The V1 app remains the source of truth while we migrate rules into Supabase views, SQL functions, and typed frontend services.

## First Run

Node is not currently available on this machine, so dependency installation has not been run yet.

When Node/npm are installed:

```bash
cd rodyo-crm
cp .env.example .env.local
npm install
npm run dev
```

Then add the Supabase values:

```text
NEXT_PUBLIC_SUPABASE_URL
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
SUPABASE_SECRET_KEY
```

## Supabase

Apply the migration in `supabase/migrations/20260619000000_initial_crm_schema.sql`.

The first migration creates:

- Store master data and geocoded locations
- Monthly revenue rows
- Cultivera order and line-item tables
- Contact log, store contact, sample drop, rep, region, and goal tables
- PostGIS location index for fast map/proximity work
- A first `crm_store_rollup` view for the Stores page

## Migration Strategy

1. Load current Google Sheet / Cultivera data into Supabase staging tables.
2. Compare V1 Streamlit outputs against V2 SQL/view outputs.
3. Build UI only against parity-checked views.
4. Move sync jobs from Apps Script/Streamlit into server-side jobs once the schema is stable.

## Import Current Data

The import runs from your computer in Terminal. It reads the current shared Google Sheet, then writes into Supabase using `.env.local`.

From the repo root:

```bash
cd rodyo-crm
python3 -m pip install -r scripts/requirements.txt
python3 scripts/import_current_data.py --dry-run
python3 scripts/import_current_data.py
```

What each command does:

- `cd rodyo-crm`: moves into the V2 project folder.
- `python3 -m pip install -r scripts/requirements.txt`: installs the Python packages the importer needs.
- `python3 scripts/import_current_data.py --dry-run`: checks that it can read and parse the Google Sheet tabs. It does not change Supabase.
- `python3 scripts/import_current_data.py`: imports into Supabase.

If you need to intentionally wipe and reload imported CRM tables:

```bash
python3 scripts/import_current_data.py --replace
```

Use `--replace` only when you are sure you want to clear and reload imported tables.

Planning docs:

- `docs/v2-rules-inventory.md`
- `docs/data-import-map.md`
- `docs/parity-checklist.md`
- `docs/v2-build-plan.md`
