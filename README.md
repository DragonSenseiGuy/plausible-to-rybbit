# plausible-to-rybbit

Migrate your historical analytics data from **Plausible** to **Rybbit**.

Rybbit doesn't support importing from Plausible directly. This tool bridges the gap by converting your Plausible export into the format Rybbit expects via its Umami importer.

---

## How it works

```
Plausible export ZIP
        ↓
  plausible2umami.py        (converts aggregated CSVs to Umami SQL)
        ↓
  Local Umami (Docker)      (stores the data in Postgres)
        ↓
  Postgres dump             (session.csv + website_event.csv)
        ↓
  plausible_to_rybbit.py    (joins + reformats into Rybbit's expected format)
        ↓
  rybbit_import.csv
        ↓
  Rybbit → Import (Umami)
```

> **Note:** Because Plausible exports are pre-aggregated (daily totals, not individual events), the migration reconstructs sessions synthetically. Daily pageview totals and per-page counts are preserved, but cross-dimension correlations (e.g. which browser a specific user had on a specific page) are approximated.

---

## Prerequisites

- Python 3.8+
- [uv](https://github.com/astral-sh/uv) — used to run the plausible2umami converter
- [Docker](https://docs.docker.com/get-docker/) — for running Umami locally
- A running local Umami instance with at least one site created (see below)

---

## Setup

### 1. Export your data from Plausible

1. Log into Plausible → **Site Settings → Imports & Exports**
2. Click **Export to CSV**
3. Plausible will email you when it's ready — you have 24 hours to download it
4. Download the ZIP file

### 2. Run Umami locally

```bash
mkdir umami-local && cd umami-local
curl -o docker-compose.yml https://raw.githubusercontent.com/umami-software/umami/master/docker-compose.yml
docker compose up -d
```

Open http://localhost:3000 and log in with `admin` / `umami`. **Change the password immediately.**

Then go to **Settings → Websites → Add website** and create a site. You don't need to configure it further.

### 3. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Restart your terminal after installing.

---

## Usage

### Option A — Full pipeline (recommended for new migrations)

Provide your Plausible ZIP and let the script handle everything:

```bash
python3 plausible_to_rybbit.py --plausible plausible-export.zip
```

### Option B — You already have the Postgres CSVs

If you've already dumped `session.csv` and `website_event.csv` from Umami's Postgres database:

```bash
python3 plausible_to_rybbit.py --session session.csv --events website_event.csv
```

### All options

```
--plausible ZIP     Path to Plausible export ZIP (full pipeline)
--session CSV       Path to session.csv dump from Umami Postgres
--events CSV        Path to website_event.csv dump from Umami Postgres
--output CSV        Output filename (default: rybbit_import.csv)
--container NAME    Umami Postgres Docker container name (default: umami-db-1)
```

---

## Importing into Rybbit

Once the script finishes and you have `rybbit_import.csv`:

1. Open your Rybbit dashboard
2. Go to **Site Settings → Import tab**
3. Select platform: **Umami**
4. Upload `rybbit_import.csv`
5. Click **Import**
6. Monitor progress in **Import History**

> **Cloud vs self-hosted:** Data import requires a paid plan on Rybbit Cloud (Standard or Pro). If you self-host Rybbit, imports are unlimited and free.

---

## Manually dumping CSVs from Umami Postgres

If you need to dump the CSVs yourself (Option B), run these commands after importing the SQL into Umami:

```bash
# Find your Postgres container name
docker ps

# Dump the tables (replace umami-db-1 with your container name)
docker exec umami-db-1 psql -U umami -d umami \
  -c "\COPY session TO '/tmp/session.csv' CSV HEADER"

docker exec umami-db-1 psql -U umami -d umami \
  -c "\COPY website_event TO '/tmp/website_event.csv' CSV HEADER"

# Copy out of the container
docker cp umami-db-1:/tmp/session.csv ./session.csv
docker cp umami-db-1:/tmp/website_event.csv ./website_event.csv
```

---

## What gets imported

| Data | Imported? |
|---|---|
| Pageviews | ✅ |
| Custom events | ✅ |
| Browser / OS / device | ✅ |
| Country | ✅ |
| Referrer domain | ✅ |
| URL path | ✅ |
| UTM parameters | ❌ (not in Plausible aggregated export) |
| Visit duration / bounce rate | ❌ (not in Plausible aggregated export) |
| Page titles | ❌ (not in Plausible aggregated export) |
| Exact session-level correlations | ❌ (reconstructed synthetically) |

---

## Troubleshooting

**Import shows 0 events in Rybbit**
- Make sure you're uploading the file produced by this script (`rybbit_import.csv`), not the raw Postgres dumps
- Check that your Rybbit plan supports imports (free tier does not on cloud)

**"Could not find a website UUID" error**
- You need to create at least one website in Umami before running the full pipeline

**plausible2umami fails**
- Make sure `uv` is installed and on your PATH (`uv --version`)
- Make sure your Plausible ZIP contains files named like `imported_pages_*.csv` and `imported_visitors_*.csv`

**Docker container not found**
- Run `docker ps` to find the exact container name and pass it with `--container your-container-name`

---

## Credits

- [plausible-to-umami](https://github.com/JeongJuhyeon/plausible-to-umami) by JeongJuhyeon — converts Plausible CSVs to Umami SQL
- [Umami](https://umami.is) — open source analytics
- [Rybbit](https://rybbit.com) — open source analytics
