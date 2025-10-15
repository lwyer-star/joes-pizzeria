# Joe’s Pizzeria — ICT320 Task 3
**Author:** Liam Wyer  
**Date:** 2025-10-14

## Overview
This proof-of-concept modernises “Little Joe’s” (one store) with:
- **Cosmos DB (NoSQL)** for operational data: `orders`, `dockets`, `customers`, `drivers`.
- **Azure SQL (Relational)** for **daily summaries** (reporting/aggregation).  
- **Python app**: Flask web UI (`web.py`) + Admin CLI (`app.py`) with a shared data layer (`db.py`).  
- **Dockets** are separate documents (not embedded in orders) to keep order writes simple and allow different output formats (text/PDF).

## Project Structure
```
.
├─ web.py          # Flask web UI (routes/templates)
├─ db.py           # Data abstraction (Cosmos + Azure SQL)
├─ app.py          # CLI (drivers/customers/orders; summaries; seeding; PDF)
├─ templates/      # HTML templates
├─ sql/
│  └─ create_daily_summary.sql
├─ requirements.txt
├─ README.md
└─ .env.example    # sample; do NOT commit real secrets
```

## Prerequisites
- Python **3.11+** (Windows/macOS/Linux)  
- Azure Free Tier resources:
  - **Azure Cosmos DB** (Core (SQL) API)
  - **Azure SQL Database** on a SQL **Server**

## Setup

### 1) Create and activate a virtual environment
```powershell
py -m venv .venv
. .venv\Scripts\Activate.ps1    # PowerShell
# or: .venv\Scripts\activate     # CMD
```

### 2) Install dependencies
```powershell
pip install -r requirements.txt
```

`requirements.txt`:
```
Flask
python-dotenv
azure-cosmos
pyodbc
reportlab
```

### 3) Configure environment (`.env`)
Create a file named **`.env`** in the project root (no quotes around values):

```
COSMOS_ENDPOINT=https://<your-account>.documents.azure.com:443/
COSMOS_KEY=<your-cosmos-key>
COSMOS_DB=joes

SQL_SERVER=sql-joes-liam.database.windows.net
SQL_DATABASE=joes_ops
SQL_USER=sqladminliam
SQL_PASSWORD=<your-password>
ODBC_DRIVER=ODBC Driver 18 for SQL Server

# stable session key for Flask
SECRET_KEY=<output of: python -c "import secrets; print(secrets.token_hex(32))">
```

> The code calls `load_dotenv(override=True)` and sanitises the ODBC value, so `.env` wins over any stale OS env var.

### 4) Azure SQL firewall (for marking)
On your **SQL server** (not the database): **Networking**  
- Public network access: **Enabled**  
- Allow Azure services and resources: **On**  
- Firewall rule:  
  - Start IP: `0.0.0.0`  
  - End IP: `255.255.255.255`  
> After marking, tighten/remove this rule.

## Running

### Web UI (primary)
```powershell
python web.py
# open http://127.0.0.1:5000
```

Key pages:
- `/drivers`, `/customers`, `/orders`
- `/new-order` (creates order + docket)
- `/docket` (view by order id), `/docket/<order_id>/pdf`
- `/generate-summary` → writes to SQL
- `/summaries` → reads from SQL

### Admin CLI (optional)
```powershell
python app.py
```
Menu includes listing/adding drivers/customers, creating orders (with docket), exporting docket PDFs, generating/saving daily summaries, and Cosmos wipe/seed helpers.

---

## Database Design

### NoSQL (Cosmos DB)
**Containers:** `drivers`, `customers`, `orders`, `dockets`  
**Rationale:** Document model matches order + line-items; easy to append orders; flexible dockets; avoids strict schema while the store iterates.

- **Order** (snapshot of commission rate for historical accuracy; includes `storeId` for future multi-store):
```json
{
  "id": "ord_20251014_193522",
  "orderDate": "2025-10-14",
  "createdAt": "2025-10-14T19:35:22.123Z",
  "customerId": "cus_01",
  "items": [
    {"sku":"MARG","name":"Margherita","qty":2,"unitPrice":14.00}
  ],
  "total": 28.00,
  "driverId": "drv_02",
  "driverCommissionRate": 0.10,
  "storeId": "little-joes"
}
```

- **Docket** (separate doc)
```json
{
  "id": "dkt_20251014_193522",
  "orderId": "ord_20251014_193522",
  "rendered": "Little Joe's Delivery Docket\\n...",
  "createdAt": "2025-10-14T19:35:22.456Z",
  "storeId": "little-joes"
}
```

- **Driver**
```json
{"id":"drv_01","name":"Ava","suburbs":["Maroochydore","Buderim"],"commissionRate":0.10}
```

### SQL (Azure SQL) — Reporting
**Table:** `dbo.daily_summary` (one row per date; prevents duplicates; supports downstream BI)

```sql
CREATE TABLE dbo.daily_summary (
  summary_date            date          NOT NULL PRIMARY KEY,
  total_orders            int           NOT NULL DEFAULT (0),
  total_revenue           decimal(12,2) NOT NULL DEFAULT (0),
  most_popular_pizza      nvarchar(100) NULL,
  total_driver_commission decimal(12,2) NOT NULL DEFAULT (0)
);
```

**Rationale:** Relational storage suits daily aggregates, indexing on date, and external reporting tools.

---

## System Architecture
- **web.py** (Flask): routes, forms, HTML rendering, flash messages, PDF download endpoint.
- **db.py**: all data access.  
  - Cosmos SDK for operational reads/writes.  
  - `generate_and_save_summary(date)` computes **Cosmos aggregates** (COUNT/SUM), calculates **most popular pizza** in a **cross-partition-safe** way (distinct names + per-name `SELECT VALUE COUNT(1)`), then **MERGE** upserts into SQL.
- **app.py**: same data layer via CLI for admin tasks and seeding.  
- **Config**: `.env` via `python-dotenv`; ODBC 18 driver; TLS enforced.

---

## Functional Requirements Mapping
- **Order Processing**: `/new-order` (web) or CLI option — saves order + total in Cosmos.
- **Docket Creation**: created alongside order; `/docket` + `/docket/<id>/pdf`.
- **Driver Management**: `drivers` container; list/add via web/CLI; has name/suburbs/commission rate.
- **Daily Summaries**: `/generate-summary` writes to SQL with **no duplicates** (MERGE); `/summaries` prints on request.

---

## Testing (what the marker can do)
1. **Drivers**: ensure ≥3 drivers exist (`/drivers` or CLI “List drivers”).
2. **Customers**: ensure some customers exist (`/customers`).
3. **Create order**: go to `/new-order` (use a valid `cus_*`), add items (e.g., 2×MARG, 1×PEPP), submit.  
   - Check `/orders` shows it.  
   - Check `/docket` and download the PDF.
4. **Generate summary**: `/generate-summary` for a date with orders; then view `/summaries`.  
   - Re-run the same date: row **updates** (no duplicate).
5. **Error handling**: generate summary on a date with no orders; SQL row has zeros and `(None)`.

**Screenshots to include in the report**:
- `/drivers` (≥3 drivers), `/customers`
- `/orders` with new order
- `/docket` + PDF download
- `/generate-summary` success flash
- `/summaries` with entries
- Azure Portal: SQL **server** → **Networking** (public enabled + firewall rule)

---

## Troubleshooting
- **IM002: Data source name not found**  
  Install the MS driver and set:
  ```
  ODBC_DRIVER=ODBC Driver 18 for SQL Server
  ```
  Print installed drivers in a REPL:
  ```python
  import pyodbc; print(pyodbc.drivers())
  ```

- **08001 / Timeout (258)**  
  SQL **server** firewall not open or network blocks 1433.  
  Enable public access, add `0.0.0.0–255.255.255.255`, allow Azure services, save.  
  Quick reachability test:
  ```powershell
  Test-NetConnection sql-joes-liam.database.windows.net -Port 1433
  ```

- **Cosmos ORDER BY errors**  
  Cross-partition `GROUP BY ... ORDER BY COUNT(1)` isn’t supported. Code uses **distinct names + per-name aggregate** to stay compliant.

---

## Discussion & Decisions
- **NoSQL for ops**: orders/dockets evolve; denormalised line items; low write friction; simple point reads.
- **SQL for summaries**: stable schema, date PK, BI friendliness, easy to index/report.
- **Snapshot commission rate**: historical correctness if driver rates change.
- **Scalability**: `storeId` allows multi-store filtering/partitioning later.
- **Security**: `.env` for secrets; TLS; stable `SECRET_KEY`. (For marking only, SQL firewall is intentionally wide.)

---

## AI Acknowledgement
I acknowledge the use of **ChatGPT** as a co-pilot. I used it to: (a) refactor environment-based configuration and ODBC driver handling, (b) resolve Cosmos cross-partition aggregate queries, (c) troubleshoot Azure SQL connectivity and firewall settings, and (d) draft this README structure and testing checklist. I verified and tested all code and documentation.

---

## Submission Hygiene
Include:
- `web.py`, `db.py`, `app.py`, `templates/`, `sql/create_daily_summary.sql`, `requirements.txt`, `README.md`, `.env.example`  
Exclude:
- `.env`, `.venv/`, `__pycache__/`, large caches.
