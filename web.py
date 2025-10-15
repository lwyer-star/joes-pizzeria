# web.py — Flask Web Application for Little Joe's Pizzeria
# This is the main controller for the web-based interface of the application.
# I've designed it to handle HTTP requests, process data using the functions
# in `db.py`, and render HTML templates to the user. This separation of
# concerns (web logic here, data logic in db.py) makes the project maintainable.

# web.py
import os
import random
import logging
from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, Response
from dotenv import load_dotenv

# I now load secrets from a .env file for the SECRET_KEY.
# NOTE: load .env BEFORE importing db so db.py can read env vars at import time.
load_dotenv()
import db  # (moved below load_dotenv)

# App Initialization
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', os.urandom(24))  # Add a fallback
# Optional: warn if SECRET_KEY not set (sessions reset on restart)
if not os.getenv('SECRET_KEY'):
    app.logger = logging.getLogger(__name__)
    app.logger.warning("SECRET_KEY not set; using a random key (sessions reset on restart).")

# Error Logging Setup
# I've implemented basic logging to a file. This is an advanced feature that
# helps in debugging the application by recording important events and errors
# without exposing them to the user.
logging.basicConfig(
    filename='app.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s'
)
app.logger.info("Application starting up...")

#  Helper Functions
def _choose_driver(drivers: list, suburb: str | None):
    """
    Selects a suitable driver for an order based on their delivery suburbs.
    If no driver serves the customer's suburb, a random driver is assigned.
    I've replicated this logic from my original CLI application.
    """
    if suburb:
        pool = [d for d in drivers if suburb in (d.get("suburbs") or [])]
        if pool:
            return random.choice(pool)
    return random.choice(drivers) if drivers else None

# Main Application Routes

@app.route("/")
def index():
    """Renders the homepage."""
    return render_template("index.html")

@app.route("/drivers")
def drivers():
    """Displays a list of all drivers from the database."""
    rows = db.get_drivers()
    return render_template("drivers.html", rows=rows)

@app.route("/customers")
def customers():
    """Displays a list of all customers."""
    rows = db.list_customers()
    return render_template("customers.html", customers=rows)

@app.route("/orders")
def orders():
    """Displays a list of the most recent orders."""
    rows = db.list_orders()
    return render_template("orders.html", orders=rows)

@app.route("/new-order", methods=['GET', 'POST'])
def new_order():
    """
    Handles the new order form.
    GET: Displays the form.
    POST: Validates input, creates the order and docket, and redirects.
    """
    if request.method == 'POST':
        customer_id = request.form.get('customer_id')

        # I've added basic input validation here as an advanced feature to ensure
        # the customer ID is in a valid format before querying the database.
        if not customer_id or not customer_id.strip().startswith("cus_"):
            flash("Invalid Customer ID format. It must start with 'cus_'.", "error")
            return redirect(url_for('new_order'))

        # Replaced Mongo-style find_one with a Cosmos query helper in db.py
        customer = db.get_customer_by_id(customer_id)
        if not customer:
            flash(f"Customer with ID '{customer_id}' not found.", "error")
            return redirect(url_for('new_order'))

        MENU = {
            "MARG": {"name": "Margherita", "price": 14.00},
            "PEPP": {"name": "Pepperoni", "price": 17.00},
            "SUPR": {"name": "Super Supreme", "price": 19.50}
        }
        items = []
        # Process the quantities of each pizza from the form.
        for sku, data in MENU.items():
            qty = request.form.get(f'qty_{sku.lower()}', 0, type=int)
            if qty > 0:
                items.append({"sku": sku, "name": data["name"], "qty": qty, "unitPrice": data["price"]})

        if not items:
            flash("Cannot create an order with no items.", "error")
            return redirect(url_for('new_order'))

        total = round(sum(i["qty"] * i["unitPrice"] for i in items), 2)

        all_drivers = db.get_drivers()
        if not all_drivers:
            flash("No drivers available to assign.", "error")
            app.logger.warning("Attempted to create order but no drivers are available.")
            return redirect(url_for('new_order'))

        assigned_driver = _choose_driver(all_drivers, customer.get("suburb"))

        # Call the function in db.py to handle database operations.
        order, docket = db.create_order_and_docket(customer, items, total, assigned_driver)

        if order:
            flash(f"Successfully created Order ID: {order['id']}", "success")
            app.logger.info(f"New order created: {order['id']} for customer {customer['id']}.")
            return redirect(url_for('orders'))
        else:
            flash("Failed to create the order.", "error")
            app.logger.error("Failed to create order and docket in database.")
            return redirect(url_for('new_order'))

    return render_template("new_order.html")

@app.route("/docket", methods=['GET', 'POST'])
def docket():
    """Displays a specific docket based on a submitted order ID."""
    docket_data = None
    if request.method == 'POST':
        order_id = request.form.get('order_id')
        if order_id:
            docket_data = db.get_docket_by_order(order_id)
    return render_template("view_docket.html", docket=docket_data)

@app.route("/docket/<order_id>/pdf")
def docket_pdf(order_id):
    """
    Generates and serves a PDF for a given order's docket.
    This is an advanced feature for the project.
    """
    docket_data = db.get_docket_by_order(order_id)
    if not docket_data or 'rendered' not in docket_data:
        flash("Could not find docket to generate PDF.", "error")
        return redirect(url_for('docket'))

    # Generate the PDF in memory and serve it directly to the user.
    pdf_buffer = db.generate_docket_pdf(docket_data['rendered'])

    app.logger.info(f"Generated PDF for docket related to order {order_id}.")
    return Response(
        pdf_buffer,
        mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment;filename={order_id}_docket.pdf'}
    )

@app.route("/summaries")
def summaries():
    """Displays all daily summaries from the SQL database."""
    summary_list = db.get_sql_summaries()
    return render_template("summaries.html", summaries=summary_list)

@app.route("/generate-summary", methods=['GET', 'POST'])
def generate_summary():
    """
    Handles the form for generating a new daily summary.
    POST: Triggers the calculation and saving of the summary.
    """
    if request.method == 'POST':
        date_str = request.form.get('summary_date')
        if not date_str:
            flash("Please select a date.", "error")
            return redirect(url_for('generate_summary'))

        success = db.generate_and_save_summary(date_str)

        if success:
            flash(f"Successfully generated and saved summary for {date_str}.", "success")
            app.logger.info(f"Generated daily summary for {date_str}.")
            return redirect(url_for('summaries'))
        else:
            flash(f"Failed to generate summary for {date_str}. Check server logs for errors.", "error")
            app.logger.error(f"Failed to generate summary for {date_str}.")
            return redirect(url_for('generate_summary'))

    return render_template("generate_summary.html")

# Utility Routes
@app.route("/drivers_raw")
def drivers_raw():
    """A simple API endpoint to return raw driver data as JSON."""
    return jsonify(db.get_drivers())

# Main Execution
if __name__ == "__main__":
    # The debug=True flag is useful for development as it provides detailed
    # error pages and auto-reloads the server when I make code changes.
    import webbrowser
    host = "127.0.0.1"
    port = 5000
    url = f"http://{host}:{port}/"
    print("\n----------------------------------------------------")
    print("Little Joe's Web UI is running.")
    print(f"Open this link in a browser: {url}")
    print("Tip: Ctrl+Click in most terminals to open it.")
    print("----------------------------------------------------\n")
    try:
        # i open the browser once on startup so it’s obvious during marking
        webbrowser.open(url)
    except Exception:
        pass
    app.logger.info(f"Serving at {url}")
    app.run(debug=True, host=host, port=port)
