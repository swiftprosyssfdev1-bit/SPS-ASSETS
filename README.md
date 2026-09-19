# Swift ProSys — Asset Register

Django app to track every company asset (workstations, monitors, keyboards,
mice, UPS, hard disks, software licenses, A/Cs, biometrics, networking gear,
employees, vendors, projects, incidents, etc.) with a branded admin login, a
dashboard, bulk import from spreadsheets, exports, and automatic
"previous vs current" history for every asset.

## What's included

- **Login page** (`/login/`) — branded, restricted to staff accounts.
- **Dashboard** (`/`) — total assets, status breakdown, per-category counts,
  and a live feed of recent changes (old value → new value, who changed it,
  when).
- **Category pages** (`/category/<id>/`) — click a category card to see every
  asset in it, current vs previous holder/location, with add/edit/delete.
- **Search** (`/search/`) — find assets across categories.
- **History** (`/history/`) — full audit trail across all assets.
- **Bulk import** (`/import/`) — upload a `.csv`, `.txt`, or `.xlsx` file,
  preview/confirm the parsed rows (`/import/confirm/`), and import up to
  20,000 rows at once. Understands many header spellings from the old
  per-category spreadsheets (e.g. "S.No", "Condition", "Assigned To") so
  legacy sheets can usually be uploaded with little to no renaming. A sample
  template is downloadable at `/import/sample/`.
- **Export** (`/export/`) — download current asset data.
- **Clear all** (`/clear-all/`) — wipes all asset data (destructive, staff
  only).
- **Django Admin** (`/admin/`) — full add/edit/delete for categories and
  assets.
- **Automatic history**: changing an asset's status, assigned user, location,
  brand, model, or serial number and saving writes a row to `AssetHistory` —
  "previous" and "now" are always available with no manual work.
- **Per-category dynamic fields** (`assets/category_fields.py`) — categories
  like Keyboard, Mouse, Monitor, Hard Disk, and Laptop show extra fields
  specific to that category (e.g. DPI, panel type, disk type), stored in
  `extra_details` with no migration needed. Fields flagged as sensitive
  (passwords, license/product keys) are filtered out of import/UI by default.

## How the data model works

Rather than a separate table per asset type, there's one flexible `Asset`
model with a `category` (ForeignKey to `AssetCategory`) and a JSON
`extra_details` field for anything category-specific. This means:

- New categories can be added anytime from the admin panel without touching
  code.
- Common fields (status, current/previous holder, current/previous location,
  brand, model, serial, purchase/service dates, linked workstation, notes)
  are shared and searchable/filterable across every category.

19 starter categories are pre-seeded via `seed_categories` to match the
original spreadsheet tabs: Employee, Workstation, CPU / System Unit, Monitor,
Keyboard, Mouse, UPS, Bluetooth Device, Hard Disk, Software / OS License, Air
Conditioner, Biometric Device, Networking Equipment, Other Asset, Project
Details, Project Backup, Inside Cupboard, IT Vendor, Incident Register.

## Setup

```bash
python -m venv venv
source venv/bin/activate        # venv\Scripts\activate on Windows
pip install -r requirements.txt

python manage.py migrate
python manage.py seed_categories      # loads the starter categories
python manage.py createsuperuser      # your admin login

python manage.py runserver
```

Then visit:
- `http://127.0.0.1:8000/` → login page → dashboard
- `http://127.0.0.1:8000/admin/` → full admin panel to add/edit assets

## Bringing in the old spreadsheet data

Two separate paths exist for getting spreadsheet data in:

- **`/import/` (web UI)** — for clean, single-sheet files already close to
  the app's own column format. Best for ongoing day-to-day imports.
- **`manage.py import_legacy_assets /path/to/Assets.xlsx --dry-run`** — a
  one-time management command written specifically for the old messy
  multi-sheet tracking file, where each sheet (keyboard, Monitor, Mouse,
  UPS, Hard disk, Bluetooth, Software and OS, others, WorkStation_List) has
  its own column shape. Sheets that aren't per-asset records (System,
  Project Details, Project backup, inside the Cupboard, IT_Vendor List,
  Incident Register) are intentionally skipped and need manual review.
  Always run with `--dry-run` first and read the report before committing.

Two related cleanup commands:
- `manage.py reroute_other_assets [--apply]` — moves rows that landed in the
  "Other Asset" catch-all into their real category based on a stored
  Device Type, for data imported before that routing existed.
- `manage.py scrub_legacy_secrets [--apply]` — finds/removes plain-text
  secrets (passwords, license keys) that were imported into
  `extra_details` before the sensitive-field filter existed. Take a DB
  backup before using `--apply`.

## Database

Controlled by env vars (see `.env` / `python-dotenv`):
- If `DB_NAME_SQLITE` (or no MySQL env vars) is set, SQLite is used —
  convenient for local development.
- Otherwise the app uses MySQL (`DB_NAME`, `DB_USER`, `DB_PASSWORD`,
  `DB_HOST`, `DB_PORT`), which is the production setup.

## Notes / things to decide before production use

- `SECRET_KEY` in `asset_register/settings.py` is a dev placeholder — replace
  it and set `DEBUG = False` with a real `ALLOWED_HOSTS` before deploying.
- Only staff/superuser accounts can log in. Regular (non-staff) users can't
  currently reach the dashboard.
- Run `scrub_legacy_secrets` (dry run first) after any bulk import of old
  spreadsheets to catch plain-text secrets that shouldn't be stored.
