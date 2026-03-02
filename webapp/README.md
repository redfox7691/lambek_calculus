# Lambek Web App (MVP)

This web app wraps existing project scripts with a simple HTTP API and browser UI.

## What it can run
- `generate_chords.py`
- `cadence_stats.py`
- `lambek_tree.py analyse ...`

## Setup
From project root:

```bash
python3 -m pip install -r webapp/requirements.txt
python3 webapp/backend.py
```

Open: <http://127.0.0.1:8000>

## API
- `POST /api/jobs/generate-chords`
- `POST /api/jobs/cadence-stats`
- `POST /api/jobs/analyse-standards`
- `GET /api/jobs`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/artifacts`
- `GET /api/jobs/{job_id}/download?path=<absolute_artifact_path>`

## Notes
- Jobs are executed in background threads.
- Job metadata is persisted in `webapp/job_store/*.json`.
- This is an MVP: no authentication, no DB, no queue worker yet.
