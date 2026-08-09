# Accounting Receipt Demo

A small modular monolith that turns receipt images into reviewable accounting
records. The modules remain separated by responsibility, but the public demo
runs as one FastAPI process and stores all application/job state in one local
SQLite file.

The review flow uses fuzzy-assisted store and item matching to improve reuse as
more confirmed receipts are added.

## Structure

- `main.py` — the single public FastAPI entrypoint
- `orchestra/workflow.py` — receipt queue, lifecycle, and background-worker orchestration
- `auth_demo/` — mock login page and signed-session helper
- `backend/app/` — receipt processing, accounting rules, and SQLite data access
- `orchestra/app/` — persistent job/duty data access
- `category_helper/` — continuously triggered item categorization worker, taxonomy, and LLM adapters
- `frontend_receipt_model/template/` — dashboard, drilldown, receipt review, and manual category pages

The only HTTP boundary inside the receipt flow is the replaceable receipt-reader
adapter. Backend modules otherwise call each other directly in Python; there
are no internal API tokens.

## Run

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`, enter any positive user ID, and use `demo` as the
default credential. The user ID selects the data partition for this mockup.

The web application runs without a `.env` file. Automatic item categorization
needs credentials for the selected LLM provider. Either export the variables in
your process environment or create an ignored local file from the public
template:

```bash
cp category_helper/.env.example category_helper/.env
# Edit category_helper/.env and set the selected provider's API key.
```

Never commit `category_helper/.env`. The repository only includes
`category_helper/.env.example`, which contains names and safe defaults but no
credentials.

## Configuration

SQLite is created at `data/accounting.sqlite3`; uploads are temporary files in
`data/uploads/`. Both are ignored by Git. Override them with
`ACCOUNTING_DB_PATH` and `RECEIPT_UPLOAD_DIR` if needed.

Application environment variables:

- `DEMO_CREDENTIAL` — shared demo credential (default `demo`)
- `SESSION_SECRET` — stable signing secret for session cookies (otherwise a random secret is made at startup)
- `SESSION_MAX_AGE` — session cookie lifetime in seconds (default `86400`)
- `ACCOUNTING_DB_PATH` — SQLite database path (default `data/accounting.sqlite3`)
- `RECEIPT_UPLOAD_DIR` — temporary upload directory (default `data/uploads`)
- `RECEIPT_READER_CONFIG` — receipt-reader JSON config path (default `backend/config/receipt_reader.json`)
- `RECEIPT_WORKER_COUNT` — concurrent in-process receipt workers (default `2`)
- `RECEIPT_IN_PROCESS_LIMIT` — active receipt limit per user (default `5`)
- `HOUSEKEEPER_INTERVAL_SEC` — background cleanup interval (default `60`)
- `CATEGORY_ENV_FILE` — optional category-worker env-file path (default `category_helper/.env`)

LLM provider, API key, model, generation, and category polling settings are
documented in [`category_helper/.env.example`](category_helper/.env.example).
Values already exported by the process take precedence over the local env file.

This login is intentionally a mock. Before a real deployment, replace it with
a real identity provider, set a strong session secret, enable secure cookies,
and run behind HTTPS.

## Receipt reader

This application was built to run with
[`watztp/japanese_receipt_reader`](https://github.com/watztp/japanese_receipt_reader),
a Japanese receipt-reading pipeline that uses YOLO to detect the receipt and a
fine-tuned Donut model to extract its contents. Its endpoint and multipart settings live in
[`backend/config/receipt_reader.json`](backend/config/receipt_reader.json), not
in Python source code:

```json
{
  "type": "http_donut",
  "http": {
    "url": "http://localhost:9001/infer",
    "file_field": "file",
    "timeout_seconds": 60,
    "connect_timeout_seconds": 10
  }
}
```

The server sends a JPEG multipart upload and expects a JSON object containing a
Donut string under `text` (or `raw`). The tags parsed by
`backend/app/engine/postprocess.py` look like this:

```json
{
  "text": "<store><store_nm>Demo Mart</store_nm><branch>Main</branch><addr>Tokyo</addr><datetime>2026-08-09</datetime></store><item><nm>Milk</nm><cnt>2</cnt><unitprice>100</unitprice><price>200</price><discount_amount>0</discount_amount><sep/><nm>Bread</nm><cnt>1</cnt><unitprice>150</unitprice><price>150</price></item><summary><menuqty_cnt>3</menuqty_cnt><subtotal_price>350</subtotal_price><total_price>350</total_price><pay_method>cash</pay_method><paidamount>500</paidamount><changeprice>150</changeprice></summary>"
}
```

There are two ways to use another receipt engine:

1. Put its API URL in `receipt_reader.json` and adapt its response to the Donut
   JSON contract above. Nothing else in the application needs to change.
2. Use a custom Python pipeline by changing the config to:

```json
{
  "type": "python_callable",
  "callable": "my_receipt_pipeline.reader:read_receipt",
  "options": {"device": "cpu"}
}
```

The callable receives `image_bytes` and `options`, may be synchronous or async,
and must return a dictionary containing either the Donut `text` value or the
already normalized `receipt_main_info`, `items`, and `summary` fields. To use a
different config location, set `RECEIPT_READER_CONFIG` to that JSON file.

## Automatic category worker

The category watcher starts with the web server. It polls SQLite for items whose
`category_id` is empty and whose `needs_review` flag is false. On startup it
checks that the selected provider, model, and API key are configured. Temporary
provider failures are retried without stopping the receipt server. Each
classification prompt includes the item name and the name of the most recent
store associated with that item when receipt data provides one.

If provider configuration or credentials are missing, invalid, or rejected,
the worker stops automatic categorization and enables **Manual assignment
mode** for the rest of that server process. In this mode, **Manage Category**
shows every uncategorized item in addition to items marked `needs_review`.
Restart the server after fixing `.env` to re-enable automatic categorization;
this process-level lock prevents manual saves from racing the automatic worker.

If the provider cannot select a valid category and subcategory from
`category_helper/utils/item_cat.json`, the worker sets `needs_review`, skips that
item, and continues processing the batch. It does not retry unresolved items in
a loop. Use **Manage Category** next to **Upload** on the dashboard to assign a
category manually. Manual assignments are validated against the same taxonomy.

- `CATEGORY_POLL_SECONDS` — idle database polling interval (default `2`)
- `CATEGORY_RETRY_SECONDS` — delay after an error (default `10`)
- `CATEGORY_BATCH_SIZE` — items handled per pass (default `10`)

Its current state and last warning are visible at `/health` under
`category_worker`.

The integrated web server starts this worker automatically. For isolated worker
operation against the same configured database, run:

```bash
.venv/bin/python -m category_helper.main
```
