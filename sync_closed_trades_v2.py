import csv
import os
from datetime import datetime
from pathlib import Path

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


OUTPUT_FILE = Path("closed_trades.csv")


def safe_value(value):
    if value is None:
        return ""
    return str(value)


def env_bool(name, default=True):
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() not in ["false", "0", "no", "live"]


def get_clients_from_railway_env():
    """
    Railway-safe client loader.
    Supports either:
    ALPACA_API_KEY / ALPACA_SECRET_KEY / ALPACA_PAPER

    or multi-account:
    ACCOUNT_1_NAME / ACCOUNT_1_API_KEY / ACCOUNT_1_SECRET_KEY / ACCOUNT_1_PAPER
    ACCOUNT_2_NAME / ACCOUNT_2_API_KEY / ACCOUNT_2_SECRET_KEY / ACCOUNT_2_PAPER
    ACCOUNT_3_NAME / ACCOUNT_3_API_KEY / ACCOUNT_3_SECRET_KEY / ACCOUNT_3_PAPER
    """
    clients = []

    for i in range(1, 6):
        name = os.getenv(f"ACCOUNT_{i}_NAME", f"Account {i}")
        key = os.getenv(f"ACCOUNT_{i}_API_KEY")
        secret = os.getenv(f"ACCOUNT_{i}_SECRET_KEY")
        paper = env_bool(f"ACCOUNT_{i}_PAPER", True)

        if key and secret:
            clients.append({
                "source": name,
                "client": TradingClient(key, secret, paper=paper),
            })

    key = os.getenv("ALPACA_API_KEY")
    secret = os.getenv("ALPACA_SECRET_KEY")
    paper = env_bool("ALPACA_PAPER", True)

    if key and secret:
        clients.append({
            "source": os.getenv("ACCOUNT_NAME", "Primary Alpaca Account"),
            "client": TradingClient(key, secret, paper=paper),
        })

    return clients


def write_rows(rows):
    fieldnames = [
        "synced_at",
        "source_env",
        "symbol",
        "side",
        "qty",
        "filled_qty",
        "filled_avg_price",
        "filled_at",
        "submitted_at",
        "status",
        "order_type",
        "order_class",
        "time_in_force",
        "order_id",
        "client_order_id",
    ]

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def sync_closed_trades():
    rows = []
    seen_ids = set()
    errors = []

    clients = get_clients_from_railway_env()

    if not clients:
        errors.append("No Alpaca credentials found in Railway environment variables.")
        write_rows(rows)
        return {
            "ok": False,
            "rows": 0,
            "errors": errors,
            "output_file": str(OUTPUT_FILE),
        }

    request = GetOrdersRequest(
        status=QueryOrderStatus.CLOSED,
        limit=500,
        direction="desc",
    )

    for item in clients:
        source = item["source"]
        client = item["client"]

        try:
            orders = client.get_orders(filter=request)
        except Exception as e:
            errors.append(f"{source}: Alpaca rejected closed-order sync: {e}")
            continue

        for order in orders:
            order_id = str(order.id)

            if order_id in seen_ids:
                continue

            seen_ids.add(order_id)

            rows.append({
                "synced_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source_env": source,
                "symbol": safe_value(order.symbol),
                "side": safe_value(order.side).replace("OrderSide.", "").lower(),
                "qty": safe_value(order.qty),
                "filled_qty": safe_value(order.filled_qty),
                "filled_avg_price": safe_value(order.filled_avg_price),
                "filled_at": safe_value(order.filled_at),
                "submitted_at": safe_value(order.submitted_at),
                "status": safe_value(order.status).replace("OrderStatus.", "").lower(),
                "order_type": safe_value(order.type).replace("OrderType.", "").lower(),
                "order_class": safe_value(order.order_class).replace("OrderClass.", "").lower(),
                "time_in_force": safe_value(order.time_in_force).replace("TimeInForce.", "").lower(),
                "order_id": order_id,
                "client_order_id": safe_value(order.client_order_id),
            })

    write_rows(rows)

    return {
        "ok": len(errors) == 0,
        "rows": len(rows),
        "errors": errors,
        "output_file": str(OUTPUT_FILE),
    }


def main():
    result = sync_closed_trades()
    print(f"Saved {result['rows']} closed order rows to {result['output_file']}")
    for error in result["errors"]:
        print(error)
    return result


if __name__ == "__main__":
    main()
