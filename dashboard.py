import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus
from sqlalchemy import create_engine, text

load_dotenv()

st.set_page_config(
    page_title="Coach Q Trading Command Center",
    page_icon="📈",
    layout="wide",
)

# -----------------------
# Config
# -----------------------
DATABASE_URL = os.getenv("DATABASE_URL", "")

BOT_CONFIG = [
    {
        "name": "TradingView Bot - QQQ TSLA AMD",
        "type": "TradingView Webhook",
        "health_url": "https://tradingview-alpaca-bot-production.up.railway.app/health",
        "webhook_url": "https://tradingview-alpaca-bot-production.up.railway.app/webhook",
        "symbols": "QQQ, TSLA, AMD",
    },
    {
        "name": "Alligator Bot - LIVE",
        "type": "TradingView Webhook",
        "health_url": "https://alligator-alpaca-bot-52oq-production.up.railway.app/health",
        "webhook_url": "https://alligator-alpaca-bot-52oq-production.up.railway.app/webhook",
        "symbols": "TradingView alerts",
    },
    {
        "name": "Alpaca Direct Bot - Auto Scanner",
        "type": "Direct Alpaca Scanner",
        "health_url": "",
        "webhook_url": "Not used",
        "symbols": "Auto watchlist",
    },
]


def get_account_configs() -> List[Dict[str, Any]]:
    """
    Supports up to 3 Alpaca accounts.

    Preferred .env format:
    ACCOUNT_1_NAME=Alligator Bot Account
    ACCOUNT_1_API_KEY=...
    ACCOUNT_1_SECRET_KEY=...
    ACCOUNT_1_PAPER=true

    ACCOUNT_2_NAME=TradingView QQQ TSLA AMD Account
    ACCOUNT_2_API_KEY=...
    ACCOUNT_2_SECRET_KEY=...
    ACCOUNT_2_PAPER=true

    ACCOUNT_3_NAME=Alpaca Direct Scanner Account
    ACCOUNT_3_API_KEY=...
    ACCOUNT_3_SECRET_KEY=...
    ACCOUNT_3_PAPER=true

    Fallback single-account format still works:
    ALPACA_API_KEY=...
    ALPACA_SECRET_KEY=...
    ALPACA_PAPER=true
    """
    accounts = []

    for i in range(1, 4):
        api_key = os.getenv(f"ACCOUNT_{i}_API_KEY")
        secret_key = os.getenv(f"ACCOUNT_{i}_SECRET_KEY")
        paper = os.getenv(f"ACCOUNT_{i}_PAPER", "true").lower() in ["true", "1", "yes"]
        name = os.getenv(f"ACCOUNT_{i}_NAME", f"Alpaca Account {i}")

        if api_key and secret_key:
            accounts.append({
                "name": name,
                "api_key": api_key,
                "secret_key": secret_key,
                "paper": paper,
            })

    # Backward compatible fallback
    if not accounts:
        api_key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
        secret_key = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
        paper = os.getenv("ALPACA_PAPER", "true").lower() in ["true", "1", "yes"]
        if api_key and secret_key:
            accounts.append({
                "name": os.getenv("ACCOUNT_NAME", "Primary Alpaca Account"),
                "api_key": api_key,
                "secret_key": secret_key,
                "paper": paper,
            })

    return accounts


ACCOUNT_CONFIGS = get_account_configs()


# -----------------------
# Helpers
# -----------------------
def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def money(value: Any) -> str:
    return f"${safe_float(value):,.2f}"


def pct(value: Any) -> str:
    return f"{safe_float(value):,.2f}%"


def to_local_time(series: pd.Series) -> pd.Series:
    converted = pd.to_datetime(series, errors="coerce", utc=True)
    try:
        return converted.dt.tz_convert("America/Chicago")
    except Exception:
        return converted


def check_url(url: str, timeout: int = 6) -> Dict[str, Any]:
    if not url:
        return {"status": "Heartbeat Only", "ok": True, "code": "", "detail": "No public URL"}
    try:
        response = requests.get(url, timeout=timeout)
        return {
            "status": "Online" if response.status_code == 200 else f"HTTP {response.status_code}",
            "ok": response.status_code == 200,
            "code": response.status_code,
            "detail": response.text[:200],
        }
    except Exception as exc:
        return {"status": "Offline", "ok": False, "code": "", "detail": str(exc)}


@st.cache_resource(show_spinner=False)
def get_db_engine():
    if not DATABASE_URL:
        return None
    return create_engine(DATABASE_URL)


def get_trading_client(account: Dict[str, Any]) -> TradingClient:
    return TradingClient(
        account["api_key"],
        account["secret_key"],
        paper=account.get("paper", True),
    )


@st.cache_data(ttl=20, show_spinner=False)
def get_account_snapshots() -> pd.DataFrame:
    rows = []

    if not ACCOUNT_CONFIGS:
        return pd.DataFrame([{"Error": "No Alpaca account keys found in .env"}])

    for account_cfg in ACCOUNT_CONFIGS:
        try:
            client = get_trading_client(account_cfg)
            account = client.get_account()

            equity = safe_float(account.equity)
            last_equity = safe_float(account.last_equity)
            daily_pl = equity - last_equity
            daily_pl_pct = (daily_pl / last_equity * 100) if last_equity else 0
            status = str(getattr(account, "status", "unknown")).replace("AccountStatus.", "")

            rows.append({
                "Account": account_cfg["name"],
                "Mode": "Paper" if account_cfg.get("paper", True) else "Live",
                "Equity": equity,
                "Cash": safe_float(account.cash),
                "Buying Power": safe_float(account.buying_power),
                "Portfolio Value": safe_float(account.portfolio_value),
                "Last Equity": last_equity,
                "Daily P/L": daily_pl,
                "Daily P/L %": daily_pl_pct,
                "Status": status,
            })
        except Exception as exc:
            rows.append({
                "Account": account_cfg["name"],
                "Mode": "Paper" if account_cfg.get("paper", True) else "Live",
                "Error": str(exc),
            })

    return pd.DataFrame(rows)


@st.cache_data(ttl=20, show_spinner=False)
def get_positions_df() -> pd.DataFrame:
    rows = []

    for account_cfg in ACCOUNT_CONFIGS:
        try:
            client = get_trading_client(account_cfg)
            positions = client.get_all_positions()

            for p in positions:
                rows.append({
                    "Account": account_cfg["name"],
                    "Symbol": p.symbol,
                    "Qty": safe_float(p.qty),
                    "Side": p.side,
                    "Avg Entry": safe_float(p.avg_entry_price),
                    "Current Price": safe_float(p.current_price),
                    "Market Value": safe_float(p.market_value),
                    "Unrealized P/L": safe_float(p.unrealized_pl),
                    "Unrealized %": safe_float(p.unrealized_plpc) * 100,
                })
        except Exception as exc:
            rows.append({
                "Account": account_cfg["name"],
                "Error": str(exc),
            })

    return pd.DataFrame(rows)


@st.cache_data(ttl=20, show_spinner=False)
def get_orders_df(limit_per_account: int = 100) -> pd.DataFrame:
    rows = []

    for account_cfg in ACCOUNT_CONFIGS:
        try:
            client = get_trading_client(account_cfg)
            request = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=limit_per_account)
            orders = client.get_orders(filter=request)

            for o in orders:
                rows.append({
                    "Account": account_cfg["name"],
                    "Submitted": o.submitted_at,
                    "Symbol": o.symbol,
                    "Side": str(o.side).replace("OrderSide.", ""),
                    "Type": str(o.order_type).replace("OrderType.", ""),
                    "Qty": safe_float(o.qty),
                    "Filled Qty": safe_float(o.filled_qty),
                    "Filled Avg Price": safe_float(getattr(o, "filled_avg_price", None)),
                    "Filled At": getattr(o, "filled_at", None),
                    "Status": str(o.status).replace("OrderStatus.", ""),
                    "Limit Price": safe_float(o.limit_price) if o.limit_price else None,
                    "Stop Price": safe_float(o.stop_price) if o.stop_price else None,
                    "Order ID": str(o.id),
                    "Client Order ID": str(getattr(o, "client_order_id", "")),
                })
        except Exception as exc:
            rows.append({
                "Account": account_cfg["name"],
                "Error": str(exc),
            })

    df = pd.DataFrame(rows)
    if not df.empty:
        if "Submitted" in df.columns:
            df["Submitted"] = to_local_time(df["Submitted"])
        if "Filled At" in df.columns:
            df["Filled At"] = to_local_time(df["Filled At"])
    return df


def account_to_bot(account_name: str) -> str:
    name = str(account_name).lower()
    if "alligator" in name:
        return "Alligator Bot - LIVE"
    if "tradingview" in name or "qqq" in name or "tsla" in name or "amd" in name:
        return "TradingView Bot - QQQ TSLA AMD"
    if "direct" in name or "scanner" in name:
        return "Alpaca Direct Bot - Auto Scanner"
    return str(account_name)


def account_to_strategy(account_name: str) -> str:
    name = str(account_name).lower()
    if "alligator" in name:
        return "alligator"
    if "tradingview" in name or "qqq" in name or "tsla" in name or "amd" in name:
        return "tradingview_strat"
    if "direct" in name or "scanner" in name:
        return "alpaca_direct_scanner"
    return "unknown"


def build_closed_trades_from_orders(orders_df: pd.DataFrame) -> pd.DataFrame:
    if orders_df.empty or "Error" in orders_df.columns:
        return pd.DataFrame()

    required = {"Account", "Symbol", "Side", "Filled Qty", "Filled Avg Price", "Status"}
    if not required.issubset(set(orders_df.columns)):
        return pd.DataFrame()

    filled = orders_df.copy()
    filled = filled[filled["Status"].astype(str).str.lower() == "filled"].copy()
    filled = filled[pd.to_numeric(filled["Filled Qty"], errors="coerce").fillna(0) > 0]
    filled = filled[pd.to_numeric(filled["Filled Avg Price"], errors="coerce").fillna(0) > 0]

    if filled.empty:
        return pd.DataFrame()

    sort_col = "Filled At" if "Filled At" in filled.columns else "Submitted"
    filled = filled.sort_values(sort_col)

    trades = []
    for (account, symbol), group in filled.groupby(["Account", "Symbol"], dropna=False):
        open_lots = []

        for _, order in group.iterrows():
            side = str(order["Side"]).lower()
            qty = safe_float(order["Filled Qty"])
            price = safe_float(order["Filled Avg Price"])
            timestamp = order.get(sort_col)
            order_id = order.get("Order ID", "")

            if qty <= 0 or price <= 0:
                continue

            if side == "buy":
                open_lots.append({
                    "qty": qty,
                    "entry_price": price,
                    "entry_time": timestamp,
                    "entry_order_id": order_id,
                })

            elif side == "sell":
                remaining = qty
                while remaining > 0 and open_lots:
                    lot = open_lots[0]
                    close_qty = min(remaining, lot["qty"])
                    pnl = (price - lot["entry_price"]) * close_qty
                    pnl_pct = ((price - lot["entry_price"]) / lot["entry_price"] * 100) if lot["entry_price"] else 0

                    trades.append({
                        "Account": account,
                        "Bot": account_to_bot(account),
                        "Strategy": account_to_strategy(account),
                        "Symbol": symbol,
                        "Qty": close_qty,
                        "Entry Time": lot["entry_time"],
                        "Exit Time": timestamp,
                        "Entry Price": lot["entry_price"],
                        "Exit Price": price,
                        "P/L": pnl,
                        "P/L %": pnl_pct,
                        "Result": "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "FLAT"),
                        "Entry Order ID": lot["entry_order_id"],
                        "Exit Order ID": order_id,
                    })

                    lot["qty"] -= close_qty
                    remaining -= close_qty
                    if lot["qty"] <= 0.000001:
                        open_lots.pop(0)

    df = pd.DataFrame(trades)
    if not df.empty:
        df["Exit Time"] = pd.to_datetime(df["Exit Time"], errors="coerce")
        df["Entry Time"] = pd.to_datetime(df["Entry Time"], errors="coerce")
        df["Exit Date"] = df["Exit Time"].dt.date
    return df


def summarize_closed_trades(closed_df: pd.DataFrame, group_cols: list) -> pd.DataFrame:
    if closed_df.empty:
        return pd.DataFrame()

    rows = []
    grouped = closed_df.groupby(group_cols, dropna=False)

    for keys, group in grouped:
        if not isinstance(keys, tuple):
            keys = (keys,)

        trade_count = len(group)
        wins = int((group["P/L"] > 0).sum())
        losses = int((group["P/L"] < 0).sum())
        flats = int((group["P/L"] == 0).sum())
        total_pl = group["P/L"].sum()
        avg_pl = group["P/L"].mean()
        win_rate = (wins / trade_count * 100) if trade_count else 0
        gross_profit = group.loc[group["P/L"] > 0, "P/L"].sum()
        gross_loss = abs(group.loc[group["P/L"] < 0, "P/L"].sum())
        profit_factor = (gross_profit / gross_loss) if gross_loss else None

        row = {col: value for col, value in zip(group_cols, keys)}
        row.update({
            "Closed Trades": trade_count,
            "Wins": wins,
            "Losses": losses,
            "Flat": flats,
            "Win Rate %": win_rate,
            "Total P/L": total_pl,
            "Avg P/L": avg_pl,
            "Gross Profit": gross_profit,
            "Gross Loss": gross_loss,
            "Profit Factor": profit_factor,
        })
        rows.append(row)

    return pd.DataFrame(rows).sort_values("Total P/L", ascending=False)


@st.cache_data(ttl=20, show_spinner=False)
def get_bot_events_df(limit: int = 1000) -> pd.DataFrame:
    if not DATABASE_URL:
        return pd.DataFrame([{"Message": "DATABASE_URL missing from .env"}])

    try:
        engine = get_db_engine()
        query = text("""
            SELECT
                created_at,
                bot_name,
                event_type,
                symbol,
                side,
                strategy,
                model,
                status,
                qty,
                entry,
                stop_loss,
                take_profit,
                order_id,
                message
            FROM bot_events
            ORDER BY created_at DESC
            LIMIT :limit
        """)
        with engine.connect() as conn:
            df = pd.read_sql(query, conn, params={"limit": limit})

        if not df.empty:
            df["created_at"] = to_local_time(df["created_at"])
            df["date"] = df["created_at"].dt.date
        return df
    except Exception as exc:
        return pd.DataFrame([{"Message": f"Database read error: {exc}"}])


def ensure_daily_pl_table():
    if not DATABASE_URL:
        return

    engine = get_db_engine()

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS daily_pl_snapshots (
                id SERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                account_name TEXT,
                equity NUMERIC,
                cash NUMERIC,
                buying_power NUMERIC,
                portfolio_value NUMERIC,
                daily_pl NUMERIC,
                daily_pl_pct NUMERIC,
                account_status TEXT
            );
        """))

        # Upgrade older table if it was created before multi-account support
        conn.execute(text("""
            ALTER TABLE daily_pl_snapshots
            ADD COLUMN IF NOT EXISTS account_name TEXT;
        """))


def record_daily_pl_snapshots(account_df: pd.DataFrame, min_minutes_between: int = 5):
    if not DATABASE_URL or account_df.empty or "Error" in account_df.columns:
        return

    try:
        ensure_daily_pl_table()
        engine = get_db_engine()

        with engine.begin() as conn:
            for _, row in account_df.iterrows():
                account_name = row.get("Account")

                recent_count = conn.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM daily_pl_snapshots
                        WHERE account_name = :account_name
                          AND created_at >= NOW() - (:minutes * INTERVAL '1 minute')
                    """),
                    {"account_name": account_name, "minutes": min_minutes_between},
                ).scalar()

                if recent_count and int(recent_count) > 0:
                    continue

                conn.execute(
                    text("""
                        INSERT INTO daily_pl_snapshots (
                            account_name,
                            equity,
                            cash,
                            buying_power,
                            portfolio_value,
                            daily_pl,
                            daily_pl_pct,
                            account_status
                        )
                        VALUES (
                            :account_name,
                            :equity,
                            :cash,
                            :buying_power,
                            :portfolio_value,
                            :daily_pl,
                            :daily_pl_pct,
                            :account_status
                        )
                    """),
                    {
                        "account_name": account_name,
                        "equity": row.get("Equity"),
                        "cash": row.get("Cash"),
                        "buying_power": row.get("Buying Power"),
                        "portfolio_value": row.get("Portfolio Value"),
                        "daily_pl": row.get("Daily P/L"),
                        "daily_pl_pct": row.get("Daily P/L %"),
                        "account_status": row.get("Status"),
                    },
                )
    except Exception as exc:
        print(f"Daily P/L snapshot failed: {exc}", flush=True)


@st.cache_data(ttl=30, show_spinner=False)
def get_daily_pl_df(limit: int = 1500) -> pd.DataFrame:
    if not DATABASE_URL:
        return pd.DataFrame([{"Message": "DATABASE_URL missing from .env"}])

    try:
        ensure_daily_pl_table()
        engine = get_db_engine()

        query = text("""
            SELECT
                created_at,
                COALESCE(account_name, 'Primary Alpaca Account') AS account_name,
                equity,
                cash,
                buying_power,
                portfolio_value,
                daily_pl,
                daily_pl_pct,
                account_status
            FROM daily_pl_snapshots
            ORDER BY created_at DESC
            LIMIT :limit
        """)

        with engine.connect() as conn:
            df = pd.read_sql(query, conn, params={"limit": limit})

        if not df.empty:
            df["created_at"] = to_local_time(df["created_at"])
            df["date"] = df["created_at"].dt.date
            for col in ["equity", "cash", "buying_power", "portfolio_value", "daily_pl", "daily_pl_pct"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df
    except Exception as exc:
        return pd.DataFrame([{"Message": f"Daily P/L read error: {exc}"}])


@st.cache_data(ttl=30, show_spinner=False)
def get_bot_health_df() -> pd.DataFrame:
    rows = []
    for bot in BOT_CONFIG:
        result = check_url(bot["health_url"])
        rows.append({
            "Bot": bot["name"],
            "Type": bot["type"],
            "Health": result["status"],
            "HTTP Code": result.get("code", ""),
            "Symbols": bot["symbols"],
            "Webhook": bot["webhook_url"],
            "Detail": result.get("detail", ""),
        })
    return pd.DataFrame(rows)


def summarize_bot_status(events_df: pd.DataFrame, health_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    now = datetime.now(timezone.utc).astimezone()

    for bot in BOT_CONFIG:
        bot_name = bot["name"]
        bot_events = events_df[events_df["bot_name"] == bot_name] if "bot_name" in events_df.columns else pd.DataFrame()

        health_row = health_df[health_df["Bot"] == bot_name]
        health = health_row.iloc[0]["Health"] if not health_row.empty else "Unknown"

        last_event_time = ""
        last_event_type = ""
        last_status = ""
        last_message = ""
        age_minutes = None

        if not bot_events.empty:
            latest = bot_events.sort_values("created_at", ascending=False).iloc[0]
            last_event_time = latest["created_at"]
            last_event_type = latest.get("event_type", "")
            last_status = latest.get("status", "")
            last_message = latest.get("message", "")

            try:
                diff = now - last_event_time.to_pydatetime()
                age_minutes = round(diff.total_seconds() / 60, 1)
            except Exception:
                age_minutes = None

        if bot_name == "Alpaca Direct Bot - Auto Scanner":
            if age_minutes is not None and age_minutes <= 20:
                overall = "Online"
            elif age_minutes is not None:
                overall = "Stale"
            else:
                overall = "No Heartbeat"
        else:
            overall = "Online" if health == "Online" else health

        rows.append({
            "Bot": bot_name,
            "Overall": overall,
            "Health": health,
            "Last Event": last_event_type,
            "Last Status": last_status,
            "Last Seen": last_event_time,
            "Minutes Ago": age_minutes,
            "Last Message": last_message,
        })

    return pd.DataFrame(rows)


# -----------------------
# UI
# -----------------------
st.title("Coach Q Trading Command Center")
st.caption("Multi-account dashboard for Alpaca balances, Railway bot health, shared bot events, and daily P/L.")

with st.sidebar:
    st.header("Controls")
    if st.button("Refresh now"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()

    st.write("Alpaca Accounts:", len(ACCOUNT_CONFIGS))
    st.write("Database:", "Connected" if DATABASE_URL else "Not connected")
    st.divider()
    hide_db_tests = st.checkbox("Hide DB_TEST rows", value=True)
    limit_events = st.slider("Event rows to load", min_value=100, max_value=2000, value=1000, step=100)

account_df = get_account_snapshots()
if not account_df.empty and "Error" not in account_df.columns:
    record_daily_pl_snapshots(account_df)

events_df = get_bot_events_df(limit_events)
health_df = get_bot_health_df()
pl_df = get_daily_pl_df(1500)

if not events_df.empty and "event_type" in events_df.columns and hide_db_tests:
    events_view_df = events_df[events_df["event_type"] != "DB_TEST"].copy()
else:
    events_view_df = events_df.copy()

# Header account metrics
if account_df.empty:
    st.error("No Alpaca account data loaded.")
elif "Error" in account_df.columns:
    st.error("One or more Alpaca accounts had an error. Check the Account Summary table.")
else:
    total_equity = account_df["Equity"].sum()
    total_cash = account_df["Cash"].sum()
    total_buying_power = account_df["Buying Power"].sum()
    total_daily_pl = account_df["Daily P/L"].sum()
    total_last_equity = account_df["Last Equity"].sum()
    total_daily_pl_pct = (total_daily_pl / total_last_equity * 100) if total_last_equity else 0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Equity", money(total_equity), money(total_daily_pl))
    c2.metric("Total Cash", money(total_cash))
    c3.metric("Total Buying Power", money(total_buying_power))
    c4.metric("Total Daily P/L %", pct(total_daily_pl_pct))
    c5.metric("Accounts", len(account_df))

st.divider()

tab_overview, tab_accounts, tab_bots, tab_events, tab_pl, tab_positions, tab_orders, tab_analytics, tab_performance, tab_setup = st.tabs([
    "Overview",
    "Accounts",
    "Bot Status",
    "Bot Events",
    "Daily P/L",
    "Open Positions",
    "Recent Orders",
    "Trade Analytics",
    "Performance",
    "Setup",
])

with tab_overview:
    st.subheader("Command Center Overview")

    if "Message" in events_df.columns:
        st.warning(events_df.iloc[0]["Message"])
    else:
        status_df = summarize_bot_status(events_df, health_df)

        c1, c2, c3 = st.columns(3)
        for idx, row in status_df.iterrows():
            with [c1, c2, c3][idx]:
                st.metric(row["Bot"], row["Overall"], row["Last Status"])
                st.caption(f"Last: {row['Last Event']} — {row['Minutes Ago']} min ago")
                if row["Last Message"]:
                    st.caption(str(row["Last Message"])[:100])

        st.subheader("Today's Bot Activity")
        today_local = datetime.now().date()
        today_df = events_view_df[events_view_df["date"] == today_local] if "date" in events_view_df.columns else pd.DataFrame()

        t1, t2, t3, t4 = st.columns(4)
        t1.metric("Events Today", len(today_df))
        t2.metric("Webhooks Today", int((today_df["event_type"] == "WEBHOOK_RECEIVED").sum()) if not today_df.empty else 0)
        t3.metric("Rejected Trades", int((today_df["event_type"] == "TRADE_REJECTED").sum()) if not today_df.empty else 0)
        t4.metric("Placed Trades", int((today_df["event_type"] == "TRADE_PLACED").sum()) if not today_df.empty else 0)

        if not pl_df.empty and "daily_pl" in pl_df.columns:
            pl_today = pl_df[pl_df["date"] == today_local].copy()
            if not pl_today.empty:
                latest_by_account = (
                    pl_today.sort_values("created_at")
                    .groupby("account_name", as_index=False)
                    .tail(1)
                )

                st.subheader("Daily P/L Snapshot by Account")
                st.dataframe(
                    latest_by_account[[
                        "created_at",
                        "account_name",
                        "equity",
                        "cash",
                        "buying_power",
                        "daily_pl",
                        "daily_pl_pct",
                    ]],
                    use_container_width=True,
                    hide_index=True,
                )

        if not today_df.empty:
            st.subheader("Latest Events")
            st.dataframe(
                today_df[["created_at", "bot_name", "event_type", "symbol", "status", "message"]].head(15),
                use_container_width=True,
                hide_index=True,
            )

with tab_accounts:
    st.subheader("Alpaca Account Summary")

    if account_df.empty:
        st.warning("No account data available.")
    else:
        display_accounts = account_df.copy()
        money_cols = ["Equity", "Cash", "Buying Power", "Portfolio Value", "Last Equity", "Daily P/L"]
        for col in money_cols:
            if col in display_accounts.columns:
                display_accounts[col] = display_accounts[col].map(lambda x: f"${x:,.2f}" if pd.notna(x) else "")
        if "Daily P/L %" in display_accounts.columns:
            display_accounts["Daily P/L %"] = display_accounts["Daily P/L %"].map(lambda x: f"{x:.2f}%" if pd.notna(x) else "")

        st.dataframe(display_accounts, use_container_width=True, hide_index=True)

with tab_bots:
    st.subheader("Bot Health and Last Seen")
    if "Message" in events_df.columns:
        st.warning(events_df.iloc[0]["Message"])
        st.dataframe(health_df, use_container_width=True, hide_index=True)
    else:
        status_df = summarize_bot_status(events_df, health_df)
        st.dataframe(status_df, use_container_width=True, hide_index=True)

        st.subheader("Railway Health Details")
        st.dataframe(health_df, use_container_width=True, hide_index=True)

with tab_events:
    st.subheader("Bot Events")

    if events_view_df.empty:
        st.warning("No bot events found.")
    elif "Message" in events_view_df.columns:
        st.warning(events_view_df.iloc[0]["Message"])
    else:
        f1, f2, f3, f4 = st.columns(4)

        bot_options = ["All"] + sorted(events_view_df["bot_name"].dropna().unique().tolist())
        event_options = ["All"] + sorted(events_view_df["event_type"].dropna().unique().tolist())
        symbol_options = ["All"] + sorted(events_view_df["symbol"].dropna().astype(str).unique().tolist())
        status_options = ["All"] + sorted(events_view_df["status"].dropna().astype(str).unique().tolist())

        bot_filter = f1.selectbox("Bot", bot_options)
        event_filter = f2.selectbox("Event Type", event_options)
        symbol_filter = f3.selectbox("Symbol", symbol_options)
        status_filter = f4.selectbox("Status", status_options)

        filtered = events_view_df.copy()

        if bot_filter != "All":
            filtered = filtered[filtered["bot_name"] == bot_filter]
        if event_filter != "All":
            filtered = filtered[filtered["event_type"] == event_filter]
        if symbol_filter != "All":
            filtered = filtered[filtered["symbol"].astype(str) == symbol_filter]
        if status_filter != "All":
            filtered = filtered[filtered["status"].astype(str) == status_filter]

        st.dataframe(
            filtered[[
                "created_at",
                "bot_name",
                "event_type",
                "symbol",
                "side",
                "strategy",
                "model",
                "status",
                "qty",
                "entry",
                "stop_loss",
                "take_profit",
                "order_id",
                "message",
            ]],
            use_container_width=True,
            hide_index=True,
        )

with tab_pl:
    st.subheader("Daily P/L Tracker")

    if pl_df.empty:
        st.warning("No P/L snapshots recorded yet. Click Refresh now to record the first snapshot.")
    elif "Message" in pl_df.columns:
        st.warning(pl_df.iloc[0]["Message"])
    else:
        pl_sorted = pl_df.sort_values("created_at").copy()
        today = datetime.now().date()
        today_pl = pl_sorted[pl_sorted["date"] == today].copy()

        if not today_pl.empty:
            latest_by_account = (
                today_pl.sort_values("created_at")
                .groupby("account_name", as_index=False)
                .tail(1)
            )

            total_equity = latest_by_account["equity"].sum()
            total_daily_pl = latest_by_account["daily_pl"].sum()
            total_last_equity = total_equity - total_daily_pl
            total_daily_pl_pct = (total_daily_pl / total_last_equity * 100) if total_last_equity else 0

            p1, p2, p3, p4 = st.columns(4)
            p1.metric("Combined Daily P/L", money(total_daily_pl))
            p2.metric("Combined Daily P/L %", pct(total_daily_pl_pct))
            p3.metric("Combined Equity", money(total_equity))
            p4.metric("Accounts Tracked", latest_by_account["account_name"].nunique())

            st.caption("This records a new snapshot about every 5 minutes while the dashboard is open or refreshed.")

            st.subheader("Daily P/L by Account")
            fig_pl_accounts = px.line(
                today_pl,
                x="created_at",
                y="daily_pl",
                color="account_name",
                markers=True,
            )
            st.plotly_chart(fig_pl_accounts, use_container_width=True)

            st.subheader("Equity by Account")
            fig_equity_accounts = px.line(
                today_pl,
                x="created_at",
                y="equity",
                color="account_name",
                markers=True,
            )
            st.plotly_chart(fig_equity_accounts, use_container_width=True)

            display_pl = today_pl[[
                "created_at",
                "account_name",
                "equity",
                "cash",
                "buying_power",
                "daily_pl",
                "daily_pl_pct",
                "account_status",
            ]].sort_values("created_at", ascending=False).copy()

            for col in ["equity", "cash", "buying_power", "daily_pl"]:
                display_pl[col] = display_pl[col].map(lambda x: f"${x:,.2f}" if pd.notna(x) else "")
            display_pl["daily_pl_pct"] = display_pl["daily_pl_pct"].map(lambda x: f"{x:.2f}%" if pd.notna(x) else "")

            st.subheader("Today’s P/L Snapshots")
            st.dataframe(display_pl, use_container_width=True, hide_index=True)
        else:
            st.info("No snapshots recorded for today yet.")

        st.subheader("All Stored P/L Snapshots")
        st.dataframe(pl_sorted.sort_values("created_at", ascending=False).head(150), use_container_width=True, hide_index=True)

with tab_positions:
    st.subheader("Open Alpaca Positions")
    positions_df = get_positions_df()

    if positions_df.empty:
        st.success("No open positions right now.")
    elif "Error" in positions_df.columns:
        st.dataframe(positions_df, use_container_width=True, hide_index=True)
    else:
        display_df = positions_df.copy()
        for col in ["Avg Entry", "Current Price", "Market Value", "Unrealized P/L"]:
            display_df[col] = display_df[col].map(lambda x: f"${x:,.2f}" if pd.notna(x) else "")
        display_df["Unrealized %"] = display_df["Unrealized %"].map(lambda x: f"{x:.2f}%" if pd.notna(x) else "")
        st.dataframe(display_df, use_container_width=True, hide_index=True)

        fig = px.bar(positions_df, x="Symbol", y="Unrealized P/L", color="Account", text="Unrealized P/L")
        st.plotly_chart(fig, use_container_width=True)

with tab_orders:
    st.subheader("Recent Alpaca Orders")
    orders_df = get_orders_df(100)

    if orders_df.empty:
        st.warning("No recent orders loaded.")
    elif "Error" in orders_df.columns:
        st.dataframe(orders_df, use_container_width=True, hide_index=True)
    else:
        st.dataframe(orders_df, use_container_width=True, hide_index=True)

with tab_analytics:
    st.subheader("Trade Analytics")

    st.caption(
        "Closed-trade P/L is estimated from filled Alpaca orders using FIFO matching. "
        "Because each strategy has its own Alpaca account, bot and strategy P/L are mapped by account."
    )

    orders_for_analytics = get_orders_df(300)
    closed_trades_df = build_closed_trades_from_orders(orders_for_analytics)

    if closed_trades_df.empty:
        st.warning("No closed trades detected yet. This will populate after buy and sell orders both fill.")
    else:
        a1, a2, a3, a4 = st.columns(4)
        total_pl = closed_trades_df["P/L"].sum()
        closed_count = len(closed_trades_df)
        wins = int((closed_trades_df["P/L"] > 0).sum())
        losses = int((closed_trades_df["P/L"] < 0).sum())
        win_rate = (wins / closed_count * 100) if closed_count else 0

        a1.metric("Closed Trade P/L", money(total_pl))
        a2.metric("Closed Trades", closed_count)
        a3.metric("Win Rate", pct(win_rate))
        a4.metric("Wins / Losses", f"{wins} / {losses}")

        st.subheader("P/L by Account")
        by_account = summarize_closed_trades(closed_trades_df, ["Account"])
        st.dataframe(by_account, use_container_width=True, hide_index=True)
        fig_account = px.bar(by_account, x="Account", y="Total P/L", text="Total P/L")
        st.plotly_chart(fig_account, use_container_width=True)

        st.subheader("P/L by Bot")
        by_bot = summarize_closed_trades(closed_trades_df, ["Bot"])
        st.dataframe(by_bot, use_container_width=True, hide_index=True)
        fig_bot = px.bar(by_bot, x="Bot", y="Total P/L", text="Total P/L")
        st.plotly_chart(fig_bot, use_container_width=True)

        st.subheader("P/L by Strategy")
        by_strategy = summarize_closed_trades(closed_trades_df, ["Strategy"])
        st.dataframe(by_strategy, use_container_width=True, hide_index=True)
        fig_strategy = px.bar(by_strategy, x="Strategy", y="Total P/L", text="Total P/L")
        st.plotly_chart(fig_strategy, use_container_width=True)

        st.subheader("P/L by Symbol")
        by_symbol = summarize_closed_trades(closed_trades_df, ["Symbol"])
        st.dataframe(by_symbol, use_container_width=True, hide_index=True)
        fig_symbol = px.bar(by_symbol, x="Symbol", y="Total P/L", color="Symbol", text="Total P/L")
        st.plotly_chart(fig_symbol, use_container_width=True)

        st.subheader("Trade Count by Day")
        by_day = closed_trades_df.groupby(["Exit Date"]).size().reset_index(name="Closed Trades")
        st.dataframe(by_day.sort_values("Exit Date", ascending=False), use_container_width=True, hide_index=True)
        fig_day = px.bar(by_day, x="Exit Date", y="Closed Trades", text="Closed Trades")
        st.plotly_chart(fig_day, use_container_width=True)

        st.subheader("Win/Loss Stats by Account")
        by_account_result = closed_trades_df.groupby(["Account", "Result"]).size().reset_index(name="Count")
        st.dataframe(by_account_result, use_container_width=True, hide_index=True)
        fig_result = px.bar(by_account_result, x="Account", y="Count", color="Result", barmode="group")
        st.plotly_chart(fig_result, use_container_width=True)

        st.subheader("Closed Trade Detail")
        detail = closed_trades_df.sort_values("Exit Time", ascending=False).copy()
        for col in ["Entry Price", "Exit Price", "P/L"]:
            detail[col] = detail[col].map(lambda x: f"${x:,.2f}" if pd.notna(x) else "")
        detail["P/L %"] = detail["P/L %"].map(lambda x: f"{x:.2f}%" if pd.notna(x) else "")
        st.dataframe(detail, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Open vs Closed Trade Results")

    positions_for_analytics = get_positions_df()
    if not positions_for_analytics.empty and "Error" not in positions_for_analytics.columns:
        o1, o2, o3 = st.columns(3)
        o1.metric("Open Positions", len(positions_for_analytics))
        o2.metric("Open Unrealized P/L", money(positions_for_analytics["Unrealized P/L"].sum()))
        o3.metric("Open Market Value", money(positions_for_analytics["Market Value"].sum()))
        st.dataframe(positions_for_analytics, use_container_width=True, hide_index=True)
    else:
        st.info("No open positions detected.")

    st.divider()
    st.subheader("Rejected-Trade Analysis")

    if events_view_df.empty or "event_type" not in events_view_df.columns:
        st.warning("No bot event data available for rejection analysis.")
    else:
        rejected = events_view_df[events_view_df["event_type"] == "TRADE_REJECTED"].copy()
        if rejected.empty:
            st.success("No rejected trade events found in the loaded event window.")
        else:
            r1, r2, r3 = st.columns(3)
            r1.metric("Rejected Trades", len(rejected))
            r2.metric("Rejected Symbols", rejected["symbol"].nunique())
            r3.metric("Rejected Bots", rejected["bot_name"].nunique())

            st.subheader("Rejections by Reason")
            by_reason = rejected.groupby(["message"]).size().reset_index(name="Count").sort_values("Count", ascending=False)
            st.dataframe(by_reason, use_container_width=True, hide_index=True)
            fig_reason = px.bar(by_reason.head(15), x="Count", y="message", orientation="h")
            st.plotly_chart(fig_reason, use_container_width=True)

            st.subheader("Rejections by Bot and Symbol")
            by_reject_symbol = rejected.groupby(["bot_name", "symbol", "message"]).size().reset_index(name="Count").sort_values("Count", ascending=False)
            st.dataframe(by_reject_symbol, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Placed Trade Event Counts")

    if events_view_df.empty or "event_type" not in events_view_df.columns:
        st.warning("No bot event data available.")
    else:
        placed = events_view_df[events_view_df["event_type"] == "TRADE_PLACED"].copy()
        if placed.empty:
            st.info("No TRADE_PLACED events in the loaded event window.")
        else:
            placed["day"] = placed["created_at"].dt.date
            placed_counts = placed.groupby(["day", "bot_name", "symbol", "strategy"]).size().reset_index(name="Placed Trades")
            st.dataframe(placed_counts.sort_values("day", ascending=False), use_container_width=True, hide_index=True)


with tab_performance:
    st.subheader("Bot Event Summary")

    if events_view_df.empty or "event_type" not in events_view_df.columns:
        st.warning("No event data available yet.")
    else:
        event_counts = events_view_df.groupby(["bot_name", "event_type"]).size().reset_index(name="Count")
        st.dataframe(event_counts, use_container_width=True, hide_index=True)

        fig = px.bar(event_counts, x="bot_name", y="Count", color="event_type", barmode="group")
        st.plotly_chart(fig, use_container_width=True)

        trade_rows = events_view_df[events_view_df["event_type"].isin(["TRADE_PLACED", "TRADE_REJECTED"])]
        if not trade_rows.empty:
            st.subheader("Trade Events by Symbol")
            symbol_counts = trade_rows.groupby(["symbol", "event_type"]).size().reset_index(name="Count")
            st.dataframe(symbol_counts, use_container_width=True, hide_index=True)
            fig2 = px.bar(symbol_counts, x="symbol", y="Count", color="event_type", barmode="group")
            st.plotly_chart(fig2, use_container_width=True)

    st.subheader("Alpaca Order Summary by Account")
    orders_df = get_orders_df(100)
    if not orders_df.empty and "Error" not in orders_df.columns:
        order_counts = orders_df.groupby(["Account", "Symbol", "Side", "Status"]).size().reset_index(name="Count")
        st.dataframe(order_counts, use_container_width=True, hide_index=True)

with tab_setup:
    st.subheader("System Setup")

    st.markdown("""
### Multi-Account Alpaca Setup

Add up to three Alpaca accounts in `.env`:

```text
ACCOUNT_1_NAME=Alligator Bot Account
ACCOUNT_1_API_KEY=your_key
ACCOUNT_1_SECRET_KEY=your_secret
ACCOUNT_1_PAPER=true

ACCOUNT_2_NAME=TradingView QQQ TSLA AMD Account
ACCOUNT_2_API_KEY=your_key
ACCOUNT_2_SECRET_KEY=your_secret
ACCOUNT_2_PAPER=true

ACCOUNT_3_NAME=Alpaca Direct Scanner Account
ACCOUNT_3_API_KEY=your_key
ACCOUNT_3_SECRET_KEY=your_secret
ACCOUNT_3_PAPER=true
```

The old single-account setup still works, but it will only show one Alpaca account.

### Trade Analytics Notes
- Closed-trade P/L is estimated from filled Alpaca orders using FIFO matching.
- Bot and strategy P/L are mapped by account because each strategy has its own Alpaca account.
- Rejected-trade analysis comes from the shared `bot_events` database.
- For even cleaner strategy P/L later, we can add exit-order IDs and realized P/L directly into each bot's database logs.
    """)

    st.code("""
cd C:\\Users\\RodgerTucker\\coach-q-trading-dashboard
venv\\Scripts\\activate.bat
python -m streamlit run dashboard.py
    """, language="powershell")

