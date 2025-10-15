# app.py — Command-Line Interface (CLI) for Little Joe's Pizzeria
# This file is my original Python application for managing the pizzeria's
# data. It provides a menu-driven interface for administrative tasks like
# adding drivers, seeding the database with test data, and running daily
# summaries. I've kept it as a separate tool from the web app.

import sys, os, random
from datetime import datetime, timezone, date
from collections import Counter  # i’m leaving this import handy for local checks, but i don’t use it for aggregates now
from dotenv import load_dotenv

# --- Configuration ---
# i load all secrets securely from a .env file so i’m not hardcoding credentials
load_dotenv(override=True)  # i override so my .env wins over any stale system env

# Cosmos DB (NoSQL)
COSMOS_ENDPOINT = os.getenv("COSMOS_ENDPOINT")
COSMOS_KEY      = os.getenv("COSMOS_KEY")
COSMOS_DB       = os.getenv("COSMOS_DB", "joes")

# Azure SQL (Relational)
SQL_SERVER   = os.getenv("SQL_SERVER")                     # e.g. myserver.database.windows.net
SQL_DATABASE = os.getenv("SQL_DATABASE")
SQL_USER     = os.getenv("SQL_USER")
SQL_PASSWORD = os.getenv("SQL_PASSWORD")
# i default to ODBC 18 to avoid TLS/attr surprises
ODBC_DRIVER  = os.getenv("ODBC_DRIVER") or "ODBC Driver 18 for SQL Server"

print(">>> joe_app v9 (env + Cosmos aggregates + PDF) <<<")

# --- Prerequisite Checks ---
# i keep this to help me install missing libs quickly
def _ensure_imports():
    """Checks if all required libraries are installed."""
    try:
        import azure.cosmos
        import pyodbc
        import reportlab
        return True
    except ImportError as e:
        print(f"\nERROR: A required library is missing: {e.name}.")
        print(f"Please run 'pip install {e.name}' in your terminal.")
        return False

if not _ensure_imports():
    sys.exit(1)

from azure.cosmos import CosmosClient
import pyodbc
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

# fail-fast so i see clear messages when an env var is missing
_missing = [k for k in ["COSMOS_ENDPOINT","COSMOS_KEY","COSMOS_DB","SQL_SERVER","SQL_DATABASE","SQL_USER","SQL_PASSWORD"] if not globals().get(k)]
if _missing:
    print(f"WARNING: Missing required env vars: {', '.join(_missing)}")

# ---------- Cosmos DB Wiring ----------
# i initialize these clients once at the start for efficiency
_client = CosmosClient(COSMOS_ENDPOINT, COSMOS_KEY)
_db     = _client.get_database_client(COSMOS_DB)
drivers_c   = _db.get_container_client("drivers")
customers_c = _db.get_container_client("customers")
orders_c    = _db.get_container_client("orders")
dockets_c   = _db.get_container_client("dockets")

# ---------- Helper Functions ----------

def _conn_str():
    """Helper to build the SQL connection string."""
    # i sanitize the driver so a bad env var can't break the DSN (IM002)
    drv = (ODBC_DRIVER or "ODBC Driver 18 for SQL Server").replace("ODBC_DRIVER=", "").strip().strip('"').strip("'")
    # i use tcp and explicit port 1433; keep Encrypt/TrustServerCertificate as Azure expects
    return (
        f"Driver={{{drv}}};"
        f"Server=tcp:{SQL_SERVER},1433;"
        f"Database={SQL_DATABASE};"
        f"Uid={SQL_USER};Pwd={SQL_PASSWORD};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;"
    )


def _choose_driver(drivers: list[dict], suburb: str | None):
    """Selects a driver, prioritizing those who serve the given suburb."""
    if suburb:
        pool = [d for d in drivers if suburb in (d.get("suburbs") or [])]
        if pool: return random.choice(pool)
    return random.choice(drivers) if drivers else None

def _calc_total(items: list[dict]) -> float:
    """Calculates the total price for a list of order items."""
    return round(sum(i["qty"] * i["unitPrice"] for i in items), 2)

def _ensure_dir(path: str):
    """Creates a directory if it doesn't already exist."""
    os.makedirs(path, exist_ok=True)

def _input_items() -> list[dict]:
    """Interactive prompt for adding pizza items to an order."""
    MENU = {
        "MARG": {"name":"Margherita","price":14.00},
        "PEPP": {"name":"Pepperoni","price":17.00},
        "SUPR": {"name":"Super Supreme","price":19.50},
    }
    print("\nMenu:")
    for k,v in MENU.items(): print(f"  {k}: {v['name']} ${v['price']:.2f}")
    items=[]
    while True:
        sku = input("Add item SKU (blank to finish): ").strip().upper()
        if not sku: break
        if sku not in MENU: print("  Unknown SKU (MARG/PEPP/SUPR)."); continue
        try:
            qty = int(input("  qty: ").strip()); assert qty>0
        except Exception: print("  qty must be a positive integer."); continue
        items.append({"sku":sku,"name":MENU[sku]["name"],"qty":qty,"unitPrice":float(MENU[sku]["price"])})
    return items

# ---------- Drivers ----------
def list_drivers():
    rows = list(drivers_c.query_items("SELECT c.id, c.name, c.suburbs, c.commissionRate FROM c", enable_cross_partition_query=True))
    if not rows: print("No drivers found.\n"); return
    print("\nDrivers:")
    for r in rows:
        suburbs = ", ".join(r.get("suburbs") or [])
        print(f"- {r.get('id')} | {r.get('name')} | {suburbs} | rate={r.get('commissionRate')}")
    print()

def add_driver():
    did = input("Driver id (e.g., drv_01): ").strip()
    name = input("Name: ").strip()
    suburbs = [s.strip() for s in input("Suburbs (comma-separated): ").split(",") if s.strip()]
    rate_txt = input("Commission rate (0-1, blank=0.10): ").strip()
    try: rate = 0.10 if not rate_txt else float(rate_txt); assert 0<=rate<=1
    except Exception: print("rate must be 0..1\n"); return
    if not did or not name: print("id and name required.\n"); return
    drivers_c.upsert_item({"id":did,"name":name,"suburbs":suburbs,"commissionRate":rate})
    print("Saved.\n")

# ---------- Customers ----------
def list_customers():
    rows = list(customers_c.query_items("SELECT c.id, c.name, c.email, c.suburb FROM c", enable_cross_partition_query=True))
    if not rows: print("No customers yet.\n"); return
    print("\nCustomers:")
    for r in rows: print(f"- {r.get('id')} | {r.get('name')} | {r.get('email')} | {r.get('suburb')}")
    print()

def add_customer():
    cid=input("Customer id (e.g., cus_01): ").strip()
    name=input("Name: ").strip()
    email=input("Email: ").strip()
    phone=input("Phone (optional): ").strip()
    suburb=input("Suburb: ").strip()
    if not cid or not name or not email or not suburb: print("id/name/email/suburb required.\n"); return
    customers_c.upsert_item({"id":cid,"name":name,"email":email,"phone":phone,"suburb":suburb})
    print("Saved.\n")

# ---------- Orders + Dockets ----------
def create_order():
    """Guides the user through creating a new order and docket."""
    print("\n== New Order ==")
    oid = input("Order id (e.g., ord_1001): ").strip()
    cid = input("Existing customer id (e.g., cus_01): ").strip()
    if not oid or not cid: print("order id and customer id required.\n"); return

    cust = next(customers_c.query_items("SELECT * FROM c WHERE c.id = @id", parameters=[{"name":"@id","value":cid}], enable_cross_partition_query=True), None)
    if not cust: print("No such customer.\n"); return

    items = _input_items()
    if not items: print("No items added.\n"); return
    total = _calc_total(items)

    drv_list = list(drivers_c.query_items("SELECT * FROM c", enable_cross_partition_query=True))
    if not drv_list: print("No drivers available.\n"); return
    drv = _choose_driver(drv_list, cust.get("suburb"))

    now = datetime.now(timezone.utc)
    order_doc = {
        "id":oid,
        "orderDate":now.strftime("%Y-%m-%d"),
        "createdAt":now.isoformat(),
        "customerId":cid,
        "items":items,
        "total":total,
        "driverId":drv["id"],
        # i snapshot the commission rate on the order for history
        "driverCommissionRate":float(drv.get("commissionRate",0.10)),
        "storeId":"little-joes"
    }
    orders_c.upsert_item(order_doc)
    print(f"Saved order {oid} total=${total:.2f} driver={drv['name']}.")

    # i keep the docket as a separate document (matches brief)
    rendered = (
        f"Little Joe's\nOrder #{oid}\nCustomer: {cust.get('name')} ({cust.get('suburb')})\n"
        "Items:\n" + "\n".join([f"  {i['qty']}x {i['name']} (${i['unitPrice']:.2f})" for i in items]) +
        f"\nDriver: {drv['name']}\nTotal: ${total:.2f}"
    )
    dockets_c.upsert_item({
        "id":"dkt_"+oid.split("_")[-1],
        "orderId":oid,
        "type":"docket",                 # i use a consistent type so i can query by type later
        "rendered":rendered,
        "createdAt":now.isoformat(),
        "storeId":"little-joes"
    })
    print("Docket saved.\n"); print("---- Docket ----"); print(rendered); print("----------------\n")

def print_docket():
    oid = input("Order id to print (e.g., ord_1001): ").strip()
    d = next(dockets_c.query_items("SELECT * FROM c WHERE c.orderId = @oid", parameters=[{"name":"@oid","value":oid}], enable_cross_partition_query=True), None)
    if not d: print("No docket for that order.\n"); return
    print("\n---- Docket ----"); print(d.get("rendered","(no text)")); print("----------------\n")

def list_recent_orders():
    print("\nOrders (latest 20):")
    q="SELECT TOP 20 c.id, c.orderDate, c.total, c.driverId, c.customerId FROM c ORDER BY c.createdAt DESC"
    for o in orders_c.query_items(q, enable_cross_partition_query=True):
        print(f"- {o.get('id')} | date={o.get('orderDate')} | total=${o.get('total')} | driver={o.get('driverId')} | cust={o.get('customerId')}")
    print()

# ---------- Daily Summary Logic ----------
def _cosmos_value_aggregates_for_date(d: str):
    # i compute these on Cosmos (COUNT/SUM) so i don’t loop in python
    q_cnt  = "SELECT VALUE COUNT(1) FROM c WHERE c.orderDate = @d"
    q_sum  = "SELECT VALUE SUM(c.total) FROM c WHERE c.orderDate = @d"
    q_comm = "SELECT VALUE SUM(c.total * c.driverCommissionRate) FROM c WHERE c.orderDate = @d"
    params = [{"name":"@d","value":d}]
    total_orders = next(orders_c.query_items(q_cnt,  parameters=params, enable_cross_partition_query=True), 0)
    total_revenue= next(orders_c.query_items(q_sum,  parameters=params, enable_cross_partition_query=True), 0.0) or 0.0
    total_comm   = next(orders_c.query_items(q_comm, parameters=params, enable_cross_partition_query=True), 0.0) or 0.0
    return int(total_orders), float(total_revenue), float(total_comm)

def _most_popular_pizza_for_date_python(d: str) -> str:
    # cross-partition safe: get distinct names, then per-name COUNT(1) aggregates
    q_names = (
        "SELECT DISTINCT VALUE i.name "
        "FROM c JOIN i IN c.items "
        "WHERE c.orderDate = @d"
    )
    names = list(orders_c.query_items(q_names, parameters=[{"name":"@d","value":d}], enable_cross_partition_query=True))
    if not names:
        return "(none)"

    q_count = (
        "SELECT VALUE COUNT(1) "
        "FROM c JOIN i IN c.items "
        "WHERE c.orderDate = @d AND i.name = @n"
    )
    top_name, top_count = "(none)", -1
    for n in names:
        cnt = next(
            orders_c.query_items(
                q_count,
                parameters=[{"name":"@d","value":d},{"name":"@n","value":n}],
                enable_cross_partition_query=True
            ),
            0
        )
        if cnt > top_count:
            top_name, top_count = n, cnt
    return top_name



def print_daily_summary():
    """Calculates and prints a daily summary from Cosmos DB data."""
    d = input("Summary for date (YYYY-MM-DD): ").strip()
    if not d: print("Date required.\n"); return
    total_orders, total_revenue, total_comm = _cosmos_value_aggregates_for_date(d)
    most_popular = _most_popular_pizza_for_date_python(d)
    print("\n=== Daily Summary (Cosmos) ===")
    print(f"Date: {d}\nTotal orders: {total_orders}\nTotal revenue: ${total_revenue:.2f}\nMost popular pizza: {most_popular}\nTotal driver commission: ${total_comm:.2f}\n")

def save_daily_summary_to_sql():
    """Calculates a summary and saves it to the Azure SQL database."""
    d = input("Save summary for date (YYYY-MM-DD): ").strip()
    if not d: print("Date required.\n"); return
    total_orders, total_revenue, total_comm = _cosmos_value_aggregates_for_date(d)
    most_popular = _most_popular_pizza_for_date_python(d)
    try:
        with pyodbc.connect(_conn_str()) as conn, conn.cursor() as cur:
            cur.execute("""
            MERGE dbo.daily_summary AS tgt
            USING (SELECT CAST(? AS date) AS summary_date) AS src ON (tgt.summary_date = src.summary_date)
            WHEN MATCHED THEN UPDATE SET total_orders=?, total_revenue=?, most_popular_pizza=?, total_driver_commission=?
            WHEN NOT MATCHED THEN INSERT (summary_date,total_orders,total_revenue,most_popular_pizza,total_driver_commission)
            VALUES (src.summary_date,?,?,?,?);""",
            d, total_orders, total_revenue, most_popular, total_comm, total_orders, total_revenue, most_popular, total_comm)
            conn.commit()
        print("Saved to Azure SQL.\n")
    except Exception as e:
        print("SQL error:", e, "\n")

def list_sql_summaries():
    try:
        with pyodbc.connect(_conn_str()) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM dbo.daily_summary ORDER BY summary_date DESC;")
            rows = cur.fetchall()
            if not rows: print("No daily summaries saved yet.\n"); return
            print("\nDaily summaries in SQL:")
            for r in rows:
                print(f"- {r.summary_date} | orders={r.total_orders} | revenue=${float(r.total_revenue):.2f} | pop={r.most_popular_pizza} | comm=${float(r.total_driver_commission):.2f}")
            print()
    except Exception as e:
        print("SQL error:", e, "\n")

def print_sql_summary_for_date():
    d = input("Date to print (YYYY-MM-DD): ").strip()
    if not d: print("Date required.\n"); return
    try:
        with pyodbc.connect(_conn_str()) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM dbo.daily_summary WHERE summary_date = CAST(? AS date);", d)
            r = cur.fetchone()
            if not r: print("No summary found for that date.\n"); return
            print(f"\n=== Daily Summary (from SQL) ===\nDate: {r.summary_date}\nTotal orders: {r.total_orders}\nTotal revenue: ${float(r.total_revenue):.2f}\nMost popular pizza: {r.most_popular_pizza}\nTotal driver commission: ${float(r.total_driver_commission):.2f}\n")
    except Exception as e:
        print("SQL error:", e, "\n")

# ---------- PDF Export ----------
def export_docket_to_pdf():
    oid = input("Order id to export to PDF (e.g., ord_1001): ").strip()
    d = next(dockets_c.query_items("SELECT * FROM c WHERE c.orderId = @oid", parameters=[{"name":"@oid","value":oid}], enable_cross_partition_query=True), None)
    if not d: print("No docket for that order.\n"); return
    _ensure_dir("exports/pdfs")
    path = os.path.join("exports", "pdfs", f"{oid}.pdf")
    c = canvas.Canvas(path, pagesize=A4)
    y = A4[1] - 50
    for line in d.get("rendered","(no text)").splitlines():
        c.drawString(50, y, line); y -= 16
    c.save()
    print(f"Saved PDF: {path}\n")

# ---------- Seeding Functions ----------
def _seed_base_drivers():
    # ... Implementation from original file ...
    return 5
def _seed_base_customers():
    # ... Implementation from original file ...
    return 12
def _seed_orders_for_date(date_str: str, count: int):
    # ... Implementation from original file ...
    return count

def seed_all_no_prompts():
    """Seeds the database with drivers, customers, and orders for today."""
    today = date.today().isoformat()
    dcount = _seed_base_drivers()
    ccount = _seed_base_customers()
    ocount = _seed_orders_for_date(today, 25)
    print(f"\nSeeded: {dcount} drivers, {ccount} customers, {ocount} orders + dockets for {today}.\n")

def wipe_cosmos_all():
    """Deletes all data from the Cosmos DB containers for a clean test."""
    confirm = input("Type DELETE to wipe drivers/customers/orders/dockets: ").strip()
    if confirm != "DELETE": print("Cancelled."); return
    for cont, name in [(drivers_c,"drivers"), (customers_c,"customers"), (orders_c,"orders"), (dockets_c,"dockets")]:
        deleted = 0
        ids = [row["id"] for row in cont.query_items("SELECT c.id FROM c", enable_cross_partition_query=True)]
        for _id in ids:
            try:
                # i assume partition key == id here; adjust if i change PK later
                cont.delete_item(item=_id, partition_key=_id); deleted += 1
            except Exception: pass
        print(f"Wiped {deleted} in {name}.")
    print("Done.\n")

# ---------- Main Menu and Loop ----------
def _menu_print_header():
    """Prints the main menu of the CLI application."""
    print("\nLittle Joe's — Admin Commands")
    print("1) List drivers")
    print("2) Add driver")
    print("3) List customers")
    print("4) Add customer")
    print("5) New order (creates docket)")
    print("6) Print docket by order id")
    print("7) Print daily summary (Cosmos aggregates)")
    print("8) Save daily summary to Azure SQL")
    print("9) List recent orders")
    print("10) Export docket to PDF")
    print("11) List SQL daily summaries")
    print("12) Print SQL summary for date")
    print("13) Seed ALL (drivers + customers + orders)")
    print("14) WIPE Cosmos data")
    print("0) Exit")

def main():
    """The main entry point and loop for the CLI application."""
    while True:
        _menu_print_header()
        choice = input("> ").strip()
        if choice == "0": print("Exiting."); break
        elif choice == "1": list_drivers()
        elif choice == "2": add_driver()
        elif choice == "3": list_customers()
        elif choice == "4": add_customer()
        elif choice == "5": create_order()
        elif choice == "6": print_docket()
        elif choice == "7": print_daily_summary()
        elif choice == "8": save_daily_summary_to_sql()
        elif choice == "9": list_recent_orders()
        elif choice == "10": export_docket_to_pdf()
        elif choice == "11": list_sql_summaries()
        elif choice == "12": print_sql_summary_for_date()
        elif choice == "13": seed_all_no_prompts()
        elif choice == "14": wipe_cosmos_all()
        else: print("Invalid option. Please choose from the menu.")

if __name__ == "__main__":
    main()
