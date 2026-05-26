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
CENTRAL_TZ = "America/Chicago"
ANALYSIS_MIN_ATTRIBUTED_TRADES = 10


# Strategy / Account Config
# Railway-safe: no Windows C:\ paths required.


STRATEGY5_DAILY_PNL_URL = os.getenv(
    "STRATEGY5_DAILY_PNL_URL",
    "https://web-production-ab0c0.up.railway.app/daily-pnl",
)

# Strategy 6 runs in a separate Railway project/Postgres database. The dashboard
# reads its public, simulation-only status/results endpoints rather than assuming
# Strategy 6 records exist in this dashboard service's DATABASE_URL.
STRATEGY6_MONITOR_BASE_URL = os.getenv("STRATEGY6_MONITOR_BASE_URL", "").strip().rstrip("/")
STRATEGY3_SERVICE_BASE_URL = os.getenv("STRATEGY3_SERVICE_BASE_URL", "").strip().rstrip("/")
STRATEGY4_SERVICE_BASE_URL = os.getenv("STRATEGY4_SERVICE_BASE_URL", "").strip().rstrip("/")

BOT_EVENT_SERVICE_NAMES = {
    "Breakout Momentum": "Alpaca Direct Bot - Auto Scanner",
    "Pullback Reclaim": "Alpaca Direct Bot - Auto Scanner",
    "HA 100 EMA Doji": "TradingView Bot - QQQ TSLA AMD",
    "Alligator Trend": "Alligator Bot - LIVE",
}

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
        "strategy": "strategy_6b_v2_forex_portfolio_candidate",
        "model": "locked_forward_validation_v1",
        "type": "development",
        "api_key_var": "",
        "secret_key_var": "",
        "paper_var": "",
        "account_name_var": "",
        "log": Path("strategy6_forward_validation_log.csv"),
        "old_log": Path("strategy6_forex_backtests.csv"),
    },
}

STRATEGY_META = {
    "Breakout Momentum": {"number": "Strategy 1", "market": "Stocks", "mode": "Paper", "status": "Testing"},
    "Pullback Reclaim": {"number": "Strategy 2", "market": "Stocks", "mode": "Paper", "status": "Testing"},
    "HA 100 EMA Doji": {"number": "Strategy 3", "market": "Stocks", "mode": "Paper", "status": "Testing"},
    "Alligator Trend": {"number": "Strategy 4", "market": "Stocks", "mode": "Paper", "status": "Testing"},
    "Strategy 5 VWAP Reclaim Simulator": {"number": "Strategy 5", "market": "Stocks", "mode": "Simulation", "status": "Running"},
    "Strategy 6 Forex": {"number": "Strategy 6", "market": "Forex Portfolio", "mode": "Forward Validation", "status": "Status Not Connected"},
}


STRATEGY6_CANDIDATE = {
    "name": "Strategy 6B V2 Candidate — Active Session Portfolio",
    "status": "Candidate / Forward Validation",
    "live_trading": "Disabled",
    "pairs": ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURJPY", "GBPJPY"],
    "session": "12:00–21:00 UTC",
    "confirmation": "15M Engulfing only",
    "direction_rule": "Weekly and Daily structure must agree",
    "entry_zone": "Daily Area of Interest; confirmation must close through AOI midpoint",
    "stop_rule": "Beyond AOI using greater of 3 pips or 0.5 × 15M ATR",
    "target": "2R",
    "estimated_cost": "0.8 pip per trade",
    "validation_goal": 30,
    "preferred_validation_goal": 50,
}

STRATEGY6_KNOWN_HISTORY = {
    "trades": 66,
    "net_pnl": 364.34,
    "net_r": 18.22,
    "win_rate": 43.94,
    "profit_factor": 1.469,
    "max_drawdown": 110.22,
    "period": "2025-01-01 through 2026-04-10",
}

STRATEGY6_FORWARD_COLUMNS = [
    "signal_date_utc",
    "pair",
    "direction",
    "entry_time_utc",
    "entry_price",
    "stop_price",
    "target_price",
    "exit_time_utc",
    "exit_price",
    "result",
    "net_r",
    "pnl_dollars",
    "notes",
]



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
    """Return rows recorded today in Central time, using a real timestamp when available."""
    if df.empty:
        return df

    for col in [
        "timestamp_et",
        "timestamp",
        "filled_at",
        "submitted_at",
        "synced_at",
        "exit_time",
        "entry_time",
        "created_at",
        "updated_at",
        "date_et",
        "date",
    ]:
        if col in df.columns:
            parsed = pd.to_datetime(df[col], errors="coerce", utc=True)
            if parsed.notna().any():
                today_central = pd.Timestamp.now(tz=CENTRAL_TZ).date()
                return df[parsed.dt.tz_convert(CENTRAL_TZ).dt.date == today_central]

    return df.iloc[0:0].copy()


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
    """
    Estimate realized trades using FIFO lots while keeping account/source isolation.
    Buys and sells are never paired across different source environments.
    """
    if closed_df.empty:
        return pd.DataFrame()

    df = closed_df.copy()
    required = {"symbol", "side", "filled_qty", "filled_avg_price", "filled_at"}

    if not required.issubset(set(df.columns)):
        return pd.DataFrame()

    for col in ["source_env", "bot_group", "strategy", "model", "client_order_id"]:
        if col not in df.columns:
            df[col] = ""

    df = df.dropna(subset=["symbol", "side", "filled_qty", "filled_avg_price", "filled_at"])
    df = df[df["filled_qty"] > 0]
    df = df.sort_values("filled_at")

    open_lots = {}
    realized = []

    for _, row in df.iterrows():
        symbol = str(row["symbol"]).upper()
        source_env = str(row.get("source_env", "") or "").strip() or "unattributed_account"
        side = str(row["side"]).lower().replace("orderside.", "")
        qty = float(row["filled_qty"])
        price = float(row["filled_avg_price"])
        filled_at = row["filled_at"]
        lot_key = (source_env, symbol)

        if qty <= 0:
            continue

        if lot_key not in open_lots:
            open_lots[lot_key] = []

        if "buy" in side:
            open_lots[lot_key].append({
                "qty": qty,
                "price": price,
                "time": filled_at,
                "bot_group": row.get("bot_group", ""),
                "strategy": row.get("strategy", ""),
                "model": row.get("model", ""),
                "source_env": source_env,
                "client_order_id": row.get("client_order_id", ""),
            })
            continue

        if "sell" in side:
            remaining = qty

            while remaining > 0 and open_lots[lot_key]:
                lot = open_lots[lot_key][0]
                matched_qty = min(remaining, lot["qty"])
                pnl = (price - lot["price"]) * matched_qty
                pnl_pct = ((price - lot["price"]) / lot["price"]) * 100 if lot["price"] else 0

                if pd.notna(filled_at) and pd.notna(lot["time"]):
                    duration_min = (filled_at - lot["time"]).total_seconds() / 60
                else:
                    duration_min = None

                realized.append({
                    "source_env": source_env,
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
                    open_lots[lot_key].pop(0)

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


def load_bot_events_from_postgres(strategy_name: str) -> pd.DataFrame:
    """Loads runtime/event records written by Strategies 1-4 into shared Postgres."""
    engine, err = get_database_engine()
    service_name = BOT_EVENT_SERVICE_NAMES.get(strategy_name)
    if engine is None or not service_name:
        return pd.DataFrame()

    try:
        query = text("""
            SELECT *
            FROM bot_events
            WHERE bot_name = :bot_name
            ORDER BY id DESC
            LIMIT 1000
        """)
        df = pd.read_sql(query, engine, params={"bot_name": service_name})
        return filter_from_start_date(df)
    except Exception:
        return pd.DataFrame()


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



def load_strategy6_forward_events_from_postgres() -> pd.DataFrame:
    """
    Loads Strategy 6 simulated forward-validation records from the shared Railway Postgres table.
    Each Strategy 6 trade remains one row and is updated when TradingView sends its exit alert.
    """
    engine, err = get_database_engine()
    if engine is None:
        return pd.DataFrame()

    try:
        query = text("""
            SELECT
                id,
                timestamp_et,
                strategy,
                bot_name,
                symbol,
                side,
                entry_price,
                exit_price,
                stop_loss,
                take_profit,
                status,
                reason,
                order_id,
                source,
                simulation_only,
                raw_payload,
                created_at,
                updated_at
            FROM trade_events
            WHERE source = 'strategy_6'
               OR strategy = 'strategy_6b_v2_forex_portfolio_candidate'
            ORDER BY created_at DESC, id DESC
        """)
        df = pd.read_sql(query, engine)
    except Exception:
        return pd.DataFrame()

    if df.empty:
        return df

    for col in ["entry_price", "exit_price", "stop_loss", "take_profit"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    parsed_metrics = []
    for raw in df.get("raw_payload", pd.Series(dtype=str)).fillna(""):
        try:
            payload = json.loads(raw)
            parsed_metrics.append(payload.get("forward_metrics", {}))
        except Exception:
            parsed_metrics.append({})

    df["net_r"] = [safe_float(item.get("net_r"), None) for item in parsed_metrics]
    df["pnl_dollars"] = [safe_float(item.get("pnl_dollars"), None) for item in parsed_metrics]
    df["risk_pips"] = [safe_float(item.get("risk_pips"), None) for item in parsed_metrics]
    df["gross_r"] = [safe_float(item.get("gross_r"), None) for item in parsed_metrics]
    df["cost_pips"] = [safe_float(item.get("cost_pips"), None) for item in parsed_metrics]

    df["direction"] = df["side"]
    df["pair"] = df["symbol"]
    df["entry_time_utc"] = df["created_at"]
    df["exit_time_utc"] = df["updated_at"].where(df["exit_price"].notna(), "")
    df["stop_price"] = df["stop_loss"]
    df["target_price"] = df["take_profit"]
    df["result"] = df["status"]

    return df



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

    df["exit_time"] = df["exit_time"].dt.tz_convert(CENTRAL_TZ)

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


def _fetch_json_endpoint(url, label):
    if not url:
        return None, f"{label} URL not configured"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.loads(response.read().decode("utf-8")), None
    except urllib.error.URLError as exc:
        return None, f"Could not reach {label}: {exc}"
    except Exception as exc:
        return None, f"Could not load {label}: {exc}"


def fetch_webhook_service_health(base_url: str, label: str):
    if not base_url:
        return None, f"Set {label} URL variable to show service health."
    return _fetch_json_endpoint(f"{base_url}/health", f"{label} health endpoint")


def webhook_runtime_state(payload, error, df):
    if error:
        return "Needs Service URL", error
    if not payload or str(payload.get("status", "")).lower() != "online":
        return "Service Status Unknown", "The service did not report online status."
    if df is not None and not df.empty:
        latest = str(df.iloc[0].get("event_type", "")).upper()
        if latest == "ERROR":
            return "Running — Last Event Error", "Service is online; review the last error row."
        return "Running — Waiting for Alert", "Service is online and event reporting is connected."
    return "Running — Waiting for Alert", "Service is online; connect shared DATABASE_URL to display event rows."


def scanner_runtime_state(df):
    if df is None or df.empty:
        return "Needs Database Connection", "Connect alpaca-direct-bot DATABASE_URL to Coach Q Trading Database."
    latest = str(df.iloc[0].get("event_type", "")).upper()
    if latest == "ERROR":
        return "Monitor Error", "Auto Scanner reported an error in its latest database event."
    return "Online — Scanning", "Shared Auto Scanner is online; Strategy 1/2 trade attribution requires tagged orders."


def fetch_strategy6_monitor_status():
    if not STRATEGY6_MONITOR_BASE_URL:
        return None, "Set STRATEGY6_MONITOR_BASE_URL to the public Python OANDA Monitor service URL."
    return _fetch_json_endpoint(
        f"{STRATEGY6_MONITOR_BASE_URL}/monitor/status",
        "Strategy 6 monitor status endpoint",
    )


def normalize_strategy6_forward_df(df):
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    for col in ["entry_price", "exit_price", "stop_loss", "take_profit"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    parsed_metrics = []
    for raw in df.get("raw_payload", pd.Series(dtype=str)).fillna(""):
        try:
            payload = json.loads(raw)
            parsed_metrics.append(payload.get("forward_metrics", {}))
        except Exception:
            parsed_metrics.append({})

    df["net_r"] = [safe_float(item.get("net_r"), None) for item in parsed_metrics]
    df["pnl_dollars"] = [safe_float(item.get("pnl_dollars"), None) for item in parsed_metrics]
    df["risk_pips"] = [safe_float(item.get("risk_pips"), None) for item in parsed_metrics]
    df["gross_r"] = [safe_float(item.get("gross_r"), None) for item in parsed_metrics]
    df["cost_pips"] = [safe_float(item.get("cost_pips"), None) for item in parsed_metrics]
    df["direction"] = df.get("side", "")
    df["pair"] = df.get("symbol", "")
    df["entry_time_utc"] = df.get("created_at", "")
    if "updated_at" in df.columns and "exit_price" in df.columns:
        df["exit_time_utc"] = df["updated_at"].where(df["exit_price"].notna(), "")
    else:
        df["exit_time_utc"] = ""
    df["stop_price"] = df.get("stop_loss", None)
    df["target_price"] = df.get("take_profit", None)
    df["result"] = df.get("status", "")
    return df


def fetch_strategy6_forward_results():
    if not STRATEGY6_MONITOR_BASE_URL:
        return pd.DataFrame(), "Set STRATEGY6_MONITOR_BASE_URL to load Strategy 6 results."
    payload, err = _fetch_json_endpoint(
        f"{STRATEGY6_MONITOR_BASE_URL}/forward-results",
        "Strategy 6 forward results endpoint",
    )
    if err:
        return pd.DataFrame(), err
    trades = payload.get("trades", []) if payload else []
    return normalize_strategy6_forward_df(pd.DataFrame(trades)), None


def strategy6_runtime_state(status_payload, status_error, forward_df):
    if status_error:
        return "Status Not Connected", status_error
    if not status_payload:
        return "Status Unavailable", "No monitor status response received."

    cycle = status_payload.get("last_monitor_cycle") or {}
    thread_alive = bool(status_payload.get("thread_alive"))
    if cycle:
        summary = cycle.get("summary") or {}
        if not cycle.get("ok", False):
            error = summary.get("last_error") or "Latest monitor cycle reported an OANDA/data error."
            return "Monitor Error", error
        open_count = int((status_payload.get("forward_summary") or {}).get("open_trades") or 0)
        if open_count:
            return "Running — Sim Trade Open", "Monitor is active and managing a simulated open trade."
        return "Running — Waiting for Signal", "Monitor is active; latest scan completed without an entry."
    if thread_alive:
        return "Running — Awaiting First Scan", "Service is online and waiting to record its first scan."
    return "Monitor Not Running", "Strategy 6 monitor thread is not active."


def strategy6_cycle_caption(status_payload):
    cycle = (status_payload or {}).get("last_monitor_cycle") or {}
    if not cycle:
        return "Last scan: none recorded yet"
    summary = cycle.get("summary") or {}
    stamp = str(cycle.get("completed_utc") or "unknown")
    pairs = summary.get("pairs_checked", len(cycle.get("results") or []))
    errors = summary.get("pairs_error", sum(1 for item in cycle.get("results", []) if item.get("error")))
    return f"Last scan: {stamp} | Pairs checked: {pairs} | Errors: {errors}"



# Load Data


auto_sync_once_on_open()

strategy6_monitor_status, strategy6_monitor_error = fetch_strategy6_monitor_status()
strategy6_endpoint_forward_df, strategy6_forward_endpoint_error = fetch_strategy6_forward_results()
strategy6_status_label, strategy6_status_detail = strategy6_runtime_state(
    strategy6_monitor_status,
    strategy6_monitor_error,
    strategy6_endpoint_forward_df,
)
STRATEGY_META["Strategy 6 Forex"]["status"] = strategy6_status_label

strategy3_health, strategy3_health_error = fetch_webhook_service_health(STRATEGY3_SERVICE_BASE_URL, "STRATEGY3_SERVICE_BASE_URL")
strategy4_health, strategy4_health_error = fetch_webhook_service_health(STRATEGY4_SERVICE_BASE_URL, "STRATEGY4_SERVICE_BASE_URL")
runtime_details = {"Strategy 6 Forex": strategy6_status_detail}

bot_data = {}
for name, info in BOTS.items():
    if name == "Strategy 6 Forex" and not strategy6_endpoint_forward_df.empty:
        df = strategy6_endpoint_forward_df.copy()
    elif name in BOT_EVENT_SERVICE_NAMES:
        df = load_bot_events_from_postgres(name)
        if df.empty:
            df = load_log(info["log"])
    else:
        df = load_log(info["log"])
    if df.empty and "old_log" in info:
        df = load_log(info["old_log"])
        if info.get("bot_group") == "DIRECT_SCANNER":
            df = normalize_old_scanner_log(df, name)

    if info.get("type") == "simulator" and df.empty:
        df = load_strategy5_events()

    df = filter_from_start_date(df)

    bot_data[name] = df

for scanner_name in ["Breakout Momentum", "Pullback Reclaim"]:
    label, detail = scanner_runtime_state(bot_data.get(scanner_name, pd.DataFrame()))
    STRATEGY_META[scanner_name]["status"] = label
    runtime_details[scanner_name] = detail

label, detail = webhook_runtime_state(strategy3_health, strategy3_health_error, bot_data.get("HA 100 EMA Doji", pd.DataFrame()))
STRATEGY_META["HA 100 EMA Doji"]["status"] = label
runtime_details["HA 100 EMA Doji"] = detail

label, detail = webhook_runtime_state(strategy4_health, strategy4_health_error, bot_data.get("Alligator Trend", pd.DataFrame()))
STRATEGY_META["Alligator Trend"]["status"] = label
runtime_details["Alligator Trend"] = detail

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


def get_daily_pnl_for_strategy(strategy_name, strategy5_summary, strategy6_forward_df=None):
    """Return today's P/L where it can be attributed accurately."""
    info = BOTS.get(strategy_name, {})

    if strategy_name == "Strategy 6 Forex":
        df = strategy6_forward_df if strategy6_forward_df is not None else pd.DataFrame()
        if df.empty or "pnl_dollars" not in df.columns:
            return 0.0, "Simulation / no closed trades today"
        closed = df.copy()
        closed["exit_time_utc"] = pd.to_datetime(closed.get("exit_time_utc"), errors="coerce", utc=True)
        closed = closed.dropna(subset=["exit_time_utc"])
        if closed.empty:
            return 0.0, "Simulation / no closed trades today"
        today_central = pd.Timestamp.now(tz="America/Chicago").date()
        today_closed = closed[closed["exit_time_utc"].dt.tz_convert("America/Chicago").dt.date == today_central]
        if today_closed.empty:
            return 0.0, "Simulation / no closed trades today"
        pnl = pd.to_numeric(today_closed["pnl_dollars"], errors="coerce").fillna(0).sum()
        return float(pnl), "Strategy 6 simulation endpoint"

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
    "Live Activity",
    "Trades & P/L",
    "Strategy 1/2 Scanner",
    "Strategy 5 Simulation",
    "Strategy 6 Forward Validation",
    "Performance Review",
])

strategy5_summary, strategy5_error = fetch_strategy5_daily_pnl()

def trustworthy_status_rows():
    rows = []
    for name, info in BOTS.items():
        df = bot_data.get(name, pd.DataFrame())
        today_df = get_today_rows(df)
        meta = STRATEGY_META.get(name, {})
        daily_pnl, pnl_source = get_daily_pnl_for_strategy(name, strategy5_summary, strategy6_endpoint_forward_df)
        activity_label = "Activity Events"
        if name == "Strategy 5 VWAP Reclaim Simulator":
            activity_label = "Sim Trade Events"
        elif name == "Strategy 6 Forex":
            activity_label = "Sim Trade Rows"
        rows.append({
            "Strategy": meta.get("number", name),
            "System": name,
            "Market": meta.get("market", ""),
            "Mode": meta.get("mode", ""),
            "Status": meta.get("status", ""),
            "Reported Daily P/L": format_daily_pnl(daily_pnl) if daily_pnl is not None else "Not attributable",
            "P/L Source": pnl_source,
            "Today Activity": len(today_df),
            "Total Activity": len(df),
            "Activity Type": activity_label,
            "Last Event": get_last_event(df),
        })
    return rows

status_rows = trustworthy_status_rows()
status_df = pd.DataFrame(status_rows)
scanner_df = bot_data.get("Breakout Momentum", pd.DataFrame())
strategy6_forward_df = strategy6_endpoint_forward_df.copy()
attributed_realized_df = realized_df.copy()
if not attributed_realized_df.empty:
    attribution_mask = pd.Series(False, index=attributed_realized_df.index)
    for col in ["strategy", "model", "bot_group"]:
        if col in attributed_realized_df.columns:
            attribution_mask |= attributed_realized_df[col].fillna("").astype(str).str.strip().ne("")
    attributed_realized_df = attributed_realized_df[attribution_mask].copy()

with tabs[0]:
    st.header("Coach T Command Center")
    st.caption("Operational status first. P/L is shown only when its source can be stated honestly.")

    st.subheader("Six Strategy Status Cards")
    for row_group in [status_rows[:3], status_rows[3:]]:
        cols = st.columns(3)
        for col, row in zip(cols, row_group):
            with col:
                st.markdown(f"**{row['Strategy']} — {row['System']}**")
                st.caption(f"{row['Market']} | {row['Mode']}")
                st.metric("Reported Daily P/L", row["Reported Daily P/L"])
                st.caption(f"Source: {row['P/L Source']}")
                if str(row["Status"]).startswith(("Running", "Online")):
                    st.success(row["Status"])
                elif "Error" in str(row["Status"]) or "Not Running" in str(row["Status"]):
                    st.error(row["Status"])
                else:
                    st.warning(row["Status"])

                if row["System"] in ("Breakout Momentum", "Pullback Reclaim"):
                    st.caption("Shared scanner activity; no independent trade attribution yet.")
                    st.caption(f"Activity events: {row['Total Activity']} | Last event: {row['Last Event']}")
                elif row["System"] == "Strategy 6 Forex":
                    st.caption(strategy6_cycle_caption(strategy6_monitor_status))
                    st.caption(f"Simulated trade rows: {row['Total Activity']} | {strategy6_status_detail}")
                else:
                    st.caption(f"{row['Activity Type']}: {row['Total Activity']} | Last event: {row['Last Event']}")
                    if row["System"] in runtime_details:
                        st.caption(runtime_details[row["System"]])

    st.info(
        "Strategies 1 and 2 share one Auto Scanner feed. Their green status means the scanner is online, "
        "not that each strategy traded. Individual P/L stays unavailable until fills are strategy-tagged. "
        "Strategy 5 reports simulator performance; Strategy 6 reports separate forward-validation simulation results."
    )

    st.subheader("Status Table")
    st.dataframe(status_df, width="stretch", hide_index=True)

    st.subheader("System Totals — Clearly Labeled")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Strategies Tracked", len(BOTS))
    c2.metric("Broker Fill Rows Synced", len(closed_trades_df))
    c3.metric("Estimated Paired Trades", len(realized_df))
    c4.metric("Rejected / Blocked Events", len(rejected_df))
    st.caption("Estimated paired trades are for review only until every entry and exit is strategy-attributed.")

with tabs[1]:
    st.header("Live Activity")
    st.caption("Service health, scanner activity, signals, blocks, rejections, and monitor errors.")

    activity_name = st.selectbox(
        "Select system activity",
        list(BOTS.keys()),
        key="live_activity_system",
    )
    activity_df = bot_data.get(activity_name, pd.DataFrame())

    if activity_name in ("Breakout Momentum", "Pullback Reclaim"):
        st.warning("Strategies 1 and 2 use the same scanner event feed. Rows here are shared scanner activity, not separate trades.")
    elif activity_name == "Strategy 6 Forex":
        st.info(strategy6_cycle_caption(strategy6_monitor_status))

    if activity_df.empty:
        st.info("No activity rows available for this system.")
    else:
        st.metric("Activity Events Loaded", len(activity_df))
        st.dataframe(activity_df.head(300), width="stretch", hide_index=True)

    st.subheader("Errors / Rejected / Blocked Events")
    flagged_parts = []
    for system_name, df in bot_data.items():
        if df.empty:
            continue
        temp = df.copy()
        text_cols = [col for col in ["event_type", "status", "decision", "message", "reason"] if col in temp.columns]
        if not text_cols:
            continue
        joined = temp[text_cols].fillna("").astype(str).agg(" ".join, axis=1).str.upper()
        mask = joined.str.contains("ERROR|REJECT|BLOCK", regex=True)
        if mask.any():
            flagged = temp[mask].copy()
            flagged["System"] = system_name
            flagged_parts.append(flagged)
    flagged_df = pd.concat(flagged_parts, ignore_index=True, sort=False) if flagged_parts else pd.DataFrame()
    if flagged_df.empty:
        st.success("No error, blocked, or rejected activity rows loaded.")
    else:
        st.dataframe(flagged_df.head(300), width="stretch", hide_index=True)

with tabs[2]:
    st.header("Trades & P/L")
    st.warning(
        "Broker fill rows are factual. Realized P/L pairing below is an estimate, now isolated by account/source and symbol. "
        "It should not be used to rank strategies until entries and exits are tagged."
    )

    st.subheader("Open Positions")
    positions_df = load_unique_open_positions()
    if positions_df.empty:
        st.success("No open positions found.")
    else:
        st.dataframe(positions_df, width="stretch", hide_index=True)

    st.subheader("Closed Broker Fill Rows")
    if closed_trades_df.empty:
        st.info("No closed order rows synced.")
    else:
        st.dataframe(closed_trades_df.head(500), width="stretch", hide_index=True)

    st.subheader("Estimated Realized P/L Pairing")
    if realized_df.empty:
        st.info("No estimated paired trades available.")
    else:
        r1, r2, r3 = st.columns(3)
        r1.metric("Estimated Paired Trades", len(realized_df))
        r2.metric("Estimated Realized P/L", f"${realized_df['realized_pnl'].sum():,.2f}")
        r3.metric("Estimated Win Rate", f"{get_win_rate_from_pnl(realized_df):.2f}%")
        st.dataframe(realized_df.sort_values("exit_time", ascending=False), width="stretch", hide_index=True)

with tabs[3]:
    st.header("Strategy 1/2 Auto Scanner")
    st.caption("This is the shared scanner health and decision view for Breakout Momentum and Pullback Reclaim.")

    st.warning(
        "Strategy 1 and Strategy 2 are not independently measurable yet. They share the same scanner event feed; "
        "individual trade/P&L reporting requires strategy-tagged order fills."
    )

    if scanner_df.empty:
        st.error("No shared Auto Scanner activity found. Confirm alpaca-direct-bot DATABASE_URL points to Coach Q Trading Database.")
    else:
        today_scanner = get_today_rows(scanner_df)
        s1, s2, s3 = st.columns(3)
        s1.metric("Scanner Activity Events", len(scanner_df))
        s2.metric("Today's Activity Events", len(today_scanner))
        s3.metric("Last Scanner Event", get_last_event(scanner_df))

        event_col = "event_type" if "event_type" in scanner_df.columns else "status" if "status" in scanner_df.columns else None
        if event_col:
            counts = scanner_df[event_col].fillna("Unknown").astype(str).value_counts().reset_index()
            counts.columns = ["Event Type", "Count"]
            st.subheader("Scanner Event Counts")
            st.dataframe(counts, width="stretch", hide_index=True)

        diagnostic_mask = pd.Series(False, index=scanner_df.index)
        for col in ["event_type", "message", "reason", "status"]:
            if col in scanner_df.columns:
                diagnostic_mask |= scanner_df[col].fillna("").astype(str).str.upper().str.contains(
                    "WATCHLIST|CYCLE_DIAGNOSTICS|SIGNAL|ORDER|BLOCK|REJECT|ERROR", regex=True
                )
        diagnostic_df = scanner_df[diagnostic_mask] if diagnostic_mask.any() else pd.DataFrame()
        st.subheader("Latest Scanner Diagnostics")
        if diagnostic_df.empty:
            st.info("No detailed diagnostic rows recorded yet. The new scanner update will populate this during the next market session.")
        else:
            st.dataframe(diagnostic_df.head(300), width="stretch", hide_index=True)

with tabs[4]:
    st.header("Strategy 5 — VWAP Reclaim Simulation")
    st.caption("Strategy 5 is a separate TradingView-driven simulator. Its P/L is not broker-account P/L.")

    if strategy5_summary:
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Reported Daily P/L", f"${safe_float(strategy5_summary.get('realized_pnl'), 0.0):,.2f}")
        s2.metric("Closed Sim Trades", int(strategy5_summary.get("closed_trades") or 0))
        s3.metric("Reported Win Rate", f"{safe_float(strategy5_summary.get('win_rate'), 0.0):.1f}%")
        s4.metric("Open Sim Trades", int(strategy5_summary.get("open_trades") or 0))
    else:
        st.warning(f"Strategy 5 simulator summary unavailable: {strategy5_error}")

    if strategy5_df.empty:
        st.info("No Strategy 5 activity rows loaded.")
    else:
        st.dataframe(strategy5_df.head(500), width="stretch", hide_index=True)

with tabs[5]:
    st.header("Strategy 6 — Forex Forward Validation")
    if strategy6_status_label.startswith("Running"):
        st.success(f"{strategy6_status_label}. Live trading remains disabled.")
    elif strategy6_status_label == "Monitor Error":
        st.error(f"{strategy6_status_label}: {strategy6_status_detail}")
    else:
        st.warning(f"{strategy6_status_label}: {strategy6_status_detail}")

    cycle = (strategy6_monitor_status or {}).get("last_monitor_cycle") or {}
    summary = cycle.get("summary") or {}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pairs Tracked", len(STRATEGY6_CANDIDATE["pairs"]))
    c2.metric("Last Scan Pairs", summary.get("pairs_checked", "—"))
    c3.metric("Scan Errors", summary.get("pairs_error", "—"))
    c4.metric("Forward Sim Rows", len(strategy6_forward_df))
    st.caption(strategy6_cycle_caption(strategy6_monitor_status))

    st.subheader("Locked Candidate Rules")
    rules_df = pd.DataFrame([
        {"Rule": "Candidate", "Locked Setting": STRATEGY6_CANDIDATE["name"]},
        {"Rule": "Pairs", "Locked Setting": ", ".join(STRATEGY6_CANDIDATE["pairs"])},
        {"Rule": "Trading Session", "Locked Setting": STRATEGY6_CANDIDATE["session"]},
        {"Rule": "Direction", "Locked Setting": STRATEGY6_CANDIDATE["direction_rule"]},
        {"Rule": "Entry / Confirmation", "Locked Setting": f"{STRATEGY6_CANDIDATE['entry_zone']} | {STRATEGY6_CANDIDATE['confirmation']}"},
        {"Rule": "Stop / Target", "Locked Setting": f"{STRATEGY6_CANDIDATE['stop_rule']} | Target {STRATEGY6_CANDIDATE['target']}"},
    ])
    st.dataframe(rules_df, width="stretch", hide_index=True)

    st.subheader("Historical Benchmark — Candidate Selection Only")
    h1, h2, h3, h4, h5 = st.columns(5)
    h1.metric("Historical Trades", STRATEGY6_KNOWN_HISTORY["trades"])
    h2.metric("Historical Net P/L", f"${STRATEGY6_KNOWN_HISTORY['net_pnl']:,.2f}")
    h3.metric("Historical Net R", f"{STRATEGY6_KNOWN_HISTORY['net_r']:.2f} R")
    h4.metric("Historical Win Rate", f"{STRATEGY6_KNOWN_HISTORY['win_rate']:.2f}%")
    h5.metric("Historical Profit Factor", f"{STRATEGY6_KNOWN_HISTORY['profit_factor']:.3f}")
    st.caption("Historical performance is not forward-validation performance.")

    st.subheader("Forward Simulation Results")
    if strategy6_forward_df.empty:
        st.info(
            "No forward simulated trades recorded yet. The Python OANDA monitor is the source of truth; "
            "no manual CSV upload is required."
        )
    else:
        complete = strategy6_forward_df.copy()
        if "pnl_dollars" in complete.columns:
            complete["pnl_dollars"] = pd.to_numeric(complete["pnl_dollars"], errors="coerce").fillna(0)
        if "net_r" in complete.columns:
            complete["net_r"] = pd.to_numeric(complete["net_r"], errors="coerce").fillna(0)
        f1, f2, f3 = st.columns(3)
        f1.metric("Forward Sim Rows", len(complete))
        f2.metric("Forward Net P/L", f"${complete.get('pnl_dollars', pd.Series(dtype=float)).sum():,.2f}")
        f3.metric("Forward Net R", f"{complete.get('net_r', pd.Series(dtype=float)).sum():.2f} R")
        st.dataframe(complete, width="stretch", hide_index=True)

with tabs[6]:
    st.header("Performance Review")
    st.caption("Performance analysis is shown only when the trade data is sufficiently attributed.")

    attributed_count = len(attributed_realized_df)
    st.metric("Attributed Paired Trades Available", attributed_count)

    if attributed_count < ANALYSIS_MIN_ATTRIBUTED_TRADES:
        st.warning(
            f"Not enough accurately attributed closed trades for recommendations. "
            f"Current sample: {attributed_count}; minimum for review: {ANALYSIS_MIN_ATTRIBUTED_TRADES}. "
            "Keep systems in paper/simulation mode and collect tagged trade results."
        )
        st.info(
            "Analysis is intentionally withheld so estimated or shared-account activity does not create misleading recommendations."
        )
    else:
        weekly_df = build_weekly_review(attributed_realized_df)
        symbol_df = build_symbol_intelligence(attributed_realized_df)
        time_df = build_time_of_day_analysis(attributed_realized_df)

        if not weekly_df.empty:
            st.subheader("Weekly Review")
            st.dataframe(weekly_df, width="stretch", hide_index=True)
        if not symbol_df.empty:
            st.subheader("Symbol Results")
            st.dataframe(symbol_df, width="stretch", hide_index=True)
        if not time_df.empty:
            st.subheader("Central-Time Session Analysis")
            st.dataframe(time_df, width="stretch", hide_index=True)
