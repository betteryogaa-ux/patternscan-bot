import requests
import time
import math
import json
from datetime import datetime, timezone

# ── CONFIG ────────────────────────────────────────────────────────────────────
ANTHROPIC_KEY = "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"
TG_TOKEN      = "8037434518:AAEA7PlFI9mzukcjRKdVUowEivsyMob7rBY"
TG_CHAT       = "5348543615"
TD_KEY        = "abd8afda1f8a43e69dea53773388524e"

PAIRS = ["XAU/USD","EUR/USD","GBP/USD","USD/JPY","AUD/USD","USD/CAD","GBP/JPY"]
TF_MAP = {"15M": "15min", "1H": "1h", "4H": "4h"}
SCORE_THRESHOLD = 80
CHECK_INTERVAL  = 900  # 15 minutes

last_signals = {}  # avoid duplicates within 1 hour

# ── HELPERS ───────────────────────────────────────────────────────────────────
def dp(pair): return 2 if pair == "XAU/USD" else 3 if "JPY" in pair else 5
def pm(pair): return 10 if pair == "XAU/USD" else 100 if "JPY" in pair else 10000

def tg_send(text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        print(f"TG error: {e}")

def is_market_open():
    now = datetime.now(timezone.utc)
    day, hour = now.weekday(), now.hour
    # Weekend
    if day == 5: return False
    if day == 6 and hour < 22: return False
    if day == 4 and hour >= 22: return False
    # Night hours - no scanning 22:00-07:00 UTC (Asia session - weak signals)
    if hour >= 22 or hour < 7: return False
    return True

def get_session():
    h = datetime.now(timezone.utc).hour
    if 7 <= h < 15: return "LONDON"
    if 12 <= h < 20: return "NEW_YORK"
    if h >= 22 or h < 7: return "ASIA"
    return "OFF_HOURS"

def is_good_session(pair):
    sess = get_session()
    good = {
        "LONDON":   ["EUR/USD","GBP/USD","EUR/GBP","XAU/USD"],
        "NEW_YORK": ["USD/JPY","USD/CAD","GBP/JPY","EUR/USD","GBP/USD","XAU/USD"],
        "ASIA":     ["AUD/USD","USD/JPY","GBP/JPY"]
    }
    return pair in good.get(sess, [])

# ── FETCH CANDLES ─────────────────────────────────────────────────────────────
def fetch_candles(pair, interval="15min", count=100):
    try:
        r = requests.get("https://api.twelvedata.com/time_series", params={
            "symbol": pair, "interval": interval,
            "outputsize": count, "apikey": TD_KEY
        }, timeout=15)
        data = r.json()
        if data.get("status") == "error":
            print(f"TD error {pair}: {data.get('message')}")
            return []
        return [{"o":float(v["open"]),"h":float(v["high"]),"l":float(v["low"]),"c":float(v["close"])}
                for v in reversed(data.get("values", []))]
    except Exception as e:
        print(f"Fetch error {pair}: {e}")
        return []

# ── INDICATORS ────────────────────────────────────────────────────────────────
def ema(data, period):
    out = [data[0]]
    k = 2 / (period + 1)
    for i in range(1, len(data)):
        out.append(data[i] * k + out[-1] * (1 - k))
    return out

def calc_rsi(candles, period=14):
    closes = [c["c"] for c in candles]
    if len(closes) < period + 1: return 50
    gains = losses = 0
    for i in range(1, period + 1):
        d = closes[i] - closes[i-1]
        if d >= 0: gains += d
        else: losses -= d
    ag, al = gains/period, losses/period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i-1]
        ag = ((ag*(period-1)) + (d if d > 0 else 0)) / period
        al = ((al*(period-1)) + (-d if d < 0 else 0)) / period
    return round(100 if al == 0 else 100 - (100/(1+ag/al)), 1)

def calc_macd(candles):
    closes = [c["c"] for c in candles]
    if len(closes) < 26: return 0, 0
    ef = ema(closes, 12); es = ema(closes, 26)
    macd_line = [ef[i]-es[i] for i in range(len(closes))]
    sl = ema(macd_line, 9)
    hist = [macd_line[i]-sl[i] for i in range(len(macd_line))]
    return round(hist[-1], 6), round(hist[-1]-hist[-2] if len(hist)>=2 else 0, 6)

def calc_adx(candles, period=14):
    if len(candles) < period*2+2: return 0
    tr_l, pdm_l, ndm_l = [], [], []
    for i in range(1, len(candles)):
        hl = candles[i]["h"]-candles[i]["l"]
        hc = abs(candles[i]["h"]-candles[i-1]["c"])
        lc = abs(candles[i]["l"]-candles[i-1]["c"])
        tr_l.append(max(hl,hc,lc))
        up = candles[i]["h"]-candles[i-1]["h"]
        dn = candles[i-1]["l"]-candles[i]["l"]
        pdm_l.append(up if up>dn and up>0 else 0)
        ndm_l.append(dn if dn>up and dn>0 else 0)
    def wilder(arr, p):
        out = [sum(arr[:p])]
        for i in range(p, len(arr)): out.append(out[-1]-out[-1]/p+arr[i])
        return out
    atr=wilder(tr_l,period); pdm=wilder(pdm_l,period); ndm=wilder(ndm_l,period)
    pdi=[(pdm[i]/atr[i])*100 if atr[i]>0 else 0 for i in range(len(atr))]
    ndi=[(ndm[i]/atr[i])*100 if atr[i]>0 else 0 for i in range(len(atr))]
    dx=[abs(pdi[i]-ndi[i])/(pdi[i]+ndi[i])*100 if (pdi[i]+ndi[i])>0 else 0 for i in range(len(pdi))]
    adx=wilder(dx,period)
    return round(min(adx[-1]/period,100),1)

def calc_atr(candles, period=14):
    tr_l = []
    for i in range(1, len(candles)):
        hl=candles[i]["h"]-candles[i]["l"]
        hc=abs(candles[i]["h"]-candles[i-1]["c"])
        lc=abs(candles[i]["l"]-candles[i-1]["c"])
        tr_l.append(max(hl,hc,lc))
    return sum(tr_l[-period:])/period if tr_l else 0

def calc_bb(candles, period=20):
    closes = [c["c"] for c in candles[-period:]]
    if len(closes) < period: return 0.5
    mid = sum(closes)/period
    std = math.sqrt(sum((x-mid)**2 for x in closes)/period)
    upper, lower = mid+2*std, mid-2*std
    lp = candles[-1]["c"]
    return round((lp-lower)/(upper-lower),2) if upper>lower else 0.5

def detect_divergence(candles):
    if len(candles) < 30: return False, False
    closes = [c["c"] for c in candles]
    rsi_arr = []
    period = 14
    gains = losses = 0
    for i in range(1, period+1):
        d = closes[i]-closes[i-1]
        if d>=0: gains+=d
        else: losses-=d
    ag, al = gains/period, losses/period
    rsi_arr.append(50)
    for i in range(period+1, len(closes)):
        d = closes[i]-closes[i-1]
        ag = ((ag*(period-1))+(d if d>0 else 0))/period
        al = ((al*(period-1))+(-d if d<0 else 0))/period
        rsi_arr.append(100 if al==0 else 100-(100/(1+ag/al)))
    recent = candles[-20:]; rr = rsi_arr[-20:]
    ph=[]; pl=[]
    for i in range(2,len(recent)-2):
        if recent[i]["h"]>recent[i-1]["h"] and recent[i]["h"]>recent[i-2]["h"] and recent[i]["h"]>recent[i+1]["h"] and recent[i]["h"]>recent[i+2]["h"]:
            ph.append({"i":i,"v":recent[i]["h"]})
        if recent[i]["l"]<recent[i-1]["l"] and recent[i]["l"]<recent[i-2]["l"] and recent[i]["l"]<recent[i+1]["l"] and recent[i]["l"]<recent[i+2]["l"]:
            pl.append({"i":i,"v":recent[i]["l"]})
    bull = bear = False
    if len(ph)>=2:
        p1,p2=ph[-2],ph[-1]
        if p2["v"]>p1["v"] and rr[p2["i"]]<rr[p1["i"]]: bear=True
    if len(pl)>=2:
        p1,p2=pl[-2],pl[-1]
        if p2["v"]<p1["v"] and rr[p2["i"]]>rr[p1["i"]]: bull=True
    return bull, bear

def detect_bos(candles):
    n = len(candles)
    if n < 20: return "none"
    sl = candles[-20:]
    sh=[]; sv=[]
    for i in range(2,len(sl)-2):
        if sl[i]["h"]>sl[i-1]["h"] and sl[i]["h"]>sl[i-2]["h"] and sl[i]["h"]>sl[i+1]["h"] and sl[i]["h"]>sl[i+2]["h"]:
            sh.append(sl[i]["h"])
        if sl[i]["l"]<sl[i-1]["l"] and sl[i]["l"]<sl[i-2]["l"] and sl[i]["l"]<sl[i+1]["l"] and sl[i]["l"]<sl[i+2]["l"]:
            sv.append(sl[i]["l"])
    lp = candles[-1]["c"]
    if len(sh)>=2 and lp>sh[-2]: return "bullish"
    if len(sv)>=2 and lp<sv[-2]: return "bearish"
    return "none"

def detect_fvg(candles):
    n = len(candles)
    if n < 3: return "none"
    c0,c2 = candles[-1],candles[-3]
    if c0["l"]>c2["h"]: return "bullish"
    if c0["h"]<c2["l"]: return "bearish"
    return "none"

def detect_liq_sweep(candles):
    if len(candles) < 20: return "none"
    rec=candles[-10:]; prior=candles[-20:-10]
    sw_h=max(c["h"] for c in rec); sw_l=min(c["l"] for c in rec)
    pr_h=max(c["h"] for c in prior); pr_l=min(c["l"] for c in prior)
    lp=candles[-1]["c"]
    if sw_l<pr_l and lp>pr_l: return "bullish"
    if sw_h>pr_h and lp<pr_h: return "bearish"
    return "none"

def calc_pd_zone(candles):
    highs=[c["h"] for c in candles[-20:]]; lows=[c["l"] for c in candles[-20:]]
    rng_h=max(highs); rng_l=min(lows); rng=rng_h-rng_l
    lp=candles[-1]["c"]
    pct=((lp-rng_l)/rng*100) if rng>0 else 50
    if pct<37.5: return "DISCOUNT"
    if pct>62.5: return "PREMIUM"
    return "EQUILIBRIUM"

# ── 3 AGENT VOTING ────────────────────────────────────────────────────────────
def run_agents(candles, div_bull, div_bear, htf_bias, pair):
    closes=[c["c"] for c in candles]
    rsi=calc_rsi(candles)
    hist,hist_mom=calc_macd(candles)
    lp=candles[-1]["c"]

    # Technical Agent
    bos=detect_bos(candles); fvg=detect_fvg(candles); liq=detect_liq_sweep(candles)
    tech=0
    if bos=="bullish": tech+=0.4
    if bos=="bearish": tech-=0.4
    if fvg=="bullish": tech+=0.2
    if fvg=="bearish": tech-=0.2
    if liq=="bullish": tech+=0.3
    if liq=="bearish": tech-=0.3
    tech=max(-1,min(1,tech))

    # Sentiment Agent
    sent=0
    if rsi<30: sent=0.8
    elif rsi<45: sent=0.3
    elif rsi>70: sent=-0.8
    elif rsi>55: sent=-0.3
    if hist>0 and hist_mom>0: sent+=0.2
    if hist<0 and hist_mom<0: sent-=0.2
    if htf_bias=="bullish": sent+=0.15
    if htf_bias=="bearish": sent-=0.15
    if div_bull: sent+=0.2
    if div_bear: sent-=0.2
    sent=max(-1,min(1,sent))

    # Risk Agent
    risk=0
    atr=calc_atr(candles); atr_pct=atr/lp*100 if lp>0 else 0
    if 0.05<atr_pct<0.4: risk=0.5
    elif atr_pct>=0.4: risk=-0.2
    if is_good_session(pair): risk+=0.3
    risk=max(-1,min(1,risk))

    # Weighted vote
    final=(tech*1.0+sent*1.0+risk*1.0)/3
    return round(tech,2), round(sent,2), round(risk,2), round(final,2)

# ── SCORE ─────────────────────────────────────────────────────────────────────
def calc_score(candles, pair, htf_bias):
    if len(candles)<30: return 0
    closes=[c["c"] for c in candles]; lp=candles[-1]["c"]
    rsi=calc_rsi(candles)
    hist,hist_mom=calc_macd(candles)
    adx=calc_adx(candles)
    atr=calc_atr(candles); atr_pct=atr/lp*100 if lp>0 else 0
    bb=calc_bb(candles)
    div_bull,div_bear=detect_divergence(candles)
    liq=detect_liq_sweep(candles)
    bos=detect_bos(candles)
    zone=calc_pd_zone(candles)
    sess=is_good_session(pair)

    e20=ema(closes,20)[-1]; e50=ema(closes,min(50,len(closes)-1))[-1]
    trend=1.0 if lp>e20>e50 else -1.0 if lp<e20<e50 else 0.3

    t1=trend*15+(0.8 if htf_bias=="bullish" else -0.8 if htf_bias=="bearish" else 0)*12
    t2=(0.7 if rsi<35 else -0.7 if rsi>65 else 0)*12+(0.9 if hist_mom>0 else -0.9)*10+(6 if div_bull or div_bear else 0)
    t3=(1.0 if sess else -0.3)*8
    t4=(0.9 if liq=="bullish" else -0.9 if liq=="bearish" else 0)*12+(0.5 if bos=="bullish" else -0.5 if bos=="bearish" else 0)*8+(5 if zone=="DISCOUNT" and trend>0 else 5 if zone=="PREMIUM" and trend<0 else 0)
    t5=(0.8 if 0.05<atr_pct<0.3 else -0.2)*6+(8 if adx>25 else -5)+(6 if bb<0.2 and trend>0 else 6 if bb>0.8 and trend<0 else 0)
    t6=(8 if adx>25 and sess else 0)+(7 if zone=="DISCOUNT" and liq=="bullish" else 7 if zone=="PREMIUM" and liq=="bearish" else 0)+(6 if htf_bias!="neutral" and adx>25 else 0)

    raw=t1+t2+t3+t4+t5+t6
    prob=round(100/(1+math.exp(-raw/15)))
    return prob

# ── CLAUDE AI DECISION ────────────────────────────────────────────────────────
def claude_decision(pair, tf, candles, score, htf_bias, agents, adx, rsi, bb, zone, liq, bos, div_bull, div_bear):
    lp=candles[-1]["c"]; d=dp(pair); sess=get_session()
    tech,sent,risk,final=agents
    last5=" ".join([("UP" if c["c"]>=c["o"] else "DN")+"@"+str(round(c["c"],d)) for c in candles[-5:]])

    prompt=f"""You are an elite SMC forex trader. Analyse {pair} on {tf}.

Price: {round(lp,d)}
Score: {score}/100
Session: {sess}
HTF Bias (4H): {htf_bias.upper()}

Indicators:
- ADX(14): {adx} {'STRONG' if adx>25 else 'WEAK'}
- RSI(14): {rsi} {'OVERSOLD' if rsi<30 else 'OVERBOUGHT' if rsi>70 else 'NEUTRAL'}
- BB%: {bb} {'LOW' if bb<0.2 else 'HIGH' if bb>0.8 else 'MID'}
- P/D Zone: {zone}
- Liquidity: {liq}
- BOS: {bos}
- Divergence: {'BULLISH' if div_bull else 'BEARISH' if div_bear else 'NONE'}

Agent Voting:
- Technical: {tech}
- Sentiment: {sent}
- Risk: {risk}
- Final Vote: {final} => {'BUY' if final>0.1 else 'SELL' if final<-0.1 else 'WAIT'}

Last 5 candles: {last5}

Rules:
- Only BUY or SELL if ALL conditions align
- ADX must be > 25 for valid signal
- R:R minimum 2:1
- If any doubt, output WAIT

Respond ONLY with valid JSON:
{{"decision":"BUY or SELL or WAIT","confidence":0-100,"entry":{round(lp,d)},"stopLoss":number,"takeProfit1":number,"takeProfit2":number,"takeProfit3":number,"riskReward":"X:1","bias":"bullish or bearish or neutral","reasoning":"max 1 sentence"}}"""

    try:
        r=requests.post("https://api.anthropic.com/v1/messages",
            headers={"Content-Type":"application/json","x-api-key":ANTHROPIC_KEY,"anthropic-version":"2023-06-01"},
            json={"model":"claude-haiku-4-5-20251001","max_tokens":300,"messages":[{"role":"user","content":prompt}]},
            timeout=30)
        data=r.json()
        txt="".join(b.get("text","") for b in data.get("content",[]) if b.get("type")=="text")
        s=txt.find("{"); e=txt.rfind("}")
        if s>=0 and e>s:
            return json.loads(txt[s:e+1])
    except Exception as ex:
        print(f"Claude error: {ex}")
    return None

# ── SEND SIGNAL ───────────────────────────────────────────────────────────────
def send_signal(pair, tf, parsed, score, sess):
    d=dp(pair); p=pm(pair)
    fv=lambda v: str(round(v,d)) if v and not math.isnan(v) else "--"
    entry=parsed.get("entry",0)
    sl=parsed.get("stopLoss",0)
    tp1=parsed.get("takeProfit1",0)
    tp2=parsed.get("takeProfit2",0)
    tp3=parsed.get("takeProfit3",0)
    rr=parsed.get("riskReward","--")
    dec=parsed.get("decision","")
    reason=parsed.get("reasoning","--")
    arrow="UP" if dec=="BUY" else "DN"
    slp=round(abs(entry-sl)*p) if entry and sl else "--"
    t1p=round(abs(tp1-entry)*p) if tp1 and entry else "--"
    lots=round(min(max(10/(slp*10),0.01),10),2) if isinstance(slp,int) and slp>0 else 0.01

    msg=(
        f"<b>PatternScan Signal</b>\n\n"
        f"<b>{arrow} {dec} {pair}</b>\n"
        f"TF: {tf} | Score: {score}/100\n"
        f"Session: {sess}\n\n"
        f"Entry:  <code>{fv(entry)}</code>\n"
        f"SL:     <code>{fv(sl)}</code> (-{slp} pips)\n"
        f"TP1:    <code>{fv(tp1)}</code> (+{t1p} pips)\n"
        f"TP2:    <code>{fv(tp2)}</code>\n"
        f"TP3:    <code>{fv(tp3)}</code>\n"
        f"R:R:    {rr} | Lots: {lots}\n\n"
        f"<i>{reason}</i>\n\n"
        f"<i>PatternScan AI Terminal</i>"
    )
    tg_send(msg)
    print(f"Signal sent: {dec} {pair} Score:{score}")

# ── MAIN LOOP ─────────────────────────────────────────────────────────────────
def analyze_pair(pair):
    # Primary TF candles
    candles=fetch_candles(pair,"15min",60)
    if len(candles)<50: return
    time.sleep(10)  # respect 8 req/min limit

    # Use last candles to estimate HTF bias (no extra API call)
    closes=[c["c"] for c in candles]
    ma50=sum(closes[-50:])/50 if len(closes)>=50 else sum(closes)/len(closes)
    htf_bias="bullish" if closes[-1]>ma50 else "bearish" 

    # Score
    score=calc_score(candles,pair,htf_bias)
    if score<SCORE_THRESHOLD:
        print(f"{pair}: Score {score} < {SCORE_THRESHOLD}, skip")
        return

    # Indicators for Claude
    adx=calc_adx(candles)
    rsi=calc_rsi(candles)
    bb=calc_bb(candles)
    zone=calc_pd_zone(candles)
    liq=detect_liq_sweep(candles)
    bos=detect_bos(candles)
    div_bull,div_bear=detect_divergence(candles)
    agents=run_agents(candles,div_bull,div_bear,htf_bias,pair)

    # Claude AI decision
    parsed=claude_decision(pair,"15M",candles,score,htf_bias,agents,adx,rsi,bb,zone,liq,bos,div_bull,div_bear)
    if not parsed: return
    if parsed.get("decision")=="WAIT": 
        print(f"{pair}: Claude says WAIT")
        return

    # Avoid duplicate within 1 hour
    key=f"{pair}_{parsed.get('decision')}"
    if time.time()-last_signals.get(key,0)<3600:
        print(f"{pair}: Duplicate signal, skip")
        return

    send_signal(pair,"15M",parsed,score,get_session())
    last_signals[key]=time.time()

def main():
    print("PatternScan Bot v2.0 started - Claude AI integrated")
    tg_send(
        "<b>PatternScan Bot v2.0</b>\n\n"
        "Bot pornit cu Claude AI!\n"
        "Semnale automate 24/7\n"
        "Score minim: 80/100\n"
        "Claude AI validare finala\n\n"
        "<i>PatternScan AI Terminal</i>"
    )

    while True:
        if not is_market_open():
            print("Market closed, waiting 15 min...")
            time.sleep(CHECK_INTERVAL)
            continue

        now=datetime.now(timezone.utc)
        print(f"\nScanning {len(PAIRS)} pairs at {now.strftime('%H:%M UTC')}...")

        for pair in PAIRS:
            try:
                analyze_pair(pair)
            except Exception as e:
                print(f"Error {pair}: {e}")
            time.sleep(10)

        print(f"Scan complete. Next in {CHECK_INTERVAL//60}min.")
        time.sleep(CHECK_INTERVAL)

if __name__=="__main__":
    main()
