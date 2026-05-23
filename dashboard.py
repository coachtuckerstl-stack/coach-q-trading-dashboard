import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

try:
    from alpaca.trading.client import TradingClient
except Exception:
    TradingClient = None

try:
    from sqlalchemy import create_engine, text
except Exception:
    create_engine = None
    text = None



# Page Setup


st.set_page_config(page_title="Coach T Trading Command Center", layout="wide")

DATA_START_DATE = pd.Timestamp("2026-05-19", tz="UTC")
DATA_START_DATE_LABEL = "05/19/2026"


# Strategy / Account Config
# Railway-safe: no Windows C:\ paths required.


STRATEGY5_DAILY_PNL_URL = os.getenv(
    "STRATEGY5_DAILY_PNL_URL",
    "https://web-production-ab0c0.up.railway.app/daily-pnl",
)

BOTS = {
    "Breakout Momentum": {
        "bot_group": "DIRECT_SCANNER",
        "strategy": "breakout_momentum_v1",
        "model": "direct_breakout_live_v1",
        "type": "scanner",
        "api_key_var": "ACCOUNT_1_API_KEY",
        "secret_key_var": "ACCOUNT_1_SECRET_KEY",
        "paper_var": "ACCOUNT_1_PAPER",
        "account_name_var": "ACCOUNT_1_NAME",
        "log": Path("breakout_momentum_log.csv"),
        "old_log": Path("trade_log.csv"),
    },
    "Pullback Reclaim": {
        "bot_group": "DIRECT_SCANNER",
        "strategy": "pullback_reclaim_v1",
        "model": "direct_pullback_live_v1",
        "type": "scanner",
        "api_key_var": "ACCOUNT_1_API_KEY",
        "secret_key_var": "ACCOUNT_1_SECRET_KEY",
        "paper_var": "ACCOUNT_1_PAPER",
        "account_name_var": "ACCOUNT_1_NAME",
        "log": Path("pullback_reclaim_log.csv"),
        "old_log": Path("trade_log.csv"),
    },
    "HA 100 EMA Doji": {
        "bot_group": "TV_WEBHOOK",
        "strategy": "ha_100ema_doji_v1",
        "model": "tv_ha_100ema_doji_live_v1",
        "type": "webhook",
        "api_key_var": "ACCOUNT_2_API_KEY",
        "secret_key_var": "ACCOUNT_2_SECRET_KEY",
        "paper_var": "ACCOUNT_2_PAPER",
        "account_name_var": "ACCOUNT_2_NAME",
        "log": Path("ha_100ema_doji_log.csv"),
        "old_log": Path("trade_log.csv"),
    },
    "Alligator Trend": {
        "bot_group": "ALLIGATOR",
        "strategy": "alligator_trend_v1",
        "model": "alligator_live_v1",
        "type": "webhook",
        "api_key_var": "ACCOUNT_3_API_KEY",
        "secret_key_var": "ACCOUNT_3_SECRET_KEY",
        "paper_var": "ACCOUNT_3_PAPER",
        "account_name_var": "ACCOUNT_3_NAME",
        "log": Path("alligator_log.csv"),
        "old_log": Path("trade_log.csv"),
    },
    "Strategy 5 VWAP Reclaim Simulator": {
        "bot_group": "STRATEGY_5",
        "strategy": "strategy_5_simple_vwap_reclaim",
        "model": "strategy5_tradingview_simulator",
        "type": "simulator",
        "api_key_var": "",
        "secret_key_var": "",
        "paper_var": "",
        "account_name_var": "",
        "log": Path("strategy5_log.csv"),
        "old_log": Path("strategy5_trades.csv"),
    },
    "Strategy 6 Forex": {
        "bot_group": "STRATEGY_6",
        "strategy": "strategy_6_forex_top_down",
        "model": "development_not_running",
        "type": "development",
        "api_key_var": "",
        "secret_key_var": "",
        "paper_var": "",
        "account_name_var": "",
        "log": Path("strategy6_forex_log.csv"),
        "old_log": Path("strategy6_forex_backtests.csv"),
    },
}

STRATEGY_META = {
    "Breakout Momentum": {"number": "Strategy 1", "market": "Stocks", "mode": "Paper", "status": "Testing"},
    "Pullback Reclaim": {"number": "Strategy 2", "market": "Stocks", "mode": "Paper", "status": "Testing"},
    "HA 100 EMA Doji": {"number": "Strategy 3", "market": "Stocks", "mode": "Paper", "status": "Testing"},
    "Alligator Trend": {"number": "Strategy 4", "market": "Stocks", "mode": "Paper", "status": "Testing"},
    "Strategy 5 VWAP Reclaim Simulator": {"number": "Strategy 5", "market": "Stocks", "mode": "Simulation", "status": "Running"},
    "Strategy 6 Forex": {"number": "Strategy 6", "market": "Forex", "mode": "Development", "status": "Not Running"},
}



# General Helpers


def env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() not in ("false", "0", "no", "live")


def safe_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def load_log(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path)
        df.columns = [str(c).strip() for c in df.columns]
        return df
    except Exception as e:
        st.error(f"Could not load {path}: {e}")
        return pd.DataFrame()


def normalize_old_scanner_log(df: pd.DataFrame, strategy_name: str) -> pd.DataFrame:
    if df.empty:
        return df

    df = df.copy()

    if "decision" in df.columns and "status" not in df.columns:
        df["status"] = df["decision"]

    if "timestamp" in df.columns and "timestamp_et" not in df.columns:
        df["timestamp_et"] = df["timestamp"]

    if "reason" not in df.columns:
        df["reason"] = ""

    if "model" not in df.columns:
        df["model"] = strategy_name

    if "strategy" not in df.columns:
        if strategy_name == "Breakout Momentum":
            df["strategy"] = "breakout_momentum_v1"
        elif strategy_name == "Pullback Reclaim":
            df["strategy"] = "pullback_reclaim_v1"
        else:
            df["strategy"] = strategy_name

    if "date_et" not in df.columns and "timestamp_et" in df.columns:
        temp_dt = pd.to_datetime(df["timestamp_et"], errors="coerce")
        df["date_et"] = temp_dt.dt.strftime("%Y-%m-%d")

    return df


def parse_datetime_column(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    for col in [
        "timestamp_et",
        "timestamp",
        "filled_at",
        "submitted_at",
        "time",
        "datetime",
        "created_at",
        "date",
    ]:
        if col in df.columns:
            df["_dt"] = pd.to_datetime(df[col], errors="coerce")
            return df

    df["_dt"] = pd.NaT
    return df


def filter_from_start_date(df: pd.DataFrame) -> pd.DataFrame:
    """
    Keeps only rows from DATA_START_DATE forward.
    Uses the first available date/time column it can find.
    """
    if df.empty:
        return df

    df = df.copy()

    possible_cols = [
        "timestamp_et",
        "timestamp",
        "filled_at",
        "submitted_at",
        "synced_at",
        "exit_time",
        "entry_time",
        "created_at",
        "date_et",
        "date",
    ]

    date_col = None

    for col in possible_cols:
        if col in df.columns:
            date_col = col
            break

    if date_col is None:
        return df

    parsed = pd.to_datetime(df[date_col], errors="coerce", utc=True)

    df["_filter_dt"] = parsed
    df = df[df["_filter_dt"] >= DATA_START_DATE]
    df = df.drop(columns=["_filter_dt"], errors="ignore")

    return df


def get_today_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    if "date_et" in df.columns:
        today = datetime.now().strftime("%Y-%m-%d")
        return df[df["date_et"].astype(str) == today]

    df = parse_datetime_column(df.copy())
    today_date = pd.Timestamp.now().date()
    return df[df["_dt"].dt.date == today_date]


def count_status(df: pd.DataFrame, status_value: str) -> int:
    if df.empty or "status" not in df.columns:
        return 0
    return int((df["status"].astype(str).str.upper() == status_value.upper()).sum())


def get_win_rate_from_pnl(df: pd.DataFrame, pnl_col: str = "realized_pnl") -> float:
    if df.empty or pnl_col not in df.columns:
        return 0.0
    vals = pd.to_numeric(df[pnl_col], errors="coerce").dropna()
    if vals.empty:
        return 0.0
    return round(float((vals > 0).mean() * 100), 2)


def get_last_event(df: pd.DataFrame):
    if df.empty:
        return "No log data"
    df = parse_datetime_column(df.copy())
    if "_dt" not in df.columns or df["_dt"].dropna().empty:
        return "Unknown"
    return df["_dt"].dropna().max().strftime("%Y-%m-%d %H:%M:%S")



# Alpaca Helpers


def get_alpaca_client(bot_info: dict | None = None):
    if TradingClient is None:
        return None, "Missing alpaca-py package"

    if not bot_info or bot_info.get("type") in ("simulator", "development"):
        return None, "This strategy is not connected to Alpaca positions"

    key = os.getenv(bot_info.get("api_key_var", ""), "")
    secret = os.getenv(bot_info.get("secret_key_var", ""), "")
    paper_text = os.getenv(bot_info.get("paper_var", ""), "true")
    paper = str(paper_text).lower() != "false"

    if not key or not secret:
        return None, "Missing Alpaca API key/secret"

    try:
        return TradingClient(api_key=key, secret_key=secret, paper=paper), ""
    except Exception as e:
        return None, str(e)


def load_alpaca_account(bot_info: dict | None = None):
    client, err = get_alpaca_client(bot_info)
    if client is None:
        return None, err

    try:
        return client.get_account(), ""
    except Exception as e:
        return None, str(e)


def load_alpaca_positions(bot_info: dict | None = None):
    client, err = get_alpaca_client(bot_info)
    if client is None:
        return [], err

    try:
        return client.get_all_positions(), ""
    except Exception as e:
        return [], str(e)


def load_unique_open_positions() -> pd.DataFrame:
    """
    Railway-safe position loader.

    The same Alpaca account can be used by more than one strategy.
    This de-duplicates by account environment variable + symbol so the same
    position does not show two or three times.
    """
    rows = []
    seen = set()

    for name, info in BOTS.items():
        if info.get("type") in ("simulator", "development"):
            continue

        account_key = info.get("api_key_var", "")
        positions, err = load_alpaca_positions(info)

        if err:
            rows.append({
                "Strategy Source": name,
                "Account": os.getenv(info.get("account_name_var", ""), account_key),
                "Symbol": "",
                "Qty": "",
                "Market Value": "",
                "Avg Entry": "",
                "Unrealized P/L": "",
                "Unrealized %": "",
                "Status": err,
            })
            continue

        for p in positions:
            dedupe_key = (account_key, str(p.symbol))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            rows.append({
                "Strategy Source": name,
                "Account": os.getenv(info.get("account_name_var", ""), account_key),
                "Symbol": p.symbol,
                "Qty": p.qty,
                "Market Value": p.market_value,
                "Avg Entry": p.avg_entry_price,
                "Unrealized P/L": p.unrealized_pl,
                "Unrealized %": p.unrealized_plpc,
                "Status": "OK",
            })

    return pd.DataFrame(rows)


# Closed Trade Sync / Load


def run_closed_trade_sync():
    """
    Railway-safe sync. GitHub repo has sync_closed_trades_v2.py.
    """
    script = Path("sync_closed_trades_v2.py")

    if not script.exists():
        return False, "sync_closed_trades_v2.py not found."

    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=90,
        )
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        return result.returncode == 0, output.strip()
    except Exception as e:
        return False, str(e)


def run_dashboard_sync():
    ok, msg = run_closed_trade_sync()

    st.session_state["last_sync_ok"] = ok
    st.session_state["last_sync_msg"] = msg
    st.session_state["last_sync_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return ok, msg


def auto_sync_once_on_open():
    if "dashboard_synced_once" not in st.session_state:
        with st.spinner("Opening dashboard and syncing Railway trade history..."):
            run_dashboard_sync()
        st.session_state["dashboard_synced_once"] = True


def load_closed_trades() -> pd.DataFrame:
    path = Path("closed_trades.csv")
    if not path.exists():
        return pd.DataFrame()

    df = load_log(path)

    if not df.empty:
        for col in ["qty", "filled_qty", "filled_avg_price"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        for col in ["filled_at", "submitted_at", "synced_at"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)

        for col in ["strategy", "model", "bot_group"]:
            if col not in df.columns:
                df[col] = ""

        if "source_env" in df.columns:
            df["source_env"] = df["source_env"].astype(str)

    return filter_from_start_date(df)


def pair_realized_trades(closed_df: pd.DataFrame) -> pd.DataFrame:
    if closed_df.empty:
        return pd.DataFrame()

    df = closed_df.copy()
    required = {"symbol", "side", "filled_qty", "filled_avg_price", "filled_at"}

    if not required.issubset(set(df.columns)):
        return pd.DataFrame()

    df = df.dropna(subset=["symbol", "side", "filled_qty", "filled_avg_price", "filled_at"])
    df = df[df["filled_qty"] > 0]
    df = df.sort_values("filled_at")

    open_lots = {}
    realized = []

    for _, row in df.iterrows():
        symbol = str(row["symbol"]).upper()
        side = str(row["side"]).lower().replace("orderside.", "")
        qty = float(row["filled_qty"])
        price = float(row["filled_avg_price"])
        filled_at = row["filled_at"]

        if qty <= 0:
            continue

        if symbol not in open_lots:
            open_lots[symbol] = []

        if "buy" in side:
            open_lots[symbol].append({
                "qty": qty,
                "price": price,
                "time": filled_at,
                "bot_group": row.get("bot_group", ""),
                "strategy": row.get("strategy", ""),
                "model": row.get("model", ""),
                "source_env": row.get("source_env", ""),
                "client_order_id": row.get("client_order_id", ""),
            })
            continue

        if "sell" in side:
            remaining = qty

            while remaining > 0 and open_lots[symbol]:
                lot = open_lots[symbol][0]
                matched_qty = min(remaining, lot["qty"])
                pnl = (price - lot["price"]) * matched_qty
                pnl_pct = ((price - lot["price"]) / lot["price"]) * 100 if lot["price"] else 0

                if pd.notna(filled_at) and pd.notna(lot["time"]):
                    duration_min = (filled_at - lot["time"]).total_seconds() / 60
                else:
                    duration_min = None

                realized.append({
                    "source_env": lot.get("source_env", ""),
                    "bot_group": lot.get("bot_group", ""),
                    "strategy": lot.get("strategy", ""),
                    "model": lot.get("model", ""),
                    "symbol": symbol,
                    "qty": matched_qty,
                    "entry_time": lot["time"],
                    "exit_time": filled_at,
                    "entry_price": lot["price"],
                    "exit_price": price,
                    "realized_pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "duration_minutes": round(duration_min, 2) if duration_min is not None else "",
                    "grade": grade_trade(pnl, pnl_pct),
                    "notes": grade_notes(pnl, pnl_pct),
                    "client_order_id": lot.get("client_order_id", ""),
                })

                lot["qty"] -= matched_qty
                remaining -= matched_qty

                if lot["qty"] <= 0.000001:
                    open_lots[symbol].pop(0)

    return pd.DataFrame(realized)


# Strategy 5 / Postgres Optional Load


def get_database_engine():
    if create_engine is None:
        return None, "SQLAlchemy not installed"

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        return None, "DATABASE_URL not configured"

    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    try:
        return create_engine(database_url, pool_pre_ping=True), ""
    except Exception as e:
        return None, str(e)


def load_strategy5_events_from_postgres() -> pd.DataFrame:
    engine, err = get_database_engine()
    if engine is None:
        return pd.DataFrame()

    try:
        query = text("""
            SELECT *
            FROM trade_events
            WHERE source = 'strategy_5'
               OR strategy IN (
                    'strategy_5',
                    'strategy_5_orb_vwap',
                    'strategy_5_simple_vwap_reclaim'
               )
            ORDER BY timestamp_et DESC
            LIMIT 1000
        """)
        return pd.read_sql(query, engine)
    except Exception:
        return pd.DataFrame()


def load_strategy5_events() -> pd.DataFrame:
    """
    First tries Postgres trade_events.
    Falls back to CSV files in the dashboard repo, if present.
    """
    pg_df = load_strategy5_events_from_postgres()
    if not pg_df.empty:
        return filter_from_start_date(pg_df)

    for path in [Path("strategy5_log.csv"), Path("strategy5_trades.csv"), Path("unified_trade_log.csv")]:
        df = load_log(path)
        if not df.empty:
            if "strategy" in df.columns:
                mask = df["strategy"].astype(str).str.contains("strategy_5|orb_vwap", case=False, na=False)
                df = df[mask]
            return filter_from_start_date(df)

    return pd.DataFrame()



# Reporting Helpers


def grade_trade(pnl: float, pnl_pct: float) -> str:
    if pnl > 0 and pnl_pct >= 1:
        return "A"
    if pnl > 0:
        return "B"
    if pnl == 0:
        return "C"
    if pnl_pct > -1:
        return "D"
    return "F"


def grade_notes(pnl: float, pnl_pct: float) -> str:
    if pnl > 0 and pnl_pct >= 1:
        return "Strong winner. Review setup for repeatability."
    if pnl > 0:
        return "Small winner. Good risk control."
    if pnl == 0:
        return "Flat trade."
    if pnl_pct > -1:
        return "Small loss. Acceptable if within planned risk."
    return "Larger loss. Review entry, stop, and timing."


def add_quality_scores(realized_df: pd.DataFrame) -> pd.DataFrame:
    if realized_df.empty:
        return realized_df

    df = realized_df.copy()
    df["realized_pnl"] = pd.to_numeric(df["realized_pnl"], errors="coerce").fillna(0)
    df["pnl_pct"] = pd.to_numeric(df.get("pnl_pct", 0), errors="coerce").fillna(0)
    df["duration_minutes"] = pd.to_numeric(df.get("duration_minutes", 0), errors="coerce").fillna(0)

    def quality(row):
        score = 50
        score += min(max(row["realized_pnl"], -50), 50)
        score += min(max(row["pnl_pct"] * 10, -20), 20)

        if row["duration_minutes"] <= 5 and row["realized_pnl"] < 0:
            score -= 10
        if row["duration_minutes"] > 240:
            score -= 5
        if row["realized_pnl"] > 0 and row["duration_minutes"] <= 120:
            score += 5

        return round(max(0, min(100, score)), 2)

    df["quality_score"] = df.apply(quality, axis=1)

    def quality_grade(score):
        if score >= 85:
            return "A"
        if score >= 70:
            return "B"
        if score >= 55:
            return "C"
        if score >= 40:
            return "D"
        return "F"

    df["quality_grade"] = df["quality_score"].apply(quality_grade)
    return df


def classify_rejection(reason: str) -> str:
    text_value = str(reason).lower()

    if "market closed" in text_value or "after 3:30" in text_value or "time" in text_value or "outside" in text_value:
        return "MARKET_TIME_BLOCK"
    if "buying power" in text_value or "insufficient" in text_value or "not enough" in text_value:
        return "BUYING_POWER_OR_QTY"
    if "stop" in text_value and ("too close" in text_value or "base_price" in text_value or "invalid" in text_value):
        return "STOP_PRICE_ISSUE"
    if "take_profit" in text_value or "take profit" in text_value or "limit_price" in text_value:
        return "TAKE_PROFIT_ISSUE"
    if "json" in text_value or "payload" in text_value or "secret" in text_value:
        return "WEBHOOK_PAYLOAD_ISSUE"
    if "position" in text_value or "open order" in text_value or "duplicate" in text_value:
        return "DUPLICATE_OR_OPEN_POSITION"
    if "unauthorized" in text_value or "401" in text_value:
        return "API_KEY_AUTH_ISSUE"

    return "OTHER_REJECTION"


def build_daily_recap(realized_df: pd.DataFrame, rejected_df: pd.DataFrame) -> str:
    today = datetime.now().strftime("%Y-%m-%d")

    if realized_df.empty:
        pnl = 0
        trades = 0
        wins = 0
        losses = 0
        win_rate = 0
        best = "N/A"
        worst = "N/A"
    else:
        temp = realized_df.copy()
        temp["exit_time"] = pd.to_datetime(temp["exit_time"], errors="coerce", utc=True)
        today_df = temp[temp["exit_time"].dt.strftime("%Y-%m-%d") == today]
        if today_df.empty:
            today_df = temp

        pnl = today_df["realized_pnl"].sum()
        trades = len(today_df)
        wins = int((today_df["realized_pnl"] > 0).sum())
        losses = int((today_df["realized_pnl"] < 0).sum())
        win_rate = round((wins / trades) * 100, 2) if trades else 0
        best = today_df.sort_values("realized_pnl", ascending=False).iloc[0]["symbol"] if trades else "N/A"
        worst = today_df.sort_values("realized_pnl", ascending=True).iloc[0]["symbol"] if trades else "N/A"

    rejected_count = len(rejected_df) if not rejected_df.empty else 0
    tone = "positive" if pnl > 0 else "negative" if pnl < 0 else "flat"

    return (
        f"Daily AI-Style Recap for {today}\n\n"
        f"Overall day was {tone}.\n"
        f"Realized P/L: ${pnl:,.2f}\n"
        f"Closed trades analyzed: {trades}\n"
        f"Wins: {wins} | Losses: {losses} | Win rate: {win_rate}%\n"
        f"Best symbol: {best}\n"
        f"Worst symbol: {worst}\n"
        f"Rejected orders/signals found: {rejected_count}\n\n"
        f"Coaching note: focus on whether losses stayed small and whether winners came from repeatable setups. "
        f"Use the Trade Replay tab to review the best and worst trades before changing strategy rules."
    )


def build_symbol_intelligence(realized_df: pd.DataFrame) -> pd.DataFrame:
    if realized_df.empty:
        return pd.DataFrame()

    df = realized_df.copy()
    df["realized_pnl"] = pd.to_numeric(df["realized_pnl"], errors="coerce").fillna(0)

    out = df.groupby("symbol").agg(
        trades=("symbol", "count"),
        total_pnl=("realized_pnl", "sum"),
        avg_pnl=("realized_pnl", "mean"),
        wins=("realized_pnl", lambda x: int((x > 0).sum())),
        losses=("realized_pnl", lambda x: int((x < 0).sum())),
        win_rate=("realized_pnl", lambda x: round((x > 0).mean() * 100, 2)),
        best_trade=("realized_pnl", "max"),
        worst_trade=("realized_pnl", "min"),
    ).reset_index()

    out["symbol_score"] = (
        out["win_rate"] * 0.35
        + out["total_pnl"].clip(-200, 200) * 0.25
        + out["trades"] * 2
        + out["avg_pnl"].clip(-50, 50) * 0.20
    ).round(2)

    return out.sort_values(["symbol_score", "total_pnl"], ascending=False)


def build_time_of_day_analysis(realized_df: pd.DataFrame) -> pd.DataFrame:
    if realized_df.empty:
        return pd.DataFrame()

    df = realized_df.copy()
    df["exit_time"] = pd.to_datetime(df["exit_time"], errors="coerce", utc=True)
    df = df.dropna(subset=["exit_time"])

    if df.empty:
        return pd.DataFrame()

    def bucket_time(ts):
        hour = ts.hour
        minute = ts.minute
        t = hour * 60 + minute

        if t < 9 * 60 + 45:
            return "Pre/Opening 15"
        if t < 10 * 60 + 30:
            return "Morning Momentum"
        if t < 12 * 60:
            return "Late Morning"
        if t < 14 * 60:
            return "Midday"
        if t < 15 * 60 + 30:
            return "Afternoon"
        return "Power Hour / Close"

    df["time_bucket"] = df["exit_time"].apply(bucket_time)
    df["realized_pnl"] = pd.to_numeric(df["realized_pnl"], errors="coerce").fillna(0)

    out = df.groupby("time_bucket").agg(
        trades=("symbol", "count"),
        total_pnl=("realized_pnl", "sum"),
        avg_pnl=("realized_pnl", "mean"),
        win_rate=("realized_pnl", lambda x: round((x > 0).mean() * 100, 2)),
        best_trade=("realized_pnl", "max"),
        worst_trade=("realized_pnl", "min"),
    ).reset_index()

    order = {
        "Pre/Opening 15": 1,
        "Morning Momentum": 2,
        "Late Morning": 3,
        "Midday": 4,
        "Afternoon": 5,
        "Power Hour / Close": 6,
    }
    out["sort_order"] = out["time_bucket"].map(order).fillna(99)
    return out.sort_values("sort_order").drop(columns=["sort_order"])


def build_strategy_heatmap(realized_df: pd.DataFrame) -> pd.DataFrame:
    if realized_df.empty:
        return pd.DataFrame()

    df = realized_df.copy()
    df["exit_time"] = pd.to_datetime(df["exit_time"], errors="coerce", utc=True)
    df = df.dropna(subset=["exit_time"])

    if df.empty:
        return pd.DataFrame()

    df["date"] = df["exit_time"].dt.strftime("%Y-%m-%d")
    df["realized_pnl"] = pd.to_numeric(df["realized_pnl"], errors="coerce").fillna(0)

    return df.pivot_table(
        index="date",
        columns="symbol",
        values="realized_pnl",
        aggfunc="sum",
        fill_value=0,
    )


def build_ai_recommendations(realized_df: pd.DataFrame, rejected_df: pd.DataFrame) -> pd.DataFrame:
    recommendations = []

    if not realized_df.empty:
        symbol_df = build_symbol_intelligence(realized_df)
        time_df = build_time_of_day_analysis(realized_df)

        if not symbol_df.empty:
            worst_symbol = symbol_df.sort_values("total_pnl").iloc[0]
            best_symbol = symbol_df.sort_values("total_pnl", ascending=False).iloc[0]

            recommendations.append({
                "Priority": "High",
                "Area": "Symbol Selection",
                "Finding": f"Worst symbol is {worst_symbol['symbol']} with ${worst_symbol['total_pnl']:.2f} P/L.",
                "Recommendation": f"Reduce or pause {worst_symbol['symbol']} until more data improves.",
                "Confidence": "Medium",
                "Manual Action Needed": "Review losing trades before blocking symbol.",
            })

            recommendations.append({
                "Priority": "Medium",
                "Area": "Symbol Selection",
                "Finding": f"Best symbol is {best_symbol['symbol']} with ${best_symbol['total_pnl']:.2f} P/L.",
                "Recommendation": f"Keep tracking {best_symbol['symbol']} as a preferred test symbol.",
                "Confidence": "Medium",
                "Manual Action Needed": "Do not increase size yet; collect more trades.",
            })

        if not time_df.empty:
            worst_time = time_df.sort_values("total_pnl").iloc[0]
            best_time = time_df.sort_values("total_pnl", ascending=False).iloc[0]

            recommendations.append({
                "Priority": "High",
                "Area": "Time of Day",
                "Finding": f"Weakest window is {worst_time['time_bucket']} with ${worst_time['total_pnl']:.2f} P/L.",
                "Recommendation": f"Consider blocking or reducing trades during {worst_time['time_bucket']}.",
                "Confidence": "Medium",
                "Manual Action Needed": "Confirm sample size before changing bot rules.",
            })

            recommendations.append({
                "Priority": "Medium",
                "Area": "Time of Day",
                "Finding": f"Best window is {best_time['time_bucket']} with ${best_time['total_pnl']:.2f} P/L.",
                "Recommendation": f"Favor monitoring trades during {best_time['time_bucket']}.",
                "Confidence": "Medium",
                "Manual Action Needed": "Do not change sizing yet.",
            })

        if "quality_grade" in realized_df.columns:
            low_quality = realized_df[realized_df["quality_grade"].isin(["D", "F"])]

            if not low_quality.empty:
                recommendations.append({
                    "Priority": "High",
                    "Area": "Trade Quality",
                    "Finding": f"{len(low_quality)} trades graded D/F.",
                    "Recommendation": "Review lowest-quality trades in Trade Replay before changing strategy rules.",
                    "Confidence": "High",
                    "Manual Action Needed": "Inspect entries, exits, time window, and symbol.",
                })

    if not rejected_df.empty:
        reason_col = "reason" if "reason" in rejected_df.columns else "message" if "message" in rejected_df.columns else None

        if reason_col:
            temp = rejected_df.copy()
            temp["rejection_class"] = temp[reason_col].apply(classify_rejection)
            top_class = temp["rejection_class"].value_counts().idxmax()
            top_count = int(temp["rejection_class"].value_counts().max())

            recommendations.append({
                "Priority": "High",
                "Area": "Rejected Orders",
                "Finding": f"Top rejection class is {top_class} with {top_count} occurrences.",
                "Recommendation": "Fix the largest rejection category before optimizing strategy settings.",
                "Confidence": "High",
                "Manual Action Needed": "Review Rejection Intelligence tab.",
            })

    if not recommendations:
        recommendations.append({
            "Priority": "Low",
            "Area": "Data Collection",
            "Finding": "Not enough usable data yet.",
            "Recommendation": "Keep bots running in paper mode and collect more trades.",
            "Confidence": "High",
            "Manual Action Needed": "No strategy changes yet.",
        })

    return pd.DataFrame(recommendations)


def build_weekly_review(realized_df: pd.DataFrame) -> pd.DataFrame:
    if realized_df.empty or "exit_time" not in realized_df.columns:
        return pd.DataFrame()

    df = realized_df.copy()
    df["exit_time"] = pd.to_datetime(df["exit_time"], errors="coerce", utc=True)
    df = df.dropna(subset=["exit_time"])

    last_week = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=7)
    df = df[df["exit_time"] >= last_week]

    if df.empty:
        return pd.DataFrame()

    strategy_col = "strategy" if "strategy" in df.columns else "source_env"
    if strategy_col not in df.columns:
        df["strategy"] = "unknown"
        strategy_col = "strategy"

    summary = []

    for strategy in df[strategy_col].fillna("unknown").unique():
        s = df[df[strategy_col].fillna("unknown") == strategy]

        trades = len(s)
        pnl = s["realized_pnl"].sum()
        avg_pnl = s["realized_pnl"].mean()
        wins = (s["realized_pnl"] > 0).sum()
        win_rate = round((wins / trades) * 100, 2) if trades else 0
        quality_avg = s["quality_score"].mean() if "quality_score" in s.columns else 0

        summary.append({
            "Strategy": strategy,
            "Trades": trades,
            "Win Rate %": win_rate,
            "Total P/L": round(pnl, 2),
            "Average Trade": round(avg_pnl, 2),
            "Best Trade": round(s["realized_pnl"].max(), 2),
            "Worst Trade": round(s["realized_pnl"].min(), 2),
            "Average Quality Score": round(quality_avg, 2),
        })

    return pd.DataFrame(summary).sort_values("Total P/L", ascending=False)


def build_parameter_suggestions(realized_df: pd.DataFrame) -> pd.DataFrame:
    suggestions = []

    if realized_df.empty:
        return pd.DataFrame()

    for symbol in realized_df["symbol"].dropna().unique():
        s = realized_df[realized_df["symbol"] == symbol]

        trades = len(s)
        pnl = s["realized_pnl"].sum()
        win_rate = round((s["realized_pnl"] > 0).mean() * 100, 2)

        if trades >= 5 and pnl < 0:
            suggestions.append({
                "Type": "Reduce Exposure",
                "Target": symbol,
                "Reason": f"${pnl:.2f} total P/L across {trades} trades.",
                "Suggestion": "Reduce trade frequency or temporarily pause this symbol.",
            })

        if trades >= 5 and win_rate < 35:
            suggestions.append({
                "Type": "Poor Win Rate",
                "Target": symbol,
                "Reason": f"{win_rate}% win rate.",
                "Suggestion": "Review entries and avoid weak time windows.",
            })

    return pd.DataFrame(suggestions)


def build_do_not_trade(realized_df: pd.DataFrame) -> pd.DataFrame:
    if realized_df.empty:
        return pd.DataFrame()

    grouped = realized_df.groupby("symbol").agg(
        trades=("symbol", "count"),
        total_pnl=("realized_pnl", "sum"),
        win_rate=("realized_pnl", lambda x: round((x > 0).mean() * 100, 2)),
    ).reset_index()

    blocked = grouped[
        (grouped["trades"] >= 5)
        & (
            (grouped["total_pnl"] < -50)
            | (grouped["win_rate"] < 25)
        )
    ]

    return blocked.sort_values("total_pnl")


def build_confidence_score(realized_df: pd.DataFrame) -> float:
    if realized_df.empty:
        return 0

    pnl = realized_df["realized_pnl"].sum()
    win_rate = (realized_df["realized_pnl"] > 0).mean() * 100
    trades = len(realized_df)

    score = 50
    score += min(max(pnl / 10, -20), 20)
    score += min(max((win_rate - 50), -20), 20)
    score += min(trades, 20)

    return round(max(0, min(100, score)), 2)


def build_tomorrow_plan(realized_df: pd.DataFrame) -> list:
    plan = []

    if realized_df.empty:
        return ["Collect more trade data before making adjustments."]

    total_pnl = realized_df["realized_pnl"].sum()

    if total_pnl > 0:
        plan.append("Keep current strategy sizing stable.")
    else:
        plan.append("Reduce aggression and focus on high-quality setups.")

    best_symbols = (
        realized_df.groupby("symbol")["realized_pnl"]
        .sum()
        .sort_values(ascending=False)
        .head(3)
    )

    if not best_symbols.empty:
        plan.append(f"Focus watchlist on: {', '.join(best_symbols.index.tolist())}")

    return plan


def build_v9_recap(realized_df: pd.DataFrame, rejected_df: pd.DataFrame) -> str:
    base = build_daily_recap(realized_df, rejected_df)

    symbol_df = build_symbol_intelligence(realized_df)
    time_df = build_time_of_day_analysis(realized_df)

    if not symbol_df.empty:
        best_symbol = symbol_df.iloc[0]
        worst_symbol = symbol_df.sort_values("total_pnl").iloc[0]
        symbol_text = (
            f"\n\nSymbol Intelligence:\n"
            f"Best symbol by score: {best_symbol['symbol']} "
            f"(${best_symbol['total_pnl']:.2f}, {best_symbol['win_rate']}% win rate)\n"
            f"Worst symbol by P/L: {worst_symbol['symbol']} "
            f"(${worst_symbol['total_pnl']:.2f}, {worst_symbol['win_rate']}% win rate)"
        )
    else:
        symbol_text = "\n\nSymbol Intelligence:\nNo realized symbol data yet."

    if not time_df.empty:
        best_time = time_df.sort_values("total_pnl", ascending=False).iloc[0]
        worst_time = time_df.sort_values("total_pnl").iloc[0]
        time_text = (
            f"\n\nTime-of-Day Intelligence:\n"
            f"Best time bucket: {best_time['time_bucket']} (${best_time['total_pnl']:.2f})\n"
            f"Worst time bucket: {worst_time['time_bucket']} (${worst_time['total_pnl']:.2f})"
        )
    else:
        time_text = "\n\nTime-of-Day Intelligence:\nNo time-of-day data yet."

    if not rejected_df.empty:
        reason_col = "reason" if "reason" in rejected_df.columns else "message" if "message" in rejected_df.columns else None
        if reason_col:
            temp = rejected_df.copy()
            temp["rejection_class"] = temp[reason_col].apply(classify_rejection)
            top_rej = temp["rejection_class"].value_counts().idxmax()
            rej_text = f"\n\nRejection Intelligence:\nTop rejection class: {top_rej}"
        else:
            rej_text = "\n\nRejection Intelligence:\nRejected rows exist, but no reason/message column was found."
    else:
        rej_text = "\n\nRejection Intelligence:\nNo rejected orders found."

    next_steps = (
        "\n\nSuggested Focus Tomorrow:\n"
        "1. Favor the best-performing symbols until the data says otherwise.\n"
        "2. Avoid or reduce size on the weakest symbol/time bucket.\n"
        "3. Fix the top rejection class before adding new strategy rules.\n"
        "4. Review the lowest quality-grade trade in Trade Replay."
    )

    return base + symbol_text + time_text + rej_text + next_steps


# Load Data


auto_sync_once_on_open()

bot_data = {}
for name, info in BOTS.items():
    df = load_log(info["log"])
    if df.empty and "old_log" in info:
        df = load_log(info["old_log"])
        if info.get("bot_group") == "DIRECT_SCANNER":
            df = normalize_old_scanner_log(df, name)

    if info.get("type") == "simulator" and df.empty:
        df = load_strategy5_events()

    df = filter_from_start_date(df)

    bot_data[name] = df

closed_trades_df = load_closed_trades()
realized_df = add_quality_scores(pair_realized_trades(closed_trades_df))
strategy5_df = bot_data.get("Strategy 5 VWAP Reclaim Simulator", pd.DataFrame())

# Build rejected dataframe
rejected_parts = []
for name, df in bot_data.items():
    if df.empty:
        continue

    temp = df.copy()
    temp["Strategy Name"] = name

    if "status" in temp.columns:
        temp = temp[temp["status"].astype(str).str.upper() == "REJECTED"]
    elif "decision" in temp.columns:
        temp = temp[temp["decision"].astype(str).str.upper() == "REJECTED"]
    else:
        temp = pd.DataFrame()

    if not temp.empty:
        rejected_parts.append(temp)

rejected_df = pd.concat(rejected_parts, ignore_index=True, sort=False) if rejected_parts else pd.DataFrame()


# Sidebar / Header


st.sidebar.title("Ops Controls")
st.sidebar.caption("Railway dashboard syncs automatically on open and when Refresh Dashboard is clicked.")

if "last_sync_time" in st.session_state:
    if st.session_state.get("last_sync_ok"):
        st.sidebar.success(f"Last sync: {st.session_state['last_sync_time']}")
    else:
        st.sidebar.error(f"Last sync failed: {st.session_state['last_sync_time']}")

    with st.sidebar.expander("Last sync message"):
        st.code(st.session_state.get("last_sync_msg", ""))

def fetch_strategy5_daily_pnl():
    try:
        with urllib.request.urlopen(STRATEGY5_DAILY_PNL_URL, timeout=8) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw)

        if not data.get("ok"):
            return None, data.get("error", "Strategy 5 daily P/L endpoint returned ok=false")

        return data.get("summary", {}), None

    except urllib.error.URLError as exc:
        return None, f"Could not reach Strategy 5 daily P/L endpoint: {exc}"

    except Exception as exc:
        return None, f"Could not load Strategy 5 daily P/L: {exc}"


def get_daily_pnl_for_strategy(strategy_name, strategy5_summary):
    """Return today's P/L where it can be attributed accurately."""
    info = BOTS.get(strategy_name, {})

    if info.get("type") == "development":
        return None, "Development / no trades"

    if info.get("type") == "simulator":
        if strategy5_summary:
            return safe_float(strategy5_summary.get("realized_pnl"), 0.0), "Simulator endpoint"
        return None, "P/L unavailable"

    account_var = info.get("api_key_var", "")
    account_strategies = [
        bot_name for bot_name, bot in BOTS.items()
        if bot.get("type") not in ("simulator", "development")
        and bot.get("api_key_var", "") == account_var
    ]
    account_name = os.getenv(info.get("account_name_var", ""), "").strip()
    has_dedicated_account = len(account_strategies) == 1 and bool(account_name)

    if realized_df.empty or "realized_pnl" not in realized_df.columns:
        if has_dedicated_account:
            return 0.0, "No closed trades today"
        return None, "Needs strategy-tagged exits"

    temp = realized_df.copy()
    temp["exit_time"] = pd.to_datetime(temp["exit_time"], errors="coerce", utc=True)
    temp = temp.dropna(subset=["exit_time"])

    if temp.empty:
        if has_dedicated_account:
            return 0.0, "No closed trades today"
        return None, "Needs strategy-tagged exits"

    today_central = pd.Timestamp.now(tz="America/Chicago").date()
    temp["_local_date"] = temp["exit_time"].dt.tz_convert("America/Chicago").dt.date
    temp = temp[temp["_local_date"] == today_central]

    if temp.empty:
        if has_dedicated_account:
            return 0.0, "No closed trades today"
        return None, "Needs strategy-tagged exits"

    strategy_id = str(info.get("strategy", "")).lower()
    model_id = str(info.get("model", "")).lower()
    bot_group = str(info.get("bot_group", "")).lower()

    exact_mask = pd.Series(False, index=temp.index)
    if "strategy" in temp.columns and strategy_id:
        exact_mask |= temp["strategy"].astype(str).str.lower().eq(strategy_id)
    if "model" in temp.columns and model_id:
        exact_mask |= temp["model"].astype(str).str.lower().eq(model_id)
    if "bot_group" in temp.columns and bot_group:
        exact_mask |= temp["bot_group"].astype(str).str.lower().eq(bot_group)

    exact_rows = temp[exact_mask]
    if not exact_rows.empty:
        pnl = pd.to_numeric(exact_rows["realized_pnl"], errors="coerce").fillna(0).sum()
        return float(pnl), "Strategy-tagged fills"

    if has_dedicated_account and "source_env" in temp.columns:
        account_rows = temp[temp["source_env"].astype(str) == account_name]
        if not account_rows.empty:
            pnl = pd.to_numeric(account_rows["realized_pnl"], errors="coerce").fillna(0).sum()
            return float(pnl), "Dedicated Alpaca account"
        return 0.0, "No closed trades today"

    return None, "Needs strategy-tagged exits"

def format_daily_pnl(value):
    return "Pending" if value is None else f"${value:,.2f}"


st.title("Coach T Trading Command Center")
st.caption(f"Last refreshed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
st.info(f"Dashboard is filtering all report data from {DATA_START_DATE_LABEL} forward.")

if st.button("Refresh Dashboard"):
    with st.spinner("Syncing Railway trade history and refreshing dashboard..."):
        run_dashboard_sync()
    st.rerun()



# Tabs


tabs = st.tabs([
    "Command Center",
    "Positions",
    "Closed Trades",
    "Realized P/L",
    "Strategy 5",
    "Trade Replay",
    "Daily AI Recap",
    "AI Recommendations",
    "Weekly Review",
    "AI Decision Center",
    "Strategy Scoring",
    "Symbol Intelligence",
    "Time of Day",
    "Rejection Intelligence",
    "Strategy Heatmap",
    "Rejected Orders",
    "Bot Health",
    "Daily Reports",
    "Raw Logs",
    "Strategy 6 Forex",
])


with tabs[0]:
    st.header("Coach T Command Center")
    st.caption("One dashboard for all six strategies: paper trading, simulation, and strategies still in development.")

    strategy5_summary, strategy5_error = fetch_strategy5_daily_pnl()

    st.subheader("Daily P/L by Strategy")
    status_rows = []
    for name, info in BOTS.items():
        df = bot_data.get(name, pd.DataFrame())
        today_df = get_today_rows(df)
        meta = STRATEGY_META.get(name, {})
        daily_pnl, pnl_source = get_daily_pnl_for_strategy(name, strategy5_summary)
        status_rows.append({
            "Strategy": meta.get("number", name),
            "System": name,
            "Market": meta.get("market", ""),
            "Mode": meta.get("mode", ""),
            "Status": meta.get("status", ""),
            "Daily P/L": format_daily_pnl(daily_pnl),
            "P/L Source": pnl_source,
            "Rows Today": len(today_df),
            "Total Rows": len(df),
            "Last Event": get_last_event(df),
        })

    status_df = pd.DataFrame(status_rows)

    for row_group in [status_rows[:3], status_rows[3:]]:
        cols = st.columns(3)
        for col, row in zip(cols, row_group):
            with col:
                st.markdown(f"**{row['Strategy']} — {row['System']}**")
                st.caption(f"{row['Market']} | {row['Mode']}")
                st.metric("Daily P/L", row["Daily P/L"])
                st.caption(f"P/L source: {row['P/L Source']}")
                if row["Status"] == "Running":
                    st.success(row["Status"])
                elif row["Status"] == "Not Running":
                    st.info(row["Status"])
                else:
                    st.warning(row["Status"])
                st.caption(f"Rows loaded: {row['Total Rows']} | Last event: {row['Last Event']}")

    st.info(
        "Strategy 5 P/L comes from its simulator endpoint. A paper strategy may show Pending until "
        "its closed orders carry strategy tags. Strategies sharing one Alpaca account cannot be split accurately from account history alone."
    )

    st.divider()

    st.subheader("Strategy 5 — Simulation Monitor")
    st.warning(
        "May 22 Strategy 5 results are a disrupted test session. "
        "The monitor could not pull prices earlier in the day, and the after-hours MSFT manual test should not be used for performance scoring."
    )

    if strategy5_summary:
        s5_c1, s5_c2, s5_c3, s5_c4 = st.columns(4)

        realized_pnl = float(strategy5_summary.get("realized_pnl") or 0)
        closed_trades = int(strategy5_summary.get("closed_trades") or 0)
        win_rate = float(strategy5_summary.get("win_rate") or 0)
        open_trades = int(strategy5_summary.get("open_trades") or 0)
        open_symbols = strategy5_summary.get("open_symbols") or []

        s5_c1.metric("S5 Reported Daily P/L", f"${realized_pnl:,.2f}")
        s5_c2.metric("S5 Closed Trades", closed_trades)
        s5_c3.metric("S5 Reported Win Rate", f"{win_rate:.1f}%")
        s5_c4.metric("S5 Open Trades", open_trades)

        if open_symbols:
            st.caption("S5 Open Symbols: " + ", ".join(open_symbols))
    else:
        st.warning(f"Strategy 5 daily P/L unavailable: {strategy5_error}")

    st.divider()
    st.subheader("System Totals")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Strategies Tracked", len(BOTS))
    c2.metric("Closed Orders", len(closed_trades_df))
    c3.metric("Realized P/L Rows", len(realized_df))
    c4.metric("Rejected Signals", len(rejected_df))
    c5.metric("Strategy 5 Rows", len(strategy5_df))

    st.dataframe(status_df, use_container_width=True)

    st.info(
        "Strategy 6 Forex is now included in Coach T as Development / Not Running. "
        "Its panel is ready for TradingView backtest files and future signal testing."
    )

with tabs[1]:
    st.header("Unified Open Positions")
    st.caption("Positions are pulled from Railway Alpaca account variables and de-duplicated by account/symbol.")

    positions_df = load_unique_open_positions()

    if positions_df.empty:
        st.success("No open positions found.")
    else:
        ok_rows = positions_df[positions_df["Status"] == "OK"] if "Status" in positions_df.columns else positions_df
        total_market = pd.to_numeric(ok_rows.get("Market Value", 0), errors="coerce").fillna(0).sum()
        total_unrealized = pd.to_numeric(ok_rows.get("Unrealized P/L", 0), errors="coerce").fillna(0).sum()

        c1, c2, c3 = st.columns(3)
        c1.metric("Position Rows", len(ok_rows))
        c2.metric("Total Market Value", f"${total_market:,.2f}")
        c3.metric("Total Unrealized P/L", f"${total_unrealized:,.2f}")

        st.dataframe(positions_df, use_container_width=True)


with tabs[2]:
    st.header("Closed Trades / Filled Orders")
    st.caption("Synced by sync_closed_trades_v2.py on open and main refresh.")

    if closed_trades_df.empty:
        st.warning("No closed_trades.csv found yet or no closed order rows were returned.")
    else:
        st.metric("Closed Order Rows", len(closed_trades_df))
        st.dataframe(closed_trades_df.head(500), use_container_width=True)


with tabs[3]:
    st.header("Realized P/L Pairing")
    st.info("This pairs filled buys and sells FIFO by symbol. It is an estimate until each bot logs exact parent/child order IDs and strategy IDs on exits.")

    if realized_df.empty:
        st.warning("No paired realized trades yet.")
    else:
        total_pnl = realized_df["realized_pnl"].sum()
        win_rate = get_win_rate_from_pnl(realized_df)

        c1, c2, c3 = st.columns(3)
        c1.metric("Estimated Realized P/L", f"${total_pnl:,.2f}")
        c2.metric("Paired Trades", len(realized_df))
        c3.metric("Win Rate", f"{win_rate}%")

        st.dataframe(realized_df.sort_values("exit_time", ascending=False), use_container_width=True)

        st.download_button(
            label="Download Realized P/L CSV",
            data=realized_df.to_csv(index=False).encode("utf-8"),
            file_name=f"realized_pnl_{datetime.now().strftime('%Y-%m-%d')}.csv",
            mime="text/csv",
            key="download_realized_pnl_tab",
        )


with tabs[4]:
    st.header("Strategy 5 VWAP Reclaim Simulator")
    st.caption("Strategy 5 is a TradingView-driven simulator writing trade activity through Railway/Postgres.")

    if strategy5_df.empty:
        st.warning("No Strategy 5 rows found yet.")
        st.info("For Railway accuracy, Strategy 5 must write to a shared Postgres table such as trade_events, not only to a CSV inside its own service.")
    else:
        today_strategy5 = get_today_rows(strategy5_df)

        c1, c2, c3 = st.columns(3)
        c1.metric("Strategy 5 Total Rows", len(strategy5_df))
        c2.metric("Strategy 5 Rows Today", len(today_strategy5))
        c3.metric("Last Strategy 5 Event", get_last_event(strategy5_df))

        st.dataframe(strategy5_df.head(500), use_container_width=True)

        st.download_button(
            label="Download Strategy 5 CSV",
            data=strategy5_df.to_csv(index=False).encode("utf-8"),
            file_name=f"strategy5_report_{datetime.now().strftime('%Y-%m-%d')}.csv",
            mime="text/csv",
            key="download_strategy5_csv",
        )


with tabs[5]:
    st.header("Trade Replay Viewer")

    if realized_df.empty:
        st.warning("No trades available for replay.")
    else:
        replay_df = realized_df.sort_values("exit_time", ascending=False).reset_index(drop=True)
        options = [
            f"{i}: {r['symbol']} | P/L ${r['realized_pnl']} | {r['grade']} | Exit {r['exit_time']}"
            for i, r in replay_df.iterrows()
        ]

        pick = st.selectbox("Select trade to review", options)
        idx = int(str(pick).split(":")[0])
        trade = replay_df.iloc[idx]

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Symbol", trade["symbol"])
        c2.metric("Grade", trade["grade"])
        c3.metric("P/L", f"${trade['realized_pnl']:,.2f}")
        c4.metric("P/L %", f"{trade['pnl_pct']}%")

        st.write("Trade Details")
        st.dataframe(pd.DataFrame([trade]), use_container_width=True)

        st.write("Review Notes")
        st.info(trade["notes"])


with tabs[6]:
    st.header("Daily AI Recap")
    st.info("This is a rule-based AI-style recap from your logs.")
    st.text_area("Daily Recap", build_v9_recap(realized_df, rejected_df), height=260)


with tabs[7]:
    st.header("AI Recommendations")
    st.warning("These are recommendations only. Do not auto-change bot rules without reviewing the data first.")

    rec_df = build_ai_recommendations(realized_df, rejected_df)
    st.dataframe(rec_df, use_container_width=True)

    high_priority = rec_df[rec_df["Priority"] == "High"] if "Priority" in rec_df.columns else pd.DataFrame()

    if not high_priority.empty:
        st.subheader("High Priority Actions")
        for _, row in high_priority.iterrows():
            st.write(f"**{row['Area']}** — {row['Recommendation']}")


with tabs[8]:
    st.header("Weekly Strategy Review")

    weekly_df = build_weekly_review(realized_df)

    if weekly_df.empty:
        st.warning("Not enough weekly trade data yet.")
    else:
        st.subheader("7-Day Strategy Performance")
        st.dataframe(weekly_df, use_container_width=True)
        st.bar_chart(weekly_df.set_index("Strategy")["Total P/L"])

        best = weekly_df.iloc[0]
        worst = weekly_df.sort_values("Total P/L").iloc[0]

        c1, c2 = st.columns(2)
        c1.metric("Best Weekly Strategy", best["Strategy"], f"${best['Total P/L']:.2f}")
        c2.metric("Worst Weekly Strategy", worst["Strategy"], f"${worst['Total P/L']:.2f}")

        st.info("Use weekly data before making major strategy changes. Daily data alone is often noisy.")


with tabs[9]:
    st.header("AI Decision Center")

    confidence = build_confidence_score(realized_df)

    c1, c2 = st.columns(2)
    c1.metric("System Confidence Score", f"{confidence}/100")

    if confidence >= 75:
        c2.success("System performance currently stable.")
    elif confidence >= 50:
        c2.warning("Mixed results. Continue collecting data.")
    else:
        c2.error("Weak performance. Review strategies carefully.")

    st.subheader("Parameter Suggestions")
    param_df = build_parameter_suggestions(realized_df)

    if param_df.empty:
        st.success("No major parameter warnings yet.")
    else:
        st.dataframe(param_df, use_container_width=True)

    st.subheader("Do-Not-Trade Watchlist")
    blocked_df = build_do_not_trade(realized_df)

    if blocked_df.empty:
        st.success("No symbols currently flagged.")
    else:
        st.dataframe(blocked_df, use_container_width=True)

    st.subheader("Tomorrow Game Plan")
    for item in build_tomorrow_plan(realized_df):
        st.write(f"- {item}")

    st.subheader("AI Strategy Coach")
    st.info("Do not change strategy rules based on one day alone. Focus on repeated weaknesses across multiple trades and time windows.")


with tabs[10]:
    st.header("Strategy Scoring / Grading")
    score_rows = []

    if not realized_df.empty:
        by_symbol = realized_df.groupby("symbol").agg(
            trades=("symbol", "count"),
            total_pnl=("realized_pnl", "sum"),
            avg_pnl=("realized_pnl", "mean"),
            win_rate=("realized_pnl", lambda x: round((x > 0).mean() * 100, 2)),
        ).reset_index()

        by_symbol["score"] = by_symbol.apply(
            lambda r: round(
                (r["win_rate"] * 0.4)
                + (max(min(r["total_pnl"], 100), -100) * 0.3)
                + (r["trades"] * 2),
                2,
            ),
            axis=1,
        )

        by_symbol = by_symbol.sort_values("score", ascending=False)
        st.subheader("Symbol Scoreboard")
        st.dataframe(by_symbol, use_container_width=True)
    else:
        st.warning("No realized trades to score yet.")

    if not realized_df.empty and "quality_score" in realized_df.columns:
        st.subheader("Trade Quality Grades")
        quality_summary = realized_df["quality_grade"].value_counts().reset_index()
        quality_summary.columns = ["Grade", "Count"]
        st.dataframe(quality_summary, use_container_width=True)

    st.subheader("Strategy Activity Scoreboard")

    for name, df in bot_data.items():
        accepted = count_status(df, "ACCEPTED")
        rejected = count_status(df, "REJECTED")
        submitted = count_status(df, "ORDER_SUBMITTED")
        simulated = count_status(df, "SIMULATED")
        total = accepted + rejected + submitted + simulated
        score = (accepted * 2) + (submitted * 3) + simulated - rejected

        score_rows.append({
            "Strategy": name,
            "Accepted": accepted,
            "Rejected": rejected,
            "Orders Submitted": submitted,
            "Simulated": simulated,
            "Activity Score": score,
            "Acceptance %": round((accepted / total) * 100, 2) if total else 0,
        })

    st.dataframe(pd.DataFrame(score_rows).sort_values("Activity Score", ascending=False), use_container_width=True)


with tabs[11]:
    st.header("Symbol Intelligence")

    symbol_df = build_symbol_intelligence(realized_df)

    if symbol_df.empty:
        st.warning("No realized trades available for symbol intelligence yet.")
    else:
        c1, c2 = st.columns(2)
        best = symbol_df.iloc[0]
        worst = symbol_df.sort_values("total_pnl").iloc[0]

        c1.metric("Best Symbol", best["symbol"], f"${best['total_pnl']:.2f}")
        c2.metric("Worst Symbol", worst["symbol"], f"${worst['total_pnl']:.2f}")

        st.subheader("Symbol Scoreboard")
        st.dataframe(symbol_df, use_container_width=True)
        st.bar_chart(symbol_df.set_index("symbol")[["total_pnl", "avg_pnl"]])


with tabs[12]:
    st.header("Time-of-Day Analysis")

    time_df = build_time_of_day_analysis(realized_df)

    if time_df.empty:
        st.warning("No realized trades available for time-of-day analysis yet.")
    else:
        st.dataframe(time_df, use_container_width=True)
        st.bar_chart(time_df.set_index("time_bucket")[["total_pnl", "avg_pnl"]])
        st.info("Use this to decide whether to block or reduce size during weak time windows.")


with tabs[13]:
    st.header("Rejection Intelligence")

    if rejected_df.empty:
        st.success("No rejected orders found.")
    else:
        reason_col = "reason" if "reason" in rejected_df.columns else "message" if "message" in rejected_df.columns else None

        if reason_col:
            temp = rejected_df.copy()
            temp["rejection_class"] = temp[reason_col].apply(classify_rejection)

            st.subheader("Rejection Classes")
            class_summary = temp["rejection_class"].value_counts().reset_index()
            class_summary.columns = ["Rejection Class", "Count"]
            st.dataframe(class_summary, use_container_width=True)

            st.subheader("Detailed Rejections")
            st.dataframe(temp.tail(300), use_container_width=True)
            st.bar_chart(class_summary.set_index("Rejection Class")["Count"])
        else:
            st.warning("Rejected rows exist, but no reason/message column was found.")
            st.dataframe(rejected_df, use_container_width=True)


with tabs[14]:
    st.header("Strategy Heatmap")

    heat = build_strategy_heatmap(realized_df)

    if heat.empty:
        st.warning("No realized trades available for heatmap yet.")
    else:
        st.info("Green/positive days and red/negative days are shown as values by symbol.")
        st.dataframe(heat, use_container_width=True)
        st.line_chart(heat)


with tabs[15]:
    st.header("Rejected Orders Diagnostics")

    if rejected_df.empty:
        st.success("No rejected orders found.")
    else:
        reason_col = "reason" if "reason" in rejected_df.columns else "message" if "message" in rejected_df.columns else None

        if reason_col:
            reason_summary = rejected_df[reason_col].astype(str).value_counts().reset_index()
            reason_summary.columns = ["Reason", "Count"]
            st.subheader("Reason Summary")
            st.dataframe(reason_summary, use_container_width=True)

        st.subheader("Rejected Details")
        st.dataframe(rejected_df.tail(300), use_container_width=True)


with tabs[16]:
    st.header("Railway-Aware Bot Health")
    st.info("This checks live Alpaca API access from Railway variables instead of local Windows folders.")

    health_rows = []

    for name, info in BOTS.items():
        if info.get("type") == "development":
            health_rows.append({
                "Strategy": name,
                "Bot Group": info.get("bot_group", ""),
                "_Account Key": info.get("api_key_var", ""),
                "API Connected": "N/A",
                "Positions Connected": "N/A",
                "Open Positions": 0,
                "Account Status": "Development / Not Running",
                "Equity": 0.0,
                "Cash": 0.0,
                "Buying Power": 0.0,
                "Last Log Event": get_last_event(bot_data.get(name, pd.DataFrame())),
                "Rows Loaded": len(bot_data.get(name, pd.DataFrame())),
                "Bot Type": info.get("type", ""),
            })
            continue

        if info.get("type") == "simulator":
            strategy5_rows = len(strategy5_df)
            health_rows.append({
                "Strategy": name,
                "Bot Group": info.get("bot_group", ""),
                "_Account Key": info.get("api_key_var", ""),
                "API Connected": "N/A",
                "Positions Connected": "N/A",
                "Open Positions": 0,
                "Account Status": "Simulator",
                "Equity": 0.0,
                "Cash": 0.0,
                "Buying Power": 0.0,
                "Last Log Event": get_last_event(strategy5_df),
                "Rows Loaded": strategy5_rows,
                "Bot Type": info.get("type", ""),
            })
            continue

        account, account_err = load_alpaca_account(info)
        positions, positions_err = load_alpaca_positions(info)

        api_ok = account is not None
        positions_ok = positions_err == ""

        if api_ok:
            equity = safe_float(account.equity)
            cash = safe_float(account.cash)
            buying_power = safe_float(account.buying_power)
            account_status = str(account.status)
        else:
            equity = 0.0
            cash = 0.0
            buying_power = 0.0
            account_status = account_err

        health_rows.append({
            "Strategy": name,
            "Bot Group": info.get("bot_group", ""),
            "_Account Key": info.get("api_key_var", ""),
            "API Connected": api_ok,
            "Positions Connected": positions_ok,
            "Open Positions": len(positions),
            "Account Status": account_status,
            "Equity": round(equity, 2),
            "Cash": round(cash, 2),
            "Buying Power": round(buying_power, 2),
            "Last Log Event": get_last_event(bot_data.get(name, pd.DataFrame())),
            "Rows Loaded": len(bot_data.get(name, pd.DataFrame())),
            "Bot Type": info.get("type", ""),
        })

    health_df = pd.DataFrame(health_rows)

    connected_accounts_df = pd.DataFrame()
    if not health_df.empty:
        connected_accounts_df = health_df[health_df["API Connected"] == True].copy()
        connected_accounts_df = connected_accounts_df.drop_duplicates(subset=["_Account Key"])

    unique_accounts_connected = len(connected_accounts_df)
    unique_buying_power = (
        pd.to_numeric(connected_accounts_df["Buying Power"], errors="coerce").fillna(0).sum()
        if not connected_accounts_df.empty else 0.0
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Unique Alpaca Accounts Connected", unique_accounts_connected)
    c2.metric("Total Open Positions", int(pd.to_numeric(health_df["Open Positions"], errors="coerce").fillna(0).sum()) if not health_df.empty else 0)
    c3.metric("Total Unique Buying Power", f"${unique_buying_power:,.2f}")
    c4.metric("Strategy Health Rows", len(health_df))

    st.subheader("Account / API Health")
    display_health_df = health_df.drop(columns=["_Account Key"], errors="ignore")
    st.dataframe(display_health_df, use_container_width=True)

    st.subheader("Health Notes")
    st.write("- API Connected means Railway can authenticate to that Alpaca paper account.")
    st.write("- Positions Connected means Railway can pull open positions from that account.")
    st.write("- Strategy 5 is a simulator loaded from shared Postgres trade_events.")
    st.write("- Strategy 6 Forex is intentionally listed as Development / Not Running until testing is stable.")
    st.write("- Breakout Momentum and Pullback Reclaim are separate strategies sharing one Alpaca account; that account is counted once in totals.")


with tabs[17]:
    st.header("Daily Reports")
    report_date = datetime.now().strftime("%Y-%m-%d")

    summary_rows = []
    for name, df in bot_data.items():
        today_df = get_today_rows(df)
        summary_rows.append({
            "Strategy": name,
            "Rows Today": len(today_df),
            "Total Rows": len(df),
            "Accepted": count_status(df, "ACCEPTED"),
            "Rejected": count_status(df, "REJECTED"),
            "Orders Submitted": count_status(df, "ORDER_SUBMITTED"),
            "Simulated": count_status(df, "SIMULATED"),
            "Last Event": get_last_event(df),
        })

    report_df = pd.DataFrame(summary_rows)

    st.subheader("Daily Strategy Summary")
    st.dataframe(report_df, use_container_width=True)

    st.download_button(
        label="Download Daily Strategy Summary CSV",
        data=report_df.to_csv(index=False).encode("utf-8"),
        file_name=f"daily_strategy_summary_{report_date}.csv",
        mime="text/csv",
    )

    combined_reports = []
    for name, df in bot_data.items():
        if not df.empty:
            temp = df.copy()
            temp["Strategy Name"] = name
            combined_reports.append(temp)

    if combined_reports:
        full_df = pd.concat(combined_reports, ignore_index=True, sort=False)
        full_df = parse_datetime_column(full_df)
        full_df = full_df.sort_values("_dt", ascending=False)

        st.subheader("Full Activity Report")
        st.dataframe(full_df.head(300), use_container_width=True)

        st.download_button(
            label="Download Full Daily Activity CSV",
            data=full_df.to_csv(index=False).encode("utf-8"),
            file_name=f"daily_activity_report_{report_date}.csv",
            mime="text/csv",
            key="download_daily_summary",
        )

    if not realized_df.empty:
        st.download_button(
            label="Download Realized P/L CSV",
            data=realized_df.to_csv(index=False).encode("utf-8"),
            file_name=f"realized_pnl_{report_date}.csv",
            mime="text/csv",
            key="download_full_activity",
        )

    if not strategy5_df.empty:
        st.download_button(
            label="Download Strategy 5 CSV",
            data=strategy5_df.to_csv(index=False).encode("utf-8"),
            file_name=f"strategy5_report_{report_date}.csv",
            mime="text/csv",
            key="download_strategy5_daily_report",
        )


with tabs[18]:
    st.header("Raw Logs")

    selected_bot = st.selectbox("Select strategy", list(BOTS.keys()))
    selected_df = bot_data[selected_bot]

    if selected_df.empty:
        st.warning("No log data found.")
    else:
        st.dataframe(selected_df.tail(300), use_container_width=True)
        st.write("Columns found:")
        st.code(", ".join(selected_df.columns.astype(str)))



with tabs[19]:
    st.header("Strategy 6 — Forex Development")
    st.info("Status: Development / Not Running. No live or simulated trading is enabled for Strategy 6 yet.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Market", "Forex")
    c2.metric("Testing Pair", "EURUSD")
    c3.metric("Current Mode", "Development")
    c4.metric("Live Signals", "Disabled")

    st.subheader("Current Strategy Concept")
    st.write(
        "Top-down market structure review using higher timeframes, "
        "with an engulfing-candle entry concept on the lower timeframe."
    )

    st.subheader("Development Checklist")
    st.checkbox("Confirm final multi-timeframe entry rules", value=False, disabled=True)
    st.checkbox("Produce acceptable TradingView backtest results", value=False, disabled=True)
    st.checkbox("Choose risk and session limits", value=False, disabled=True)
    st.checkbox("Connect alerts to a simulator only", value=False, disabled=True)
    st.checkbox("Approve for paper testing", value=False, disabled=True)

    strategy6_df = bot_data.get("Strategy 6 Forex", pd.DataFrame())
    if strategy6_df.empty:
        st.caption("No Strategy 6 data loaded yet. This is expected while the Forex strategy is under development.")
    else:
        st.subheader("Loaded Strategy 6 Data")
        st.dataframe(strategy6_df.head(500), use_container_width=True)
