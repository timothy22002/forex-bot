"""
=============================================================
  FOREX SIGNAL BOT — Stochastic & ADX Alert via Telegram
  Pairs : XAUUSD | GBPJPY | EURUSD
  Deploy: Railway.app

  Requirements: pip install requests pandas
=============================================================
"""

import os
import time
import logging
import requests
import pandas as pd
from datetime import datetime

# ─────────────────────────────────────────────────────────
#  KONFIGURASI — dibaca dari Environment Variables Railway
#  (tidak perlu hardcode di sini, aman & tidak bocor)
# ─────────────────────────────────────────────────────────
TWELVE_DATA_API_KEY = "f01acf7646b54cfa855db26609d68c75"    # dari twelvedata.com
TELEGRAM_BOT_TOKEN  = "8705713017:AAGiF3WcOL_h000Cr-vAnvjR4au_KYBB6cY"        # dari @BotFather
TELEGRAM_CHAT_ID    = "6574758309"    
# ─────────────────────────────────────────────────────────

PAIRS        = ["XAU/USD", "GBP/JPY", "EUR/USD"]
TIMEFRAME    = "1h"
INTERVAL     = 300      # cek setiap 5 menit

STOCH_OS     = 20
STOCH_OB     = 80
ADX_TREND    = 25

STOCH_K      = 14
STOCH_D      = 3
STOCH_SMOOTH = 3
ADX_PERIOD   = 14
OUTPUTSIZE   = 120

SUMMARY_INTERVAL_MINUTES = 60

# ─────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

last_alerts: dict  = {p: {} for p in PAIRS}
last_data:   dict  = {}
last_summary: float = 0.0


# ── Validasi config saat startup ─────────────────────────
def validate_config():
    missing = []
    if not TWELVE_DATA_API_KEY: missing.append("TWELVE_DATA_API_KEY")
    if not TELEGRAM_BOT_TOKEN:  missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID:    missing.append("TELEGRAM_CHAT_ID")
    if missing:
        log.error(f"Environment variable belum diset: {', '.join(missing)}")
        log.error("Tambahkan di Railway → project → Variables")
        raise SystemExit(1)


# ── Telegram ─────────────────────────────────────────────
def send_telegram(message: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "HTML",
        }, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Telegram error: {e}")
        return False


# ── Ambil data OHLCV ─────────────────────────────────────
def fetch_ohlcv(pair: str) -> pd.DataFrame | None:
    try:
        r = requests.get("https://api.twelvedata.com/time_series", params={
            "symbol":     pair,
            "interval":   TIMEFRAME,
            "outputsize": OUTPUTSIZE,
            "apikey":     TWELVE_DATA_API_KEY,
        }, timeout=15)
        data = r.json()
        if data.get("status") == "error":
            log.error(f"[{pair}] API: {data.get('message')}")
            return None
        values = data.get("values", [])
        if not values:
            log.warning(f"[{pair}] Data kosong.")
            return None
        df = pd.DataFrame(values)
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.sort_values("datetime").reset_index(drop=True)
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
        return df
    except Exception as e:
        log.error(f"[{pair}] Fetch error: {e}")
        return None


# ── Hitung Stochastic ─────────────────────────────────────
def calc_stochastic(df: pd.DataFrame, k=14, smooth_k=3, d=3):
    highs  = df["high"].values
    lows   = df["low"].values
    closes = df["close"].values

    raw_k = []
    for i in range(k - 1, len(closes)):
        hh = max(highs[i - k + 1: i + 1])
        ll = min(lows[i  - k + 1: i + 1])
        raw_k.append(50.0 if hh == ll else (closes[i] - ll) / (hh - ll) * 100)

    smooth = []
    for i in range(smooth_k - 1, len(raw_k)):
        smooth.append(sum(raw_k[i - smooth_k + 1: i + 1]) / smooth_k)

    d_line = []
    for i in range(d - 1, len(smooth)):
        d_line.append(sum(smooth[i - d + 1: i + 1]) / d)

    return round(smooth[-1], 2), round(d_line[-1], 2)


# ── Hitung ADX ────────────────────────────────────────────
def calc_adx(df: pd.DataFrame, period=14) -> float:
    highs  = df["high"].values
    lows   = df["low"].values
    closes = df["close"].values

    tr_list, pdm_list, ndm_list = [], [], []
    for i in range(1, len(closes)):
        tr   = max(highs[i] - lows[i],
                   abs(highs[i] - closes[i-1]),
                   abs(lows[i]  - closes[i-1]))
        up   = highs[i] - highs[i-1]
        down = lows[i-1] - lows[i]
        tr_list.append(tr)
        pdm_list.append(up   if up > down and up > 0   else 0.0)
        ndm_list.append(down if down > up and down > 0 else 0.0)

    def wilder(arr, p):
        result = [0.0] * len(arr)
        result[p-1] = sum(arr[:p]) / p
        for i in range(p, len(arr)):
            result[i] = (result[i-1] * (p-1) + arr[i]) / p
        return result

    atr  = wilder(tr_list,  period)
    wpdm = wilder(pdm_list, period)
    wndm = wilder(ndm_list, period)

    dx_list = []
    for i in range(period - 1, len(atr)):
        if atr[i] == 0:
            dx_list.append(0.0)
            continue
        pdi = wpdm[i] / atr[i] * 100
        ndi = wndm[i] / atr[i] * 100
        dx_list.append(abs(pdi - ndi) / (pdi + ndi) * 100 if (pdi + ndi) else 0.0)

    adx_arr = wilder(dx_list, period)
    return round(adx_arr[-1], 2)


# ── Cek kondisi dan kirim alert ───────────────────────────
def check_and_alert(pair: str, price: float, k: float, d: float, adx: float):
    sym = pair.replace("/", "")
    ts  = datetime.now().strftime("%H:%M")

    is_buy  = adx > ADX_TREND and k < STOCH_OS and d < STOCH_OS
    is_sell = adx > ADX_TREND and k > STOCH_OB and d > STOCH_OB

    if is_buy:
        msg = (
            f"🟢 <b>SINYAL BUY — {sym}</b>\n"
            f"Harga: <code>{price}</code>  |  TF: {TIMEFRAME.upper()}  |  {ts}\n\n"
            f"✅ ADX = <b>{adx}</b>  (trending kuat, &gt;{ADX_TREND})\n"
            f"✅ Stoch %K = <b>{k}</b>  (oversold, &lt;{STOCH_OS})\n"
            f"✅ Stoch %D = <b>{d}</b>  (oversold, &lt;{STOCH_OS})\n\n"
            f"<i>Semua kriteria terpenuhi — potensi reversal naik</i>"
        )
        if send_telegram(msg):
            log.info(f"[{pair}] ✅ BUY alert terkirim  ADX={adx} %K={k} %D={d}")
    elif is_sell:
        msg = (
            f"🔴 <b>SINYAL SELL — {sym}</b>\n"
            f"Harga: <code>{price}</code>  |  TF: {TIMEFRAME.upper()}  |  {ts}\n\n"
            f"✅ ADX = <b>{adx}</b>  (trending kuat, &gt;{ADX_TREND})\n"
            f"✅ Stoch %K = <b>{k}</b>  (overbought, &gt;{STOCH_OB})\n"
            f"✅ Stoch %D = <b>{d}</b>  (overbought, &gt;{STOCH_OB})\n\n"
            f"<i>Semua kriteria terpenuhi — potensi reversal turun</i>"
        )
        if send_telegram(msg):
            log.info(f"[{pair}] ✅ SELL alert terkirim  ADX={adx} %K={k} %D={d}")
    else:
        log.info(f"[{pair}] Kriteria belum terpenuhi — tidak ada alert")


# ── Ringkasan 1 jam ───────────────────────────────────────
def send_summary():
    global last_summary
    if time.time() - last_summary < SUMMARY_INTERVAL_MINUTES * 60:
        return
    last_summary = time.time()

    if not last_data:
        return

    lines = [
        f"📊 <b>Ringkasan {SUMMARY_INTERVAL_MINUTES} Menit</b>  "
        f"|  {datetime.now().strftime('%H:%M')}  |  TF: {TIMEFRAME.upper()}\n"
    ]
    for pair in PAIRS:
        if pair not in last_data:
            lines.append(f"<b>{pair.replace('/','')}</b>  —  data belum tersedia")
            continue
        info  = last_data[pair]
        k     = info["k"]
        dv    = info["d"]
        adx   = info["adx"]
        price = info["price"]
        sym   = pair.replace("/", "")

        if adx > ADX_TREND and k < STOCH_OS and dv < STOCH_OS:
            cond = "🟢 BUY aktif"
        elif adx > ADX_TREND and k > STOCH_OB and dv > STOCH_OB:
            cond = "🔴 SELL aktif"
        elif adx > ADX_TREND and k < STOCH_OS:
            cond = "⚠️ Hampir BUY (%D belum &lt;20)"
        elif adx > ADX_TREND and k > STOCH_OB:
            cond = "⚠️ Hampir SELL (%D belum &gt;80)"
        elif adx > ADX_TREND:
            cond = "📈 ADX kuat, Stoch netral"
        else:
            cond = "⚪ Belum ada sinyal"

        lines.append(
            f"<b>{sym}</b>  <code>{price}</code>\n"
            f"%K: <code>{k}</code>  |  %D: <code>{dv}</code>  |  ADX: <code>{adx}</code>\n"
            f"{cond}"
        )

    send_telegram("\n\n".join(lines))
    log.info("Ringkasan 1 jam terkirim.")


# ── Log status terminal ───────────────────────────────────
def log_status(pair, price, k, d, adx):
    if adx > ADX_TREND and k < STOCH_OS and d < STOCH_OS:
        label = "✅ SINYAL BUY"
    elif adx > ADX_TREND and k > STOCH_OB and d > STOCH_OB:
        label = "✅ SINYAL SELL"
    elif adx > ADX_TREND and (k < STOCH_OS or k > STOCH_OB):
        label = "⚠️  1 kriteria terpenuhi"
    else:
        label = "— belum ada sinyal"
    log.info(f"{pair:8s} | Price={price:<10.5g} | %K={k:5.1f} | %D={d:5.1f} | ADX={adx:5.1f} | {label}")


# ── Main loop ─────────────────────────────────────────────
def run():
    global last_summary
    validate_config()
    last_summary = time.time()

    log.info("=" * 55)
    log.info("  FOREX SIGNAL BOT — RAILWAY DEPLOY")
    log.info(f"  Pairs      : {', '.join(PAIRS)}")
    log.info(f"  Timeframe  : {TIMEFRAME.upper()}")
    log.info(f"  Interval   : setiap {INTERVAL}s ({INTERVAL//60} menit)")
    log.info(f"  Ringkasan  : setiap {SUMMARY_INTERVAL_MINUTES} menit")
    log.info(f"  BUY  : ADX>{ADX_TREND} + %K<{STOCH_OS} + %D<{STOCH_OS}")
    log.info(f"  SELL : ADX>{ADX_TREND} + %K>{STOCH_OB} + %D>{STOCH_OB}")
    log.info("=" * 55)

    send_telegram(
        f"🤖 <b>Forex Alert Bot AKTIF</b>  (Railway)\n"
        f"Pairs: XAU/USD · GBP/JPY · EUR/USD\n"
        f"TF: <b>{TIMEFRAME.upper()}</b>  |  Cek setiap {INTERVAL//60} menit\n\n"
        f"<b>Kriteria alert:</b>\n"
        f"🟢 <b>BUY</b>  → ADX &gt;{ADX_TREND}  +  %K &lt;{STOCH_OS}  +  %D &lt;{STOCH_OS}\n"
        f"🔴 <b>SELL</b> → ADX &gt;{ADX_TREND}  +  %K &gt;{STOCH_OB}  +  %D &gt;{STOCH_OB}\n\n"
        f"Ringkasan dikirim setiap <b>{SUMMARY_INTERVAL_MINUTES} menit</b>."
    )

    while True:
        log.info(f"── Siklus {datetime.now().strftime('%H:%M:%S')} ──")
        for pair in PAIRS:
            df = fetch_ohlcv(pair)
            if df is None:
                continue
            try:
                k, d  = calc_stochastic(df, STOCH_K, STOCH_SMOOTH, STOCH_D)
                adx   = calc_adx(df, ADX_PERIOD)
                price = df["close"].iloc[-1]
                last_data[pair] = {"price": price, "k": k, "d": d, "adx": adx}
                log_status(pair, price, k, d, adx)
                check_and_alert(pair, price, k, d, adx)
            except Exception as e:
                log.error(f"[{pair}] Error: {e}")
            time.sleep(2)

        send_summary()
        log.info(f"Menunggu {INTERVAL}s...\n")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    run()
