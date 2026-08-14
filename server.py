#!/usr/bin/env python3
"""Mock dashboard server serving static files and JSON API endpoints."""
import hashlib
import http.server
import json
import os
import random
import socketserver
import time
from urllib.parse import parse_qs, unquote, urlparse

PORT = int(os.environ.get("PORT", "8765"))
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))

CATALOG = {
    "KOSPI": [
        {"ticker": "005930", "name": "삼성전자", "sector": "반도체", "ref_price": 85700},
        {"ticker": "000660", "name": "SK하이닉스", "sector": "반도체", "ref_price": 2158000},
        {"ticker": "005380", "name": "현대차", "sector": "자동차", "ref_price": 258000},
        {"ticker": "051910", "name": "LG화학", "sector": "화학", "ref_price": 341000},
        {"ticker": "035420", "name": "NAVER", "sector": "인터넷", "ref_price": 196000},
        {"ticker": "207940", "name": "삼성바이오로직스", "sector": "제약/바이오", "ref_price": 925000},
        {"ticker": "068270", "name": "셀트리온", "sector": "제약/바이오", "ref_price": 188000},
        {"ticker": "105560", "name": "KB금융", "sector": "금융", "ref_price": 96200},
    ],
    "KOSDAQ": [
        {"ticker": "091990", "name": "셀트리온헬스케어", "sector": "제약/바이오", "ref_price": 88900},
        {"ticker": "293490", "name": "카카오게임즈", "sector": "게임", "ref_price": 18200},
        {"ticker": "263750", "name": "펄어비스", "sector": "게임", "ref_price": 38900},
        {"ticker": "247540", "name": "에코프로비엠", "sector": "2차전지", "ref_price": 138000},
        {"ticker": "066970", "name": "엘앤에프", "sector": "2차전지", "ref_price": 76800},
        {"ticker": "086900", "name": "메디톡스", "sector": "제약/바이오", "ref_price": 198000},
        {"ticker": "214150", "name": "클래시스", "sector": "의료기기", "ref_price": 61800},
        {"ticker": "041510", "name": "에스엠", "sector": "엔터테인먼트", "ref_price": 124000},
    ],
    "NASDAQ": [
        {"ticker": "AAPL", "name": "Apple", "sector": "Technology", "ref_price": 234.50},
        {"ticker": "MSFT", "name": "Microsoft", "sector": "Technology", "ref_price": 535.00},
        {"ticker": "NVDA", "name": "NVIDIA", "sector": "Semiconductors", "ref_price": 195.20},
        {"ticker": "AMZN", "name": "Amazon", "sector": "Consumer Discretionary", "ref_price": 232.80},
        {"ticker": "META", "name": "Meta", "sector": "Technology", "ref_price": 810.00},
        {"ticker": "GOOGL", "name": "Alphabet", "sector": "Communication Services", "ref_price": 210.00},
        {"ticker": "TSLA", "name": "Tesla", "sector": "Automotive", "ref_price": 345.00},
        {"ticker": "AVGO", "name": "Broadcom", "sector": "Semiconductors", "ref_price": 325.00},
    ],
}

MARKET_LABELS = {
    "US": "NASDAQ",
    "NASDAQ": "NASDAQ",
    "KOSPI": "KOSPI",
    "KOSDAQ": "KOSDAQ",
}

WATCHLIST = {}


def normalize_market(market):
    return MARKET_LABELS.get(str(market or "").upper(), "KOSPI")


def market_currency(market):
    return "USD" if normalize_market(market) == "NASDAQ" else "KRW"


def base_price_for_market(market):
    return random.uniform(80, 1200) if normalize_market(market) == "NASDAQ" else random.uniform(12000, 420000)


def stable_ratio(*parts, lo=-1.0, hi=1.0):
    seed = "|".join(str(part) for part in parts)
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    raw = int(digest[:12], 16) / float((16 ** 12) - 1)
    return lo + (hi - lo) * raw


def market_tick_size(market, price):
    market = normalize_market(market)
    if market == "NASDAQ":
        return 0.01
    price = float(price)
    if price < 2000:
        return 1
    if price < 5000:
        return 5
    if price < 20000:
        return 10
    if price < 50000:
        return 50
    if price < 200000:
        return 100
    if price < 500000:
        return 500
    return 1000


def round_price_for_market(market, price):
    market = normalize_market(market)
    tick = market_tick_size(market, price)
    if market == "NASDAQ":
        return round(float(price), 2)
    return int(round(float(price) / tick) * tick)


def ticker_with_suffix(market, ticker):
    ticker = str(ticker or "").upper().replace(".KS", "").replace(".KQ", "")
    market = normalize_market(market)
    if market == "KOSPI":
        return f"{ticker}.KS"
    if market == "KOSDAQ":
        return f"{ticker}.KQ"
    return ticker


def catalog_entry(market, ticker=None):
    market = normalize_market(market)
    pool = CATALOG[market]
    if ticker:
        bare = str(ticker).upper().replace(".KS", "").replace(".KQ", "")
        for item in pool:
            if item["ticker"].upper() == bare:
                return item
    return random.choice(pool)


def classify_signal(close, ma, lower, upper):
    if close <= lower * 0.99:
        return "STRONG_BUY"
    if close <= lower * 1.01:
        return "BUY"
    if close < ma * 0.992:
        return "WATCH_BUY"
    if close >= upper * 1.01:
        return "STRONG_SELL"
    if close >= upper * 0.99:
        return "SELL"
    if close > ma * 1.008:
        return "WATCH_SELL"
    return "NEUTRAL"


def make_signal_snapshot(market, period=20, pct=5, entry=None):
    market = normalize_market(market)
    entry = entry or catalog_entry(market)
    anchor = float(entry.get("ref_price") or base_price_for_market(market))
    minute_bucket = int(time.time() // 60)
    day_bucket = int(time.time() // 86400)

    # 현재가: ref_price 기준으로 -3% ~ +3% 변동 (좀 더 현실적인 움직임)
    close_move = stable_ratio(
        market, entry["ticker"], minute_bucket, "close",
        lo=-3.0 if market == "NASDAQ" else -3.5,
        hi=3.0 if market == "NASDAQ" else 3.5,
    )
    close = round_price_for_market(market, anchor * (1 + close_move / 100))

    # MA: 현재가 기준으로 -2% ~ +2% 편차 (현재가와 일관성 유지)
    ma_bias = stable_ratio(market, entry["ticker"], period, minute_bucket, "ma", lo=-2.0, hi=2.0)
    ma = round_price_for_market(market, close * (1 + ma_bias / 100))
    lower = round_price_for_market(market, ma * (1 - float(pct) / 100))
    upper = round_price_for_market(market, ma * (1 + float(pct) / 100))

    # 등락률: 현재가 변동률과 동일하게 (close_move 기반)
    change_1d = round(close_move, 2)
    pct_from_ma = round((close - ma) / ma * 100, 2) if ma else 0
    pct_from_lower = round((close - lower) / lower * 100, 2) if lower else 0
    pct_from_upper = round((close - upper) / upper * 100, 2) if upper else 0

    # 52주 고가: 현재가보다 항상 높게 설정 (10%~40% 위)
    high_52w_offset = stable_ratio(market, entry["ticker"], "52w", lo=10.0, hi=40.0)
    high_52w = round_price_for_market(
        market,
        close * (1 + high_52w_offset / 100),
    )
    from_52w_high = round((close - high_52w) / high_52w * 100, 2) if high_52w else 0
    signal = classify_signal(close, ma, lower, upper)
    return {
        "ticker": entry["ticker"],
        "display_ticker": entry["ticker"],
        "display_name": entry["name"],
        "market": market,
        "sector": entry["sector"],
        "close": close,
        "ma": ma,
        "lower": lower,
        "upper": upper,
        "change_1d": change_1d,
        "pct_from_ma": pct_from_ma,
        "pct_from_lower": pct_from_lower,
        "pct_from_upper": pct_from_upper,
        "from_52w_high": from_52w_high,
        "signal": signal,
        "updated_at": "2026-08-14 10:00:00",
        "period": int(period),
        "pct": float(pct),
        "currency": market_currency(market),
    }


def generate_stocks(market, count=8, period=20, pct=5):
    market = normalize_market(market)
    pool = CATALOG[market][:]
    random.shuffle(pool)
    selected = pool[: min(int(count), len(pool))]
    stocks = []
    for entry in selected:
        snap = make_signal_snapshot(market, period=period, pct=pct, entry=entry)
        stocks.append({
            "ticker": entry["ticker"],
            "name": entry["name"],
            "display_name": entry["name"],
            "display_ticker": entry["ticker"],
            "market": market,
            "close": snap["close"],
            "ma": snap["ma"],
            "lower": snap["lower"],
            "upper": snap["upper"],
            "change_1d": snap["change_1d"],
            "change_pct": snap["change_1d"],
            "pct_from_ma": snap["pct_from_ma"],
            "pct_from_lower": snap["pct_from_lower"],
            "pct_from_upper": snap["pct_from_upper"],
            "from_52w_high": snap["from_52w_high"],
            "sector": entry["sector"],
            "volume": random.randint(100000, 5000000),
            "signal": snap["signal"],
            "score": round(random.uniform(60, 99), 1),
            "rsi": round(random.uniform(20, 80), 1),
            "ma_gap": snap["pct_from_ma"],
        })
    return stocks


def create_watchlist_item(market, ticker, period=20, pct=5):
    market = normalize_market(market)
    entry = catalog_entry(market, ticker)
    signal = make_signal_snapshot(market, period=period, pct=pct, entry=entry)
    key = f"{market}:{entry['ticker']}"
    return {
        "key": key,
        "market": market,
        "ticker": ticker_with_suffix(market, entry["ticker"]),
        "display_ticker": entry["ticker"],
        "display_name": entry["name"],
        "sector": entry["sector"],
        "period": int(period),
        "pct": float(pct),
        "alert_count": 0,
        "signal_changed": False,
        "active": True,
        "added_at": "2026-08-14 10:00:00",
        "last_signal": signal,
        "history": [{"time": "10:00:00", "signal": signal["signal"], "close": signal["close"]}],
    }


def update_watchlist_items():
    items = []
    for item in WATCHLIST.values():
        prev_signal = item["last_signal"]["signal"]
        entry = catalog_entry(item["market"], item["display_ticker"])
        signal = make_signal_snapshot(item["market"], period=item["period"], pct=item["pct"], entry=entry)
        item["signal_changed"] = signal["signal"] != prev_signal
        if item["signal_changed"]:
            item["alert_count"] = int(item.get("alert_count", 0)) + 1
        item["last_signal"] = signal
        item.setdefault("history", []).append({
            "time": "10:00:00",
            "signal": signal["signal"],
            "close": signal["close"],
        })
        item["history"] = item["history"][-10:]
        items.append(item)
    return items


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/api/watchlist/status":
            return self.send_json({"status": "ok", "mode": "mock", "timestamp": "2026-08-14T10:00:00Z"})

        if path == "/api/watchlist":
            return self.send_json(list(WATCHLIST.values()))

        if path.startswith("/api/index/"):
            market = normalize_market(path.split("/")[-1])
            period = int(qs.get("period", [20])[0])
            pct = float(qs.get("pct", [5])[0])
            return self.send_json({
                "market": market,
                "signal": make_signal_snapshot(market, period=period, pct=pct),
                "return_1y": round(random.uniform(-18, 34), 2),
            })

        if path.startswith("/api/scan/"):
            market = normalize_market(path.split("/")[-1])
            period = int(qs.get("period", [20])[0])
            pct = float(qs.get("pct", [5])[0])
            limit = int(qs.get("limit", [20])[0])
            signal_filter = str(qs.get("signal", ["ALL"])[0]).upper()
            stocks = generate_stocks(market, count=max(limit, 8), period=period, pct=pct)
            if signal_filter != "ALL":
                if signal_filter == "BUY":
                    stocks = [s for s in stocks if "BUY" in s["signal"]]
                elif signal_filter == "SELL":
                    stocks = [s for s in stocks if "SELL" in s["signal"]]
            return self.send_json({
                "market": market,
                "scanned": random.randint(150, 220),
                "stocks": stocks[:limit],
            })

        if path.startswith("/api/chart/"):
            parts = path.strip("/").split("/")
            if len(parts) >= 4:
                market = normalize_market(parts[2])
                ticker = unquote(parts[3])
                period = int(qs.get("period", [20])[0])
                pct = float(qs.get("pct", [5])[0])
                entry = catalog_entry(market, ticker)
                signal = make_signal_snapshot(market, period=period, pct=pct, entry=entry)
                return self.send_json({
                    "market": market,
                    "ticker": ticker_with_suffix(market, entry["ticker"]),
                    "display_ticker": entry["ticker"],
                    "display_name": entry["name"],
                    "signal": signal,
                })

        if path.startswith("/api/"):
            return self.send_json({"error": f"Unknown API endpoint: {path}"}, status=404)

        return self.serve_static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8") if content_len else "{}"
        try:
            req = json.loads(body)
        except json.JSONDecodeError:
            req = {}

        if path == "/api/watchlist/group_scan":
            market = normalize_market(req.get("market", "KOSPI"))
            limit = int(req.get("limit", 8))
            period = int(req.get("period", 20))
            pct = float(req.get("pct", 5))
            preset = req.get("preset", "256")
            stocks = generate_stocks(market, count=limit, period=period, pct=pct)
            buy = sum(1 for s in stocks if "BUY" in s["signal"])
            sell = sum(1 for s in stocks if "SELL" in s["signal"])
            return self.send_json({
                "success": True,
                "market": market,
                "matched": len(stocks),
                "scanned": random.randint(150, 220),
                "summary": {"buy": buy, "sell": sell, "hold": len(stocks) - buy - sell},
                "stocks": stocks,
                "preset": preset,
                "updated_at": "2026-08-14T10:00:00+09:00",
            })

        if path == "/api/watchlist/add":
            market = normalize_market(req.get("market", "KOSPI"))
            ticker = req.get("ticker", "")
            period = int(req.get("period", 20))
            pct = float(req.get("pct", 5))
            item = create_watchlist_item(market, ticker, period=period, pct=pct)
            WATCHLIST[item["key"]] = item
            return self.send_json({"ok": True, "data": item})

        if path == "/api/watchlist/update":
            return self.send_json({"items": update_watchlist_items()})

        if path.startswith("/api/"):
            return self.send_json({"error": f"Unknown API endpoint: {path}"}, status=404)

        return self.send_json({"error": "Unsupported POST path"}, status=404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/watchlist/remove/"):
            raw_key = unquote(path.split("/api/watchlist/remove/", 1)[1])
            WATCHLIST.pop(raw_key, None)
            return self.send_json({"ok": True, "removed": raw_key})
        if path.startswith("/api/"):
            return self.send_json({"error": f"Unknown API endpoint: {path}"}, status=404)
        return self.send_json({"error": "Unsupported DELETE path"}, status=404)

    def serve_static(self, path):
        safe_path = path or "/"
        if safe_path == "/":
            safe_path = "/deployed_dashboard.html"
        file_path = os.path.join(STATIC_DIR, safe_path.lstrip("/"))
        if os.path.exists(file_path) and os.path.isfile(file_path):
            self.path = safe_path
            return http.server.SimpleHTTPRequestHandler.do_GET(self)
        self.send_error(404)

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")


if __name__ == "__main__":
    os.chdir(STATIC_DIR)
    with ReusableTCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print(f"Server running at http://0.0.0.0:{PORT}/")
        print(f"Dashboard: http://0.0.0.0:{PORT}/deployed_dashboard.html")
        httpd.serve_forever()
