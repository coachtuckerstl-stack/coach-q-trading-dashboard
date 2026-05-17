import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

try:
    from dotenv import dotenv_values
except Exception:
    dotenv_values = None

try:
    from alpaca.trading.client import TradingClient
except Exception:
    TradingClient = None


st.set_page_config(page_title="Trading Ops Center V9", layout="wide")

BOT1_DIR = Path(r"C:\Users\RodgerTucker\alpaca-bot")
BOT2_DIR = Path(r"C:\Users\RodgerTucker\tradingview-alpaca-bot")
BOT3_DIR = Path(r"C:\Users\RodgerTucker\alligator-alpaca-bot")

BOTS = {
    "Breakout Momentum": {
        "dir": BOT1_DIR,
        "log": BOT1_DIR / "unified_trade_log.csv",
        "old_log": BOT1_DIR / "trade_log.csv",
        "env": BOT1_DIR / ".env",
        "bot_group": "DIRECT_SCANNER",
        "strategy": "breakout_momentum_v1",
        "model": "direct_breakout_live_v1",
        "type": "scanner",
        "api_key_var": "ACCOUNT_1_API_KEY",
        "secret_key_var": "ACCOUNT_1_SECRET_KEY",
        "paper_var": "ACCOUNT_1_PAPER",
    },
    "Pullback Reclaim": {
        "dir": BOT1_DIR,
        "log": BOT1_DIR / "unified_trade_log.csv",
        "old_log": BOT1_DIR / "trade_log.csv",
        "env": BOT1_DIR / ".env",
        "bot_group": "DIRECT_SCANNER",
        "strategy": "pullback_reclaim_v1",
        "model": "direct_pullback_live_v1",
        "type": "scanner",
        "api_key_var": "ACCOUNT_1_API_KEY",
        "secret_key_var": "ACCOUNT_1_SECRET_KEY",
        "paper_var": "ACCOUNT_1_PAPER",
    },
    "HA 100 EMA Doji": {
        "dir": BOT2_DIR,
        "log": BOT2_DIR / "unified_trade_log.csv",
        "old_log": BOT2_DIR / "trade_log.csv",
        "env": BOT2_DIR / ".env",
        "bot_group": "TV_WEBHOOK",
        "strategy": "ha_100ema_doji_v1",
        "model": "tv_ha_100ema_doji_live_v1",
        "type": "webhook",
        "api_key_var": "ACCOUNT_2_API_KEY",
        "secret_key_var": "ACCOUNT_2_SECRET_KEY",
        "paper_var": "ACCOUNT_2_PAPER",
    },
    "Alligator Trend": {
        "dir": BOT3_DIR,
        "log": BOT3_DIR / "unified_trade_log.csv",
        "old_log": BOT3_DIR / "trade_log.csv",
        "env": BOT3_DIR / ".env",
        "bot_group": "ALLIGATOR",
        "strategy": "alligator_trend_v1",
        "model": "alligator_live_v1",
        "type": "webhook",
        "api_key_var": "ACCOUNT_3_API_KEY",
        "secret_key_var": "ACCOUNT_3_SECRET_KEY",
        "paper_var": "ACCOUNT_3_PAPER",
    },
}


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

    for col in ["timestamp_et", "timestamp", "filled_at", "submitted_at", "time", "datetime", "created_at", "date"]:
        if col in df.columns:
            df["_dt"] = pd.to_datetime(df[col], errors="coerce")
            return df

    df["_dt"] = pd.NaT
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
    return int((df["status"].astype(str).str.upper() == status_value).sum())


def estimate_pnl_from_log(df: pd.DataFrame) -> float:
    for col in ["pnl", "pnl_dollars", "profit", "profit_loss", "realized_pnl", "realized_pl", "pl"]:
        if not df.empty and col in df.columns:
            return float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())
    return 0.0


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


def get_alpaca_client(bot_info: dict = None):
    if TradingClient is None:
        return None, "Missing alpaca-py package"

    key = ""
    secret = ""
    paper_text = "true"

    if bot_info:
        key = os.getenv(bot_info.get("api_key_var", ""), "")
        secret = os.getenv(bot_info.get("secret_key_var", ""), "")
        paper_text = os.getenv(bot_info.get("paper_var", ""), "true")

    if (not key or not secret) and dotenv_values is not None and bot_info:
        env_path = bot_info.get("env")
        if env_path is not None and env_path.exists():
            env = dotenv_values(env_path)
            key = env.get("ALPACA_API_KEY", "")
            secret = env.get("ALPACA_SECRET_KEY", "")
            paper_text = str(env.get("ALPACA_PAPER", "true"))

    paper = str(paper_text).lower() != "false"

    if not key or not secret:
        return None, "Missing Alpaca API key/secret"

    try:
        return TradingClient(api_key=key, secret_key=secret, paper=paper), ""
    except Exception as e:
        return None, str(e)


def load_alpaca_account(bot_info: dict = None):
    client, err = get_alpaca_client(bot_info)
    if client is None:
        return None, err

    try:
        return client.get_account(), ""
    except Exception as e:
        return None, str(e)


def load_alpaca_positions(bot_info: dict = None):
    client, err = get_alpaca_client(bot_info)
    if client is None:
        return [], err

    try:
        return client.get_all_positions(), ""
    except Exception as e:
        return [], str(e)


def load_closed_trades() -> pd.DataFrame:
    path = Path("closed_trades.csv")
    if not path.exists():
        return pd.DataFrame()
    df = load_log(path)
    if not df.empty:
        for col in ["qty", "filled_qty", "filled_avg_price"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "filled_at" in df.columns:
            df["filled_at"] = pd.to_datetime(df["filled_at"], errors="coerce")
    return df


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
            open_lots[symbol].append({"qty": qty, "price": price, "time": filled_at})
            continue

        if "sell" in side:
            remaining = qty

            while remaining > 0 and open_lots[symbol]:
                lot = open_lots[symbol][0]
                matched_qty = min(remaining, lot["qty"])
                pnl = (price - lot["price"]) * matched_qty
                pnl_pct = ((price - lot["price"]) / lot["price"]) * 100 if lot["price"] else 0
                duration_min = (filled_at - lot["time"]).total_seconds() / 60 if pd.notna(filled_at) and pd.notna(lot["time"]) else None

                realized.append({
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
                })

                lot["qty"] -= matched_qty
                remaining -= matched_qty

                if lot["qty"] <= 0.000001:
                    open_lots[symbol].pop(0)

    return pd.DataFrame(realized)


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
        temp["exit_time"] = pd.to_datetime(temp["exit_time"], errors="coerce")
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


def classify_rejection(reason: str) -> str:
    text = str(reason).lower()

    if "market closed" in text or "after 3:30" in text or "time" in text or "outside" in text:
        return "MARKET_TIME_BLOCK"
    if "buying power" in text or "insufficient" in text or "not enough" in text:
        return "BUYING_POWER_OR_QTY"
    if "stop" in text and ("too close" in text or "base_price" in text or "invalid" in text):
        return "STOP_PRICE_ISSUE"
    if "take_profit" in text or "take profit" in text or "limit_price" in text:
        return "TAKE_PROFIT_ISSUE"
    if "json" in text or "payload" in text or "secret" in text:
        return "WEBHOOK_PAYLOAD_ISSUE"
    if "position" in text or "open order" in text or "duplicate" in text:
        return "DUPLICATE_OR_OPEN_POSITION"
    if "unauthorized" in text or "401" in text:
        return "API_KEY_AUTH_ISSUE"

    return "OTHER_REJECTION"


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
    df["exit_time"] = pd.to_datetime(df["exit_time"], errors="coerce")
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
    df["exit_time"] = pd.to_datetime(df["exit_time"], errors="coerce")
    df = df.dropna(subset=["exit_time"])
    if df.empty:
        return pd.DataFrame()

    df["date"] = df["exit_time"].dt.strftime("%Y-%m-%d")
    df["realized_pnl"] = pd.to_numeric(df["realized_pnl"], errors="coerce").fillna(0)

    heat = df.pivot_table(
        index="date",
        columns="symbol",
        values="realized_pnl",
        aggfunc="sum",
        fill_value=0,
    )

    return heat


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



def run_closed_trade_sync():
    script = Path("sync_closed_trades.py")
    if not script.exists():
        return False, "sync_closed_trades.py not found."
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        return result.returncode == 0, output.strip()
    except Exception as e:
        return False, str(e)


# Load data
bot_data = {}
for name, info in BOTS.items():
    df = load_log(info["log"])
    if df.empty and "old_log" in info:
        df = load_log(info["old_log"])
        if info.get("bot_group") == "DIRECT_SCANNER":
            df = normalize_old_scanner_log(df, name)
    bot_data[name] = df

st.sidebar.title("Ops Controls")
auto_sync = st.sidebar.checkbox("Auto sync closed trades on refresh", value=False)
if st.sidebar.button("Sync Closed Trades Now"):
    ok, msg = run_closed_trade_sync()
    if ok:
        st.sidebar.success("Closed trades synced.")
    else:
        st.sidebar.error("Sync failed.")
    st.sidebar.code(msg)

if auto_sync:
    ok, msg = run_closed_trade_sync()
    if not ok:
        st.sidebar.warning("Auto sync failed.")
        st.sidebar.code(msg)

closed_trades_df = load_closed_trades()
realized_df = add_quality_scores(pair_realized_trades(closed_trades_df))

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

st.title("Trading Operations Center V9")
st.caption(f"Last refreshed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if st.button("Refresh Dashboard"):
    st.rerun()

tabs = st.tabs([
    "Command Center",
    "Positions",
    "Closed Trades",
    "Realized P/L",
    "Trade Replay",
    "Daily AI Recap",
    "Strategy Scoring",
    "Symbol Intelligence",
    "Time of Day",
    "Rejection Intelligence",
    "Strategy Heatmap",
    "Rejected Orders",
    "Bot Health",
    "Daily Reports",
    "Raw Logs",
])

with tabs[0]:
    st.header("Command Center")

    rows = []
    for name, df in bot_data.items():
        today_df = get_today_rows(df)
        rows.append({
            "Strategy": name,
            "Rows Today": len(today_df),
            "Total Rows": len(df),
            "Accepted": count_status(df, "ACCEPTED"),
            "Rejected": count_status(df, "REJECTED"),
            "Orders Submitted": count_status(df, "ORDER_SUBMITTED"),
            "Last Event": get_last_event(df),
        })
    summary_df = pd.DataFrame(rows)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Strategies", len(BOTS))
    c2.metric("Closed Orders", len(closed_trades_df))
    c3.metric("Realized P/L Rows", len(realized_df))
    c4.metric("Rejected Signals", len(rejected_df))

    st.dataframe(summary_df, use_container_width=True)

with tabs[1]:
    st.header("Unified Open Positions")
    position_rows = []
    for name, info in BOTS.items():
        positions, err = load_alpaca_positions(info)
        if err:
            st.warning(f"{name}: {err}")
            continue
        for p in positions:
            position_rows.append({
                "Strategy Source": name,
                "Symbol": p.symbol,
                "Qty": p.qty,
                "Market Value": p.market_value,
                "Avg Entry": p.avg_entry_price,
                "Unrealized P/L": p.unrealized_pl,
                "Unrealized %": p.unrealized_plpc,
            })
    if position_rows:
        st.dataframe(pd.DataFrame(position_rows), use_container_width=True)
    else:
        st.success("No open positions found.")

with tabs[2]:
    st.header("Closed Trades / Filled Orders")
    if closed_trades_df.empty:
        st.warning("No closed_trades.csv found yet. Run sync_closed_trades.py first.")
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

with tabs[5]:
    st.header("Daily AI Recap")
    st.info("This is a rule-based AI-style recap from your logs. Upload the CSV here for a deeper manual analysis.")
    recap = build_v9_recap(realized_df, rejected_df)
    st.text_area("Daily Recap", recap, height=260)

with tabs[6]:
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
            lambda r: round((r["win_rate"] * 0.4) + (max(min(r["total_pnl"], 100), -100) * 0.3) + (r["trades"] * 2), 2),
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
        total = accepted + rejected + submitted
        score = (accepted * 2) + (submitted * 3) - rejected
        score_rows.append({
            "Strategy": name,
            "Accepted": accepted,
            "Rejected": rejected,
            "Orders Submitted": submitted,
            "Activity Score": score,
            "Acceptance %": round((accepted / total) * 100, 2) if total else 0,
        })
    st.dataframe(pd.DataFrame(score_rows).sort_values("Activity Score", ascending=False), use_container_width=True)


with tabs[7]:
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


with tabs[8]:
    st.header("Time-of-Day Analysis")

    time_df = build_time_of_day_analysis(realized_df)

    if time_df.empty:
        st.warning("No realized trades available for time-of-day analysis yet.")
    else:
        st.dataframe(time_df, use_container_width=True)
        st.bar_chart(time_df.set_index("time_bucket")[["total_pnl", "avg_pnl"]])

        st.info(
            "Use this to decide whether to block or reduce size during weak time windows."
        )


with tabs[9]:
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


with tabs[10]:
    st.header("Strategy Heatmap")

    heat = build_strategy_heatmap(realized_df)

    if heat.empty:
        st.warning("No realized trades available for heatmap yet.")
    else:
        st.info("Green/positive days and red/negative days are shown as values by symbol.")
        st.dataframe(heat, use_container_width=True)
        st.line_chart(heat)


with tabs[11]:
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

with tabs[12]:
    st.header("Railway-Aware Bot Health")

    st.info(
        "This checks live Alpaca API access from Railway variables instead of local Windows folders."
    )

    health_rows = []

    for name, info in BOTS.items():
        account, account_err = load_alpaca_account(info)
        positions, positions_err = load_alpaca_positions(info)

        api_ok = account is not None
        positions_ok = positions_err == ""

        last_event = get_last_event(bot_data.get(name, pd.DataFrame()))

        if api_ok:
            equity = float(account.equity)
            cash = float(account.cash)
            buying_power = float(account.buying_power)
            account_status = str(account.status)
        else:
            equity = 0.0
            cash = 0.0
            buying_power = 0.0
            account_status = account_err

        health_rows.append(
            {
                "Strategy": name,
                "Bot Group": info.get("bot_group", ""),
                "API Connected": api_ok,
                "Positions Connected": positions_ok,
                "Open Positions": len(positions),
                "Account Status": account_status,
                "Equity": round(equity, 2),
                "Cash": round(cash, 2),
                "Buying Power": round(buying_power, 2),
                "Last Log Event": last_event,
                "Bot Type": info.get("type", ""),
            }
        )

    health_df = pd.DataFrame(health_rows)

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "Accounts Connected",
        int(health_df["API Connected"].sum()) if not health_df.empty else 0,
    )

    c2.metric(
        "Total Open Positions",
        int(health_df["Open Positions"].sum()) if not health_df.empty else 0,
    )

    c3.metric(
        "Total Buying Power",
        f"${health_df['Buying Power'].sum():,.2f}" if not health_df.empty else "$0.00",
    )

    c4.metric(
        "Health Rows",
        len(health_df),
    )

    st.subheader("Account / API Health")
    st.dataframe(health_df, width="stretch")

    st.subheader("Health Notes")
    st.write(
        "- API Connected means Railway can authenticate to that Alpaca paper account."
    )
    st.write(
        "- Positions Connected means Railway can pull open positions from that account."
    )
    st.write(
        "- Last Log Event still depends on available CSV/log data."
    )

with tabs[13]:
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

with tabs[14]:
    st.header("Raw Logs")
    selected_bot = st.selectbox("Select strategy", list(BOTS.keys()))
    selected_df = bot_data[selected_bot]
    if selected_df.empty:
        st.warning("No log data found.")
    else:
        st.dataframe(selected_df.tail(300), use_container_width=True)
        st.write("Columns found:")
        st.code(", ".join(selected_df.columns.astype(str)))
