# Real Estate Broker Agent

AI-powered backend agent for property discovery and visit orchestration over WhatsApp.

## Documentation

- Project setup and architecture: `PROJECT_INSTRUCTIONS.md`
- Deployment guide (Render): `DEPLOYMENT_INSTRUCTIONS.md`
- Original product requirements: `technical-specification.md`

## Run Locally

```bash
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
python gradio_ui.py 
```

## Run Local Tests

```bash
./scripts/run_local_tests.sh
```

## Refresh Inventory

Refresh a location into the property DB with cleaning, deduplication, enrichment, and score updates:

```bash
python3 scripts/refresh_inventory.py --location Airoli
```

Notes:
- The refresher upgrades the SQLite schema if needed.
- It refreshes inventory from site adapters and search fallbacks, including NoBroker coverage.
- It updates `freshness_score`, `fitness_score`, verification state, and source payload metadata.

## Owner Upload Via Chat

Owners can now submit listings through the chatbot. Example flow:

```text
I am an owner and want to list my property in Airoli
It is a 2 BHK for rent at 42000
```

The service stores the listing in `properties` with source `owner_upload` and pending verification.

## Cloud Photo Backfill

Add your storage settings in `.env` with the existing repo keys:

```bash
SUPERBASE_API_KEY="..."
SUPERBASE_URL="https://<project>.supabase.co"
SUPERBASE_STORAGE_URL="https://<project>.supabase.co/storage/v1"
SUPERBASE_BUCKET="property-images"
SUPERBASE_PROJECT_ID="..."
```

Then backfill property photos and folder references into the DB:

```bash
python3 scripts/backfill_property_photos.py
```

This script:
- adds `photo_storage_folder` to `properties` if missing
- uploads discovered property images into `properties/<property_id>/...`
- stores the folder path on the property row
- stores uploaded object paths in `metadata.cloud_photo_paths`
