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


def strategy_from_client_order_id(client_order_id):
    """
    Decode Coach T client order tags added by the auto-scanner bot.
    Older orders without these tags remain unattributed.
    """
    value = safe_value(client_order_id).lower()

    if value.startswith("ct_s1_breakout_"):
        return {
            "bot_group": "DIRECT_SCANNER",
            "strategy": "breakout_momentum_v1",
            "model": "direct_breakout_live_v1",
        }

    if value.startswith("ct_s2_pullback_"):
        return {
            "bot_group": "DIRECT_SCANNER",
            "strategy": "pullback_reclaim_v1",
            "model": "direct_pullback_live_v1",
        }

    return {"bot_group": "", "strategy": "", "model": ""}


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
        "bot_group",
        "strategy",
        "model",
        "parent_order_id",
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
        nested=True,
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
            parent_order_id = safe_value(getattr(order, "id", ""))
            parent_tag = strategy_from_client_order_id(getattr(order, "client_order_id", ""))

            related_orders = [(order, "")]
            for leg in (getattr(order, "legs", None) or []):
                related_orders.append((leg, parent_order_id))

            for related_order, related_parent_id in related_orders:
                order_id = safe_value(getattr(related_order, "id", ""))

                if not order_id or order_id in seen_ids:
                    continue

                seen_ids.add(order_id)

                own_tag = strategy_from_client_order_id(
                    getattr(related_order, "client_order_id", "")
                )
                strategy_tag = own_tag if own_tag.get("strategy") else parent_tag

                rows.append({
                    "synced_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "source_env": source,
                    "bot_group": strategy_tag.get("bot_group", ""),
                    "strategy": strategy_tag.get("strategy", ""),
                    "model": strategy_tag.get("model", ""),
                    "parent_order_id": related_parent_id,
                    "symbol": safe_value(getattr(related_order, "symbol", "")),
                    "side": safe_value(getattr(related_order, "side", "")).replace("OrderSide.", "").lower(),
                    "qty": safe_value(getattr(related_order, "qty", "")),
                    "filled_qty": safe_value(getattr(related_order, "filled_qty", "")),
                    "filled_avg_price": safe_value(getattr(related_order, "filled_avg_price", "")),
                    "filled_at": safe_value(getattr(related_order, "filled_at", "")),
                    "submitted_at": safe_value(getattr(related_order, "submitted_at", "")),
                    "status": safe_value(getattr(related_order, "status", "")).replace("OrderStatus.", "").lower(),
                    "order_type": safe_value(getattr(related_order, "type", "")).replace("OrderType.", "").lower(),
                    "order_class": safe_value(getattr(related_order, "order_class", "")).replace("OrderClass.", "").lower(),
                    "time_in_force": safe_value(getattr(related_order, "time_in_force", "")).replace("TimeInForce.", "").lower(),
                    "order_id": order_id,
                    "client_order_id": safe_value(getattr(related_order, "client_order_id", "")),
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
