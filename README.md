# Swift ProSys — Asset Register

A Django web app for tracking every company asset (workstations, CPUs, monitors,
keyboards, mice, UPS, hard disks, licenses, A/Cs, biometrics, networking gear,
employees, vendors, projects, incidents, etc.) across multiple branches, with
admin-configurable fields, bulk import, password-protected Excel export, and a
full audit trail.

## Features

**Assets**
- **Dashboard** (`/`) — totals, status breakdown, per-category counts and a live
  feed of recent changes.
- **Categories** (`/category/<id>/`) — list, add, edit and delete assets per
  category. Categories themselves can be added, renamed or removed from the UI.
- **Search** (`/search/`) with live suggestions.
- **Deactivate / reactivate** — assets can be retired without losing data
  (`/deactivated/`).
- **History** (`/history/`) — automatic "previous vs now" record for every
  asset change (who, when, old value → new value).

**Workstations** (`/workstation/`)
- Dedicated Workstation module with its own field builder, bulk import, and
  rename option.
- Fields can be **Lookups** that link a workstation to other assets (CPU,
  Monitor, Keyboard, Mouse, UPS, Employee). Tick **Allow multiple** when one
  workstation can have several (e.g. Monitors, UPS units).

**Field Builders** (Super Admin)
- Add, edit, reorder, deactivate and delete fields per category (and for
  Workstations) with no code change or migration. Supported types: single-line
  text, multi-line text, dropdown, searchable dropdown, number, date, email,
  URL and lookup. Each field has width, required, and show-in-list / detail /
  add / edit options.

**Branches and access control**
- **Super Admin** (Django superuser): all branches, branch and admin
  management, field builders, import, export password, audit log.
- **Branch Admin** (staff user with branch access): sees and manages only the
  branches assigned to them. Access is checked server-side in
  `assets/permissions.py`.
- Branches are managed from the UI (`/branches/`, `/admins/`).

**Import / Export**
- **Import** (`/import/`) — `.csv`, `.txt`, `.xlsx`; preview and confirm before
  saving; up to 20,000 rows per file and `MAX_IMPORT_UPLOAD_MB` (default 10 MB).
  A sample template is at `/import/sample/`. Understands many legacy header
  spellings.
- **Export** (`/export/`) — Excel download, always password-protected. The
  Super Admin sets the password under **Manage → Export Password**
  (stored encrypted, never shown). Import uses the same password to open
  exported files. Export is disabled until a password is set.

**Audit and safety**
- **Config Audit Log** (`/config-audit/`) — records every change to categories,
  fields and field order (who, when, what changed).
- **Delete All** on the audit log is Super Admin only and asks for the
  Super Admin password. A "cleared" entry is left behind so it is always
  visible who deleted it.
- **Clear all assets** (`/clear-all/`) is destructive and only works when
  `ALLOW_CLEAR_ALL=True`. Keep it `False` in production.
- Sensitive fields (passwords, license keys) are filtered out of import and
  display by default.

## Tech stack

Python 3 · Django 5.2 · MySQL (SQLite for local dev) · openpyxl / xlrd /
msoffcrypto-tool (Excel) · cryptography · WhiteNoise (static files) · Gunicorn.
Time zone: `Asia/Kolkata`.

## Project layout

```
Assets/
├── manage.py
├── .env                   # local settings (never commit)
├── .env.example           # template: copy to .env and fill in
├── requirements.txt
├── asset_register/        # Django project (settings, urls, wsgi)
├── assets/                # main app
│   ├── models.py          # Asset, AssetField, Workstation, Branch, ConfigAuditLog ...
│   ├── views/             # split by area (see below)
│   │   ├── dashboard.py   # login, dashboard, search, history
│   │   ├── assets.py      # categories and asset add / edit / view / delete
│   │   ├── workstations.py
│   │   ├── imports.py     # Excel bulk import (assets + workstations)
│   │   ├── exports.py     # Excel export
│   │   ├── builder.py     # field builders + config audit log
│   │   ├── accounts.py    # branches, admins, my account, export password
│   │   └── common.py      # helpers shared by the files above
│   ├── urls.py, forms.py
│   ├── permissions.py     # Super Admin / Branch Admin rules
│   ├── import_utils.py    # CSV / Excel import
│   ├── excel_security.py  # export password encryption
│   ├── relations.py       # lookup relationships between assets
│   ├── management/commands/
│   ├── templates/, templatetags/, tests/
├── static/                # project static files (STATICFILES_DIRS)
└── staticfiles/           # collectstatic output (generated)
```

## Setup (local)

```bash
python -m venv venv
venv\Scripts\activate            # Linux/Mac: source venv/bin/activate
pip install -r requirements.txt

copy .env.example .env           # or create .env (see below)

python manage.py migrate
python manage.py seed_categories          # starter categories
python manage.py seed_dynamic_fields      # category fields (use --dry-run first)
python manage.py seed_workstation_fields  # workstation fields (safe to re-run)
python manage.py createsuperuser

python manage.py runserver
```

Open `http://127.0.0.1:8000/` and log in. Only staff accounts can log in.

Run tests with `python manage.py test assets`.

## Environment variables (`.env`)

| Variable | Purpose |
|---|---|
| `DJANGO_SECRET_KEY` | Required when `DJANGO_DEBUG` is off |
| `DJANGO_DEBUG` | `True` for local dev, default `False` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated origins (e.g. `https://assets.example.com`) |
| `DJANGO_HTTPS` | Secure cookies; defaults to on when debug is off. Set `False` only if serving plain HTTP |
| `DJANGO_SSL_REDIRECT` | Default `False` (the proxy already redirects) |
| `DB_ENGINE` | Set to `sqlite` to use SQLite (file: `DB_NAME_SQLITE`, default `db.sqlite3`) |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | MySQL (defaults: `asset_register`, `root`, empty, `localhost`, `3306`) |
| `ALLOW_CLEAR_ALL` | Enables `/clear-all/` (wipes all assets). Default `False` |
| `MAX_IMPORT_UPLOAD_MB` | Max import file size, default `10` |

## Deployment

Built for a reverse-proxy setup (Coolify + Traefik): HTTPS terminates at the
proxy and Django trusts `X-Forwarded-Proto`.

### Variables to set

Start from `.env.example`. With `DJANGO_DEBUG=False` (production) these are
**required**:

| Variable | Notes |
|---|---|
| `DJANGO_SECRET_KEY` | The app refuses to start without it. Generate one with `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"` |
| `DJANGO_ALLOWED_HOSTS` | Your domain(s), comma-separated. If empty, every request is rejected |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Full origin with scheme, e.g. `https://assets.example.com`. Without it, login and forms fail with a CSRF 403 behind the proxy |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | MySQL connection. Do **not** set `DB_ENGINE=sqlite` in production |

Optional (safe defaults): `DJANGO_HTTPS`, `DJANGO_SSL_REDIRECT`,
`ALLOW_CLEAR_ALL` (keep `False`), `MAX_IMPORT_UPLOAD_MB`. See the table above.

### First deploy

1. Set the variables above with `DJANGO_DEBUG=False`.
2. `python manage.py migrate`
3. `python manage.py collectstatic --noinput`
4. `python manage.py createsuperuser`
5. Optional starter data: `seed_categories`, `seed_dynamic_fields`,
   `seed_workstation_fields`.
6. Start with `gunicorn asset_register.wsgi`.
7. Log in as the Super Admin and set the export password under
   **Manage → Export Password** (export stays disabled until it is set).

### Updating

Pull the new code, then run `python manage.py migrate` and
`python manage.py collectstatic --noinput`, and restart Gunicorn.

### Backups

Take a database backup before every update and before any bulk import or
`--apply` command, for example:

```bash
mysqldump --single-transaction -u <DB_USER> -p <DB_NAME> > backup_$(date +%F).sql
```

## Bringing in old spreadsheet data

- **`/import/`** — for clean single-sheet files close to the app's column
  format. Best for day-to-day imports.
- **`manage.py import_legacy_assets /path/to/Assets.xlsx --dry-run`** — one-time
  command for the old multi-sheet tracking file. Always dry-run first and read
  the report before committing.

## Management commands

Most take `--dry-run` or `--apply`. Run the dry run first and take a database
backup before any `--apply`.

| Command | What it does |
|---|---|
| `seed_categories` | Loads the starter categories |
| `seed_dynamic_fields` | Creates field rows from the built-in category definitions |
| `seed_workstation_fields` | Creates the Workstation fields (safe to re-run) |
| `migrate_workstation_data` | Moves legacy workstation assets into the Workstation module |
| `audit_workstation_migration`, `inspect_workstation_category` | Check the workstation migration |
| `import_legacy_assets` | One-time import of the old multi-sheet workbook |
| `reroute_other_assets` | Moves "Other Asset" rows into their real category |
| `rekey_extra_details` | Moves old-keyed values onto the field-builder keys |
| `scrub_legacy_secrets` | Finds and removes plain-text secrets from imported data |
| `trim_list_columns` | Chooses which fields show in a category's list view |
| `backfill_cupboard_ids` | Fills ID / Device Name / Device Type on cupboard rows |
| `merge_project_backup_duplicates` | Merges `<tag>-2`, `<tag>-3` duplicates into `<tag>` |
| `fix_air_conditioner_fields`, `fix_air_conditioner_status`, `fix_project_details_names`, `fix_project_details_status` | One-off data clean-ups |

## Notes

- Never commit `.env`. Replace any development secret key before deploying.
- Take a database backup before bulk imports, `--apply` commands, or
  Clear All.
- Run `scrub_legacy_secrets` (dry run first) after importing old spreadsheets.
