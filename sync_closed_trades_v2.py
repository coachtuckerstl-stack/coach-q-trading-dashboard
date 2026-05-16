import csv
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

BOT_ENVS = [
    Path(r"C:\Users\RodgerTucker\alpaca-bot\.env"),
    Path(r"C:\Users\RodgerTucker\tradingview-alpaca-bot\.env"),
    Path(r"C:\Users\RodgerTucker\alligator-alpaca-bot\.env"),
]

OUTPUT_FILE = Path("closed_trades.csv")


def get_client(env_path: Path):
    env = dotenv_values(env_path)
    key = env.get("ALPACA_API_KEY")
    secret = env.get("ALPACA_SECRET_KEY")
    paper = str(env.get("ALPACA_PAPER", "true")).lower() != "false"

    if not key or not secret:
        raise ValueError(f"Missing Alpaca keys in {env_path}")

    return TradingClient(key, secret, paper=paper)


def safe_value(value):
    if value is None:
        return ""
    return str(value)


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


def main():
    rows = []
    seen_ids = set()

    for env_path in BOT_ENVS:
        if not env_path.exists():
            print(f"Skipping missing env: {env_path}")
            continue

        try:
            client = get_client(env_path)
        except Exception as e:
            print(f"Skipping {env_path}: {e}")
            continue

        request = GetOrdersRequest(
            status=QueryOrderStatus.CLOSED,
            limit=500,
            direction="desc",
        )

        try:
            orders = client.get_orders(filter=request)
        except Exception as e:
            print(f"Skipping {env_path} because Alpaca rejected it: {e}")
            continue

        for order in orders:
            order_id = str(order.id)

            if order_id in seen_ids:
                continue

            seen_ids.add(order_id)

            rows.append({
                "synced_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source_env": str(env_path),
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
    print(f"Saved {len(rows)} closed order rows to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
