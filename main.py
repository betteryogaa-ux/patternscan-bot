import requests
import time
import math
from datetime import datetime, timezone

# ── CONFIG ────────────────────────────────────────────────────────────────────
TG_TOKEN = "8037434518:AAEA7PlFI9mzukcjRKdVUowEivsyMob7rBY"
TG_CHAT  = "5348543615"
TD_KEY   = "abd8afda1f8a43e69dea53773388524e"

PAIRS = ["XAU/USD", "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CAD", "GBP/JPY"]
TF    = "15min"
SCORE_THRESHOLD = 80
CHECK_INTERVAL  = 900  # 15 minutes

# ── TELEGRAM ──────────────────────────────────────────────────────────────────
def tg_send(text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        print(f"TG error: {e}")

# ── MARKET HOURS ──────────────────────────────────────────────────────────────
def is_market_open():
    now = datetime.now(timezone.utc)
    day = now.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun
    hour = now.hour
    if day == 5:  # Saturday
        return False
    if day == 6 and hour < 22:  # Sunday before 22:00
        return False
    if day == 4 and hour >= 22:  # Friday after 22:00
        return False
    return True

# ── FETCH CANDLES ─────────────────────────────────────────────────────────────
def fetch_candles(pair, tf="15min", count=100):
    try:
        url = f"https://api.twelvedata.com/time_series"
        params = {
            "symbol": pair,
            "interval": tf,
            "outputsize": count,
            "apikey": TD_KEY
        }
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if data.get("status") == "error":
            print(f"TD error for {pair}: {data.get('message')}")
            return []
        values = data.get("values", [])
        candles = []
        for v in reversed(values):
            candles.append({
                "o": float(v["open"]),
                "h": float(v["high"]),
                "l": float(v["low"]),
                "c": float(v["close"])
            })
        return candles
    except Exception as e:
        print(f"Fetch error for {pair}: {e}")
        return []

# ── INDICATORS ────────────────────────────────────────────────────────────────
def calc_rsi(candles, period=14):
    closes = [c["c"] for c in candles]
    if len(closes) < period + 1:
        return 50
    gains = losses = 0
    for i in range(1, period + 1):
        d = closes[i] - closes[i-1]
        if d >= 0: gains += d
        else: losses -= d
    ag, al = gains/period, losses/period
    rsi_vals = [50]
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i-1]
        ag = ((ag * (period-1)) + (d if d > 0 else 0)) / period
        al = ((al * (period-1)) + (-d if d < 0 else 0)) / period
        rsi_vals.append(100 if al == 0 else 100 - (100 / (1 + ag/al)))
    return rsi_vals[-1]

def calc_macd(candles):
    closes = [c["c"] for c in candles]
    if len(closes) < 26:
        return 0
    def ema(data, p):
        out = [data[0]]
        k = 2 / (p + 1)
        for i in range(1, len(data)):
            out.append(data[i] * k + out[-1] * (1 - k))
        return out
    ef = ema(closes, 12)
    es = ema(closes, 26)
    macd = [ef[i] - es[i] for i in range(len(closes))]
    sl = ema(macd, 9)
    hist = [macd[i] - sl[i] for i in range(len(macd))]
    return hist[-1] - hist[-2] if len(hist) >= 2 else 0

def calc_adx(candles, period=14):
    if len(candles) < period + 2:
        return 0
    tr_list, pdm_list, ndm_list = [], [], []
    for i in range(1, len(candles)):
        hl = candles[i]["h"] - candles[i]["l"]
        hc = abs(candles[i]["h"] - candles[i-1]["c"])
        lc = abs(candles[i]["l"] - candles[i-1]["c"])
        tr_list.append(max(hl, hc, lc))
        up = candles[i]["h"] - candles[i-1]["h"]
        dn = candles[i-1]["l"] - candles[i]["l"]
        pdm_list.append(up if up > dn and up > 0 else 0)
        ndm_list.append(dn if dn > up and dn > 0 else 0)
    def smooth(arr, p):
        s = sum(arr[:p])
        out = [s]
        for i in range(p, len(arr)):
            s = s - s/p + arr[i]
            out.append(s)
        return out
    atr = smooth(tr_list, period)
    pdm = smooth(pdm_list, period)
    ndm = smooth(ndm_list, period)
    pdi = [(pdm[i]/atr[i])*100 if atr[i] > 0 else 0 for i in range(len(atr))]
    ndi = [(ndm[i]/atr[i])*100 if atr[i] > 0 else 0 for i in range(len(atr))]
    dx  = [abs(pdi[i]-ndi[i])/(pdi[i]+ndi[i])*100 if (pdi[i]+ndi[i]) > 0 else 0 for i in range(len(pdi))]
    adx = smooth(dx, period)
    return adx[-1]

def calc_atr(candles, period=14):
    if len(candles) < period + 1:
        return 0
    tr_list = []
    for i in range(1, len(candles)):
        hl = candles[i]["h"] - candles[i]["l"]
        hc = abs(candles[i]["h"] - candles[i-1]["c"])
        lc = abs(candles[i]["l"] - candles[i-1]["c"])
        tr_list.append(max(hl, hc, lc))
    return sum(tr_list[-period:]) / period

def get_session():
    h = datetime.now(timezone.utc).hour
    if 7 <= h < 15: return "LONDON"
    if 12 <= h < 20: return "NEW_YORK"
    if h >= 22 or h < 7: return "ASIA"
    return "OFF_HOURS"

def is_good_session(pair):
    sess = get_session()
    sess_pairs = {
        "LONDON":   ["EUR/USD","GBP/USD","EUR/GBP","XAU/USD"],
        "NEW_YORK": ["USD/JPY","USD/CAD","GBP/JPY","EUR/USD","GBP/USD","XAU/USD"],
        "ASIA":     ["AUD/USD","USD/JPY","GBP/JPY"]
    }
    return pair in sess_pairs.get(sess, [])

# ── SCORING ───────────────────────────────────────────────────────────────────
def calc_score(candles, pair):
    if len(candles) < 30:
        return 0, {}

    lp = candles[-1]["c"]
    rsi = calc_rsi(candles)
    macd_mom = calc_macd(candles)
    adx = calc_adx(candles)
    atr = calc_atr(candles)
    sess = get_session()
    good_sess = is_good_session(pair)

    # EMA trend
    closes = [c["c"] for c in candles]
    def ema(data, p):
        out = [data[0]]; k = 2/(p+1)
        for i in range(1, len(data)): out.append(data[i]*k+out[-1]*(1-k))
        return out
    e20 = ema(closes, 20)[-1]
    e50 = ema(closes, min(50, len(closes)-1))[-1]
    trend = 1.0 if lp > e20 > e50 else -1.0 if lp < e20 < e50 else 0.3

    # Premium/Discount
    highs = [c["h"] for c in candles[-20:]]
    lows  = [c["l"] for c in candles[-20:]]
    rng_h, rng_l = max(highs), min(lows)
    rng = rng_h - rng_l
    pct = ((lp - rng_l) / rng * 100) if rng > 0 else 50
    in_discount = pct < 37.5
    in_premium  = pct > 62.5

    # Liquidity sweep
    rec   = candles[-10:]
    prior = candles[-20:-10]
    sw_h = max(c["h"] for c in rec)
    sw_l = min(c["l"] for c in rec)
    pr_h = max(c["h"] for c in prior)
    pr_l = min(c["l"] for c in prior)
    swept_low  = sw_l < pr_l and candles[-1]["c"] > pr_l
    swept_high = sw_h > pr_h and candles[-1]["c"] < pr_h
    liq = 0.9 if swept_low else -0.9 if swept_high else 0

    # Score calculation (GBM trees)
    t1 = trend * 15
    t2 = (0.7 if rsi < 35 else -0.7 if rsi > 65 else 0) * 12 + (0.9 if macd_mom > 0 else -0.9) * 10
    t3 = (1.0 if good_sess else -0.3) * 8
    t4 = liq * 12 + (5 if in_discount and trend > 0 else 5 if in_premium and trend < 0 else 0)
    t5 = (0.8 if 0.05 < (atr/lp*100) < 0.3 else -0.2) * 6 + (8 if adx > 25 else -5)
    t6 = (8 if adx > 25 and good_sess else 0) + (7 if in_discount and liq > 0 else 0)

    raw = t1 + t2 + t3 + t4 + t5 + t6
    prob = round(100 / (1 + math.exp(-raw / 15)))

    details = {
        "rsi": round(rsi, 1), "adx": round(adx, 1), "trend": round(trend, 2),
        "session": sess, "liq": "BULL" if liq > 0 else "BEAR" if liq < 0 else "NONE",
        "zone": "DISCOUNT" if in_discount else "PREMIUM" if in_premium else "EQUILIBRIUM",
        "macd": "UP" if macd_mom > 0 else "DN", "raw": round(raw, 1)
    }
    return prob, details

# ── SIGNAL GENERATION ─────────────────────────────────────────────────────────
def analyze_pair(pair):
    candles = fetch_candles(pair)
    if len(candles) < 50:
        return None

    # Higher TF bias (1h)
    h_candles = fetch_candles(pair, "1h", 50)
    htf_bias = "neutral"
    if len(h_candles) >= 20:
        closes = [c["c"] for c in h_candles]
        ma = sum(closes[-20:]) / 20
        htf_bias = "bullish" if closes[-1] > ma else "bearish"

    score, details = calc_score(candles, pair)
    if score < SCORE_THRESHOLD:
        return None

    lp  = candles[-1]["c"]
    atr = calc_atr(candles)
    dp  = 2 if pair == "XAU/USD" else 3 if "JPY" in pair else 5
    pm  = 100 if "JPY" in pair or pair == "XAU/USD" else 10000

    # Direction from score and HTF
    direction = "BUY" if htf_bias == "bullish" and details["liq"] == "BULL" else \
                "SELL" if htf_bias == "bearish" and details["liq"] == "BEAR" else \
                "BUY" if details["liq"] == "BULL" else "SELL"

    sl_dist  = atr * 1.5
    tp1_dist = sl_dist * 2
    tp2_dist = sl_dist * 3
    tp3_dist = sl_dist * 4.5

    if direction == "BUY":
        sl  = round(lp - sl_dist, dp)
        tp1 = round(lp + tp1_dist, dp)
        tp2 = round(lp + tp2_dist, dp)
        tp3 = round(lp + tp3_dist, dp)
    else:
        sl  = round(lp + sl_dist, dp)
        tp1 = round(lp - tp1_dist, dp)
        tp2 = round(lp - tp2_dist, dp)
        tp3 = round(lp - tp3_dist, dp)

    sl_pips  = round(abs(lp - sl) * pm)
    tp1_pips = round(abs(tp1 - lp) * pm)
    rr = round(tp1_dist / sl_dist, 1)

    # Position size (1% risk on $1000)
    lots = round(min(max(10 / (sl_pips * 10), 0.01), 10), 2) if sl_pips > 0 else 0.01

    return {
        "pair": pair, "direction": direction, "score": score,
        "entry": round(lp, dp), "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "rr": f"{rr}:1", "sl_pips": sl_pips, "tp1_pips": tp1_pips,
        "lots": lots, "session": details["session"],
        "htf": htf_bias, "rsi": details["rsi"], "adx": details["adx"],
        "zone": details["zone"], "liq": details["liq"]
    }

def send_signal(sig):
    arrow = "UP" if sig["direction"] == "BUY" else "DN"
    msg = (
        f"<b>PatternScan Signal</b>\n\n"
        f"<b>{arrow} {sig['direction']} {sig['pair']}</b>\n"
        f"Timeframe: 15M\n"
        f"Score: {sig['score']}/100\n"
        f"Session: {sig['session']}\n"
        f"HTF Bias: {sig['htf'].upper()}\n\n"
        f"Entry:     <code>{sig['entry']}</code>\n"
        f"Stop Loss: <code>{sig['sl']}</code> (-{sig['sl_pips']} pips)\n"
        f"TP1:       <code>{sig['tp1']}</code> (+{sig['tp1_pips']} pips)\n"
        f"TP2:       <code>{sig['tp2']}</code>\n"
        f"TP3:       <code>{sig['tp3']}</code>\n"
        f"R:R:       {sig['rr']}\n"
        f"Lots:      {sig['lots']}\n\n"
        f"RSI: {sig['rsi']} | ADX: {sig['adx']}\n"
        f"Zone: {sig['zone']} | Liq: {sig['liq']}\n\n"
        f"<i>PatternScan AI - Auto Signal</i>"
    )
    tg_send(msg)
    print(f"Signal sent: {sig['direction']} {sig['pair']} Score:{sig['score']}")

# ── MAIN LOOP ─────────────────────────────────────────────────────────────────
def main():
    print("PatternScan Bot started")
    tg_send("<b>PatternScan Bot</b>\n\nBot pornit! Vei primi semnale automate 24/7.\nScore minim: 80/100\n\n<i>PatternScan AI Terminal</i>")

    last_signals = {}  # avoid duplicate signals

    while True:
        if not is_market_open():
            print("Market closed, waiting...")
            time.sleep(CHECK_INTERVAL)
            continue

        now = datetime.now(timezone.utc)
        print(f"\nScanning {len(PAIRS)} pairs at {now.strftime('%H:%M UTC')}...")

        for pair in PAIRS:
            try:
                sig = analyze_pair(pair)
                if sig:
                    # Avoid sending same signal within 1 hour
                    key = f"{pair}_{sig['direction']}"
                    last = last_signals.get(key, 0)
                    if time.time() - last > 3600:
                        send_signal(sig)
                        last_signals[key] = time.time()
                time.sleep(3)  # rate limit TD API
            except Exception as e:
                print(f"Error analyzing {pair}: {e}")

        print(f"Scan complete. Next scan in {CHECK_INTERVAL//60} minutes.")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
