# db.py — Data Abstraction Layer for Little Joe's Pizzeria
import os
import random
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from azure.cosmos import CosmosClient
from collections import Counter  # i’m leaving this import in case i want quick local checks, but i don’t use it for aggregates
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from dotenv import load_dotenv

# --- Configuration ---
# i load all secrets from .env so i’m not hardcoding keys anywhere
# if something is missing, i log a warning below so i see it early
load_dotenv(override=True)  # i override to ensure my .env beats any stale system env

# Cosmos DB (NoSQL)
COSMOS_ENDPOINT = os.getenv("COSMOS_ENDPOINT")
COSMOS_KEY      = os.getenv("COSMOS_KEY")
COSMOS_DB       = os.getenv("COSMOS_DB")

# Azure SQL (Relational)
SQL_SERVER   = os.getenv("SQL_SERVER")
SQL_DATABASE = os.getenv("SQL_DATABASE")
SQL_USER     = os.getenv("SQL_USER")
SQL_PASSWORD = os.getenv("SQL_PASSWORD")
# i default to ODBC Driver 18 so i get modern TLS settings without surprises
ODBC_DRIVER  = os.getenv("ODBC_DRIVER") or "ODBC Driver 18 for SQL Server"

# if any env var is missing, i warn myself here (i still try to start so i can see the message in the console)
_missing = [k for k in ["COSMOS_ENDPOINT","COSMOS_KEY","COSMOS_DB","SQL_SERVER","SQL_DATABASE","SQL_USER","SQL_PASSWORD"] if not globals().get(k)]
if _missing:
    print(f"WARNING: Missing required env vars: {', '.join(_missing)}")

# --- Cosmos DB Client and Container Handles ---
# i open clients once at import time; if this fails, i null the handles so my functions can early-out gracefully
try:
    _client = CosmosClient(COSMOS_ENDPOINT, COSMOS_KEY)
    _db     = _client.get_database_client(COSMOS_DB)
    # i’m keeping one logical container per entity because it matches how i query in the web app
    drivers_c   = _db.get_container_client("drivers")
    customers_c = _db.get_container_client("customers")
    orders_c    = _db.get_container_client("orders")
    dockets_c   = _db.get_container_client("dockets")
except Exception as e:
    print(f"FATAL: Could not connect to Cosmos DB. Check credentials. Error: {e}")
    drivers_c = customers_c = orders_c = dockets_c = None

# --- NoSQL (Cosmos DB) Functions ---
def get_drivers() -> List[Dict[str, Any]]:
    # if cosmos isn’t available i just return empty so the UI doesn’t crash
    if not drivers_c: return []
    # i’m selecting only the fields i actually render in the template
    q = "SELECT c.id, c.name, c.suburbs, c.commissionRate FROM c"
    return list(drivers_c.query_items(q, enable_cross_partition_query=True))

def list_customers() -> List[Dict[str, Any]]:
    if not customers_c: return []
    # i keep this light so i don’t pay RU to pull large docs i don’t show
    q = "SELECT c.id, c.name, c.email, c.phone, c.suburb FROM c"
    return [dict(r) for r in customers_c.query_items(q, enable_cross_partition_query=True)]

def list_orders(limit: int = 50) -> List[Dict[str, Any]]:
    if not orders_c: return []
    # i order by createdAt so my UI shows freshest orders first
    q = f"SELECT TOP {int(limit)} c.id, c.orderDate, c.total, c.customerId, c.driverId, c.createdAt FROM c ORDER BY c.createdAt DESC"
    return [dict(r) for r in orders_c.query_items(q, enable_cross_partition_query=True)]

def get_docket_by_order(order_id: str) -> Optional[Dict[str, Any]]:
    if not dockets_c or not order_id: return None
    # i store dockets as separate docs linked by orderId, so this lookup is cheap and clean
    q = "SELECT * FROM c WHERE c.orderId = @oid"
    params = [{"name": "@oid", "value": order_id}]
    items = list(dockets_c.query_items(q, parameters=params, enable_cross_partition_query=True))
    return dict(items[0]) if items else None

# i use this in web.py instead of a Mongo-style find_one
def get_customer_by_id(customer_id: str) -> Optional[Dict[str, Any]]:
    if not customers_c or not customer_id: return None
    q = "SELECT * FROM c WHERE c.id = @id"
    params = [{"name": "@id", "value": customer_id}]
    items = list(customers_c.query_items(q, parameters=params, enable_cross_partition_query=True))
    return dict(items[0]) if items else None

def create_order_and_docket(customer, items, total, driver):
    # i return (order_doc, docket_doc) so the caller can toast/redirect with the id
    if not orders_c or not dockets_c: return None, None
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    order_id = f"ord_{now.strftime('%Y%m%d_%H%M%S')}"
    
    # i snapshot the driver’s commission rate into the order so history doesn’t change if i edit the driver later
    order_doc = {
        "id": order_id, "orderDate": date_str, "createdAt": now.isoformat(),
        "customerId": customer["id"], "items": items, "total": total,
        "driverId": driver["id"],
        "driverCommissionRate": float(driver.get("commissionRate", 0.10)),
        "storeId": "little-joes"  # i keep a store id here so i can scale to multi-store later
    }
    orders_c.upsert_item(order_doc)
    
    # i keep the docket separate to match the brief (and to avoid rewriting orders if i tweak formats)
    docket_id = f"dkt_{order_id[4:]}"
    rendered_text = (
        f"Little Joe's Delivery Docket\n----------------------------\n"
        f"Order ID: {order_id}\nDate: {date_str}\n"
        f"Customer: {customer.get('name')} ({customer.get('suburb')})\n"
        f"Driver: {driver.get('name')}\n\nItems:\n" +
        "\n".join([f"  - {i['qty']}x {i['name']} (${i['unitPrice']:.2f})" for i in items]) +
        f"\n\nTotal: ${total:.2f}"
    )
    docket_doc = {
        "id": docket_id, "orderId": order_id, "rendered": rendered_text,
        "createdAt": now.isoformat(), "storeId": "little-joes"
    }
    dockets_c.upsert_item(docket_doc)
    
    return order_doc, docket_doc

def generate_docket_pdf(docket_text: str) -> BytesIO:
    # i stream the PDF from memory so i don’t write temp files on disk
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 50
    for line in docket_text.splitlines():
        p.drawString(50, y, line)
        y -= 16
        if y < 50:
            p.showPage()
            y = height - 50
    p.showPage()
    p.save()
    buffer.seek(0)
    return buffer

# --- SQL (Azure SQL) Functions ---
def _get_sql_conn_str():
    # i sanitize the driver in case the env accidentally includes 'ODBC_DRIVER=' again
    drv = (ODBC_DRIVER or "ODBC Driver 18 for SQL Server").replace("ODBC_DRIVER=", "").strip().strip('"').strip("'")
    # i use tcp and the explicit 1433 port so the driver doesn’t guess
    # i keep Encrypt=yes;TrustServerCertificate=no to match Azure’s expectations
    return (
        f"Driver={{{drv}}};"
        f"Server=tcp:{SQL_SERVER},1433;"
        f"Database={SQL_DATABASE};"
        f"Uid={SQL_USER};Pwd={SQL_PASSWORD};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )


def get_sql_summaries() -> List[Dict[str, Any]]:
    try:
        import pyodbc
    except ImportError:
        print("FATAL: The 'pyodbc' library is not installed.")
        return []

    summaries = []
    try:
        # i keep a short timeout so the UI doesn’t hang if SQL is unreachable
        with pyodbc.connect(_get_sql_conn_str(), timeout=5) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM dbo.daily_summary ORDER BY summary_date DESC;")
            columns = [column[0] for column in cursor.description]
            for row in cursor.fetchall():
                summaries.append(dict(zip(columns, row)))
    except pyodbc.Error as e:
        print(f"SQL CONNECTION ERROR: {e}")
        return []
    except Exception as e:
        print(f"An unexpected SQL Error occurred: {e}")
        return []
    
    return summaries

def generate_and_save_summary(date_str: str) -> bool:
    # i use Cosmos-side aggregates (no python loops for sums/counts) and only pick the top name client-side
    if not orders_c:
        print("generate_and_save_summary: orders_c is None (Cosmos not connected)")
        return False

    params = [{"name": "@d", "value": date_str}]
    try:
        # total orders
        total_orders = next(
            orders_c.query_items(
                "SELECT VALUE COUNT(1) FROM c WHERE c.orderDate = @d",
                parameters=params,
                enable_cross_partition_query=True
            ),
            0
        )
        # total revenue
        total_revenue = next(
            orders_c.query_items(
                "SELECT VALUE SUM(c.total) FROM c WHERE c.orderDate = @d",
                parameters=params,
                enable_cross_partition_query=True
            ),
            0.0
        ) or 0.0
        # total driver commission (snapshot rate saved on each order)
        total_comm = next(
            orders_c.query_items(
                "SELECT VALUE SUM(c.total * c.driverCommissionRate) FROM c WHERE c.orderDate = @d",
                parameters=params,
                enable_cross_partition_query=True
            ),
            0.0
        ) or 0.0

        # most popular pizza — cross-partition safe approach:
        # step 1: get distinct pizza names for that date (no aggregate yet)
        q_names = (
            "SELECT DISTINCT VALUE i.name "
            "FROM c JOIN i IN c.items "
            "WHERE c.orderDate = @d"
        )
        names = list(orders_c.query_items(q_names, parameters=params, enable_cross_partition_query=True))

        # step 2: for each name, run SELECT VALUE COUNT(1) (allowed cross-partition) and keep the max
        top_name, top_count = "(None)", -1
        q_count = (
            "SELECT VALUE COUNT(1) "
            "FROM c JOIN i IN c.items "
            "WHERE c.orderDate = @d AND i.name = @n"
        )
        for n in names:
            cnt = next(
                orders_c.query_items(
                    q_count,
                    parameters=[{"name": "@d", "value": date_str}, {"name": "@n", "value": n}],
                    enable_cross_partition_query=True,
                ),
                0,
            )
            if cnt > top_count:
                top_name, top_count = n, cnt
        most_popular = top_name

        print(f"[Cosmos] date={date_str} -> orders={total_orders} revenue={total_revenue:.2f} comm={total_comm:.2f} popular='{most_popular}' (cnt={top_count})")

    except Exception as e:
        print(f"[Cosmos] aggregate error for date {date_str}: {e}")
        return False

    # --- write to Azure SQL (MERGE upsert) ---
    try:
        import pyodbc
    except ImportError:
        print("FATAL: 'pyodbc' is not installed.")
        return False

    # helpful debug (mask password)
    try:
        dbg = _get_sql_conn_str().replace(SQL_PASSWORD or "", "***")
        print("[SQL] connecting with:", dbg)
    except Exception:
        pass

    try:
        with pyodbc.connect(_get_sql_conn_str(), timeout=5) as conn:
            cursor = conn.cursor()
            merge_sql = """
            MERGE dbo.daily_summary AS tgt
            USING (SELECT CAST(? AS date) AS summary_date) AS src ON (tgt.summary_date = src.summary_date)
            WHEN MATCHED THEN UPDATE SET total_orders=?, total_revenue=?, most_popular_pizza=?, total_driver_commission=?
            WHEN NOT MATCHED THEN INSERT (summary_date, total_orders, total_revenue, most_popular_pizza, total_driver_commission)
            VALUES (src.summary_date, ?, ?, ?, ?);
            """
            params_for_sql = (
                date_str, total_orders, total_revenue, most_popular, total_comm,
                total_orders, total_revenue, most_popular, total_comm
            )
            cursor.execute(merge_sql, params_for_sql)
            conn.commit()
        print(f"[SQL] upserted summary for {date_str}")
        return True
    except Exception as e:
        print(f"[SQL] error saving summary for {date_str}: {e}")
        return False
