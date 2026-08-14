#!/usr/bin/env python3
"""Trace why /api/watchlist/group_scan returns 0 matches for target presets.

Compares /api/scan/{market} with /api/watchlist/group_scan, mutates parameters
(market, preset, signal_mode, period, pct, limit), and reports which conditions
produce non-zero matches. This isolates the backend-side filter mismatch.
"""
import argparse
import json
import time
from urllib.request import Request, urlopen
from urllib.parse import urlencode

DEFAULT_BASE_URL = "https://5050-iuavos2lbiw7gvl484136-02b9cc79.sandbox.novita.ai"
DEFAULT_TARGET_GROUPS = [
    {"name": "코스피 256 기법", "market": "KOSPI", "preset": "256", "period": 20, "pct": 5, "limit": 8},
    {"name": "코스닥 밥그릇 3번", "market": "KOSDAQ", "preset": "BABGURU", "period": 20, "pct": 5, "limit": 8},
    {"name": "NASDAQ 공구리", "market": "NASDAQ", "preset": "GONGGURI", "period": 20, "pct": 5, "limit": 8},
    {"name": "코스피 오돌이", "market": "KOSPI", "preset": "ODOL", "period": 20, "pct": 5, "limit": 8},
]

SIGNAL_MODES = ["BUY", "SELL", "256", "BABGURU", "GONGGURI", "ODOL"]
PERIODS = [5, 10, 15, 20, 30, 60]
PCTS = [1, 3, 5, 10, 15]


def fetch_text(url: str, timeout: int = 30) -> str:
    with urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_json(url: str, payload: dict | None = None, timeout: int = 60) -> dict:
    if payload is None:
        request = Request(url)
    else:
        data = json.dumps(payload).encode("utf-8")
        request = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def scan_market(base_url: str, market: str, period: int = 20, pct: int = 5, limit: int = 10, signal: str = "BUY") -> dict:
    query = urlencode({"period": period, "pct": pct, "limit": limit, "signal": signal})
    return fetch_json(f"{base_url}/api/scan/{market}?{query}")


def group_scan(base_url: str, market: str, preset: str, signal_mode: str, period: int, pct: int, limit: int) -> dict:
    payload = {
        "market": market,
        "preset": preset,
        "signal_mode": signal_mode,
        "limit": limit,
        "period": period,
        "pct": pct,
    }
    return fetch_json(f"{base_url}/api/watchlist/group_scan", payload=payload)


def trace_group(base_url: str, group: dict) -> dict:
    market = group["market"]
    preset = group["preset"]
    baseline = scan_market(base_url, market)
    baseline_count = len(baseline.get("stocks", []))
    baseline_tickers = [s.get("display_ticker") for s in baseline.get("stocks", [])[:5]]

    # Default attempt mirroring the UI
    default_result = group_scan(
        base_url, market, preset, preset, group["period"], group["pct"], group["limit"]
    )

    # Vary signal_mode: does the backend expect the preset string or a generic BUY/SELL?
    signal_mode_attempts = []
    for signal_mode in SIGNAL_MODES:
        try:
            result = group_scan(base_url, market, preset, signal_mode, group["period"], group["pct"], group["limit"])
            signal_mode_attempts.append({
                "signal_mode": signal_mode,
                "matched": result.get("matched"),
                "scanned": result.get("scanned"),
                "sample_tickers": [s.get("display_ticker") for s in result.get("stocks", [])[:3]],
            })
        except Exception as exc:  # noqa: BLE001
            signal_mode_attempts.append({"signal_mode": signal_mode, "error": str(exc)})

    # Vary period / pct around the default to see if strict thresholds kill all matches
    param_attempts = []
    for period in PERIODS:
        for pct in PCTS:
            try:
                result = group_scan(base_url, market, preset, preset, period, pct, group["limit"])
                param_attempts.append({
                    "period": period,
                    "pct": pct,
                    "matched": result.get("matched"),
                    "scanned": result.get("scanned"),
                })
            except Exception as exc:  # noqa: BLE001
                param_attempts.append({"period": period, "pct": pct, "error": str(exc)})

    non_zero_signal = [a for a in signal_mode_attempts if a.get("matched") not in (None, 0)]
    non_zero_param = [a for a in param_attempts if a.get("matched") not in (None, 0)]

    return {
        "name": group["name"],
        "market": market,
        "preset": preset,
        "baseline_scan": {
            "scanned": baseline.get("scanned"),
            "result_count": baseline_count,
            "sample_tickers": baseline_tickers,
        },
        "default_group_scan": {
            "matched": default_result.get("matched"),
            "scanned": default_result.get("scanned"),
            "stocks": default_result.get("stocks", []),
        },
        "signal_mode_variants": signal_mode_attempts,
        "parameter_variants": param_attempts,
        "non_zero_signal_variants": non_zero_signal[:5],
        "non_zero_parameter_variants": non_zero_param[:5],
        "root_cause_hypothesis": infer_root_cause(non_zero_signal, non_zero_param, baseline_count),
    }


def infer_root_cause(non_zero_signal: list, non_zero_param: list, baseline_count: int) -> list[str]:
    hypotheses = []
    if not baseline_count:
        hypotheses.append("The baseline /api/scan for this market returned 0 stocks, so no filter can match anything.")
    if non_zero_signal:
        best = non_zero_signal[0]
        hypotheses.append(
            f"signal_mode='{best['signal_mode']}' produced matches while preset='{best.get('preset')}' did not. "
            "The backend likely expects a generic signal (BUY/SELL) rather than the preset code in signal_mode."
        )
    if non_zero_param:
        best = non_zero_param[0]
        hypotheses.append(
            f"Looser parameters (period={best['period']}, pct={best['pct']}) produced matches. "
            "The default threshold may be too strict for the current dataset."
        )
    if not non_zero_signal and not non_zero_param and baseline_count:
        hypotheses.append(
            "Baseline scan has stocks, but no signal_mode or parameter variation produced matches. "
            "The preset filter may be case-sensitive, misspelled, or require an additional field not exposed in the UI."
        )
    if not hypotheses:
        hypotheses.append("No conclusive hypothesis; backend response shape or additional parameters may be needed.")
    return hypotheses


def main() -> int:
    parser = argparse.ArgumentParser(description="Trace /api/watchlist/group_scan zero-match causes.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--group", choices=[g["name"] for g in DEFAULT_TARGET_GROUPS], action="append")
    args = parser.parse_args()

    target_groups = [g for g in DEFAULT_TARGET_GROUPS if not args.group or g["name"] in args.group]
    report = []
    for group in target_groups:
        started = time.time()
        report.append({
            "elapsed_sec": round(time.time() - started, 2),
            **trace_group(args.base_url, group),
        })
        # Be polite to the upstream server
        time.sleep(0.5)

    output = {"target_groups": target_groups, "traces": report}
    print(json.dumps(output, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
