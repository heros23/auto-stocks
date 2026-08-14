#!/usr/bin/env python3
import argparse
import json
import re
import sys
import time
from urllib.request import Request, urlopen
from urllib.parse import urlencode

DEFAULT_BASE_URL = "https://5050-iuavos2lbiw7gvl484136-02b9cc79.sandbox.novita.ai"
DEFAULT_GROUPS = [
    {"name": "코스피 256 기법", "market": "KOSPI", "preset": "256", "period": 20, "pct": 5, "limit": 8},
    {"name": "코스닥 밥그릇 3번", "market": "KOSDAQ", "preset": "BABGURU", "period": 20, "pct": 5, "limit": 8},
    {"name": "NASDAQ 공구리", "market": "NASDAQ", "preset": "GONGGURI", "period": 20, "pct": 5, "limit": 8},
    {"name": "코스피 오돌이", "market": "KOSPI", "preset": "ODOL", "period": 20, "pct": 5, "limit": 8},
]
SCAN_MARKETS = ["US", "KOSPI", "KOSDAQ"]
MATRIX_MARKETS = ["US", "KOSPI", "KOSDAQ", "NASDAQ"]
PRESETS = ["256", "BABGURU", "GONGGURI", "ODOL"]


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


def detect_runtime_mode(html: str) -> dict:
    watchlist_timer_tick_seconds = None
    watchlist_default_refresh_seconds = None
    account_poll_seconds = None

    watchlist_match = re.search(
        r"S\.wl\.cd\s*=\s*setInterval\(\(\)\s*=>\s*\{.*?\},\s*(\d+)\)",
        html,
        re.S,
    )
    if watchlist_match:
        watchlist_timer_tick_seconds = int(watchlist_match.group(1)) // 1000

    watchlist_default_match = re.search(r'<option value="(\d+)" selected>30초</option>', html)
    if watchlist_default_match:
        watchlist_default_refresh_seconds = int(watchlist_default_match.group(1))

    account_match = re.search(
        r"// 5초마다 자동 갱신.*?setInterval\(\(\)\s*=>\s*\{.*?\},\s*(\d+)\)",
        html,
        re.S,
    )
    if account_match:
        account_poll_seconds = int(account_match.group(1)) // 1000

    return {
        "uses_websocket": "WebSocket" in html,
        "uses_sse": "EventSource" in html,
        "set_interval_count": html.count("setInterval("),
        "watchlist_timer_tick_seconds": watchlist_timer_tick_seconds,
        "watchlist_default_refresh_seconds": watchlist_default_refresh_seconds,
        "account_poll_seconds": account_poll_seconds,
        "group_scan_endpoint_present": "/api/watchlist/group_scan" in html,
        "watchlist_update_endpoint_present": "/api/watchlist/update" in html,
    }


def inspect_frontend_defaults(html: str) -> dict:
    market_match = re.search(r"\$\('wg-market'\)\.value\s*=\s*'([^']+)'", html)
    preset_match = re.search(r"\$\('wg-preset'\)\.value\s*=\s*'([^']+)'", html)
    valid_markets = sorted(set(re.findall(r'<option value="(KOSPI|KOSDAQ|NASDAQ)">', html)))
    valid_presets = sorted(set(re.findall(r'<option value="(256|BABGURU|GONGGURI|ODOL)">', html)))
    reset_group_market_default = market_match.group(1) if market_match else None
    reset_group_preset_default = preset_match.group(1) if preset_match else None
    return {
        "reset_group_market_default": reset_group_market_default,
        "reset_group_preset_default": reset_group_preset_default,
        "valid_markets": valid_markets,
        "valid_group_presets": valid_presets,
        "reset_group_market_is_valid": reset_group_market_default in valid_markets,
        "reset_group_preset_is_valid": reset_group_preset_default in valid_presets,
    }


def inspect_dom_bindings(html: str) -> dict:
    dom_ids = set(re.findall(r'id="([^"]+)"', html))
    referenced_ids = set(re.findall(r"\$\('([^']+)'\)", html))
    missing_ids = sorted(referenced_ids - dom_ids)
    return {
        "missing_dom_ids": missing_ids,
        "missing_dom_id_count": len(missing_ids),
    }


def run_basic_scan_checks(base_url: str) -> list[dict]:
    results = []
    for market in SCAN_MARKETS:
        started = time.time()
        query = urlencode({"period": 20, "pct": 5, "limit": 10, "signal": "BUY"})
        payload = fetch_json(f"{base_url}/api/scan/{market}?{query}")
        results.append(
            {
                "market": market,
                "elapsed_sec": round(time.time() - started, 2),
                "scanned": payload.get("scanned"),
                "result_count": len(payload.get("stocks", [])),
                "sample_tickers": [stock.get("display_ticker") for stock in payload.get("stocks", [])[:5]],
            }
        )
    return results


def run_group_checks(base_url: str, groups: list[dict]) -> list[dict]:
    results = []
    for group in groups:
        started = time.time()
        payload = {
            "market": group["market"],
            "preset": group["preset"],
            "signal_mode": group["preset"],
            "limit": group["limit"],
            "period": group["period"],
            "pct": group["pct"],
        }
        response = fetch_json(f"{base_url}/api/watchlist/group_scan", payload=payload)
        results.append(
            {
                "name": group["name"],
                "market": group["market"],
                "preset": group["preset"],
                "elapsed_sec": round(time.time() - started, 2),
                "scanned": response.get("scanned"),
                "matched": response.get("matched"),
                "updated_at": response.get("updated_at"),
                "sample_tickers": [stock.get("display_ticker") for stock in response.get("stocks", [])[:5]],
            }
        )
    return results


def run_group_matrix(base_url: str) -> list[dict]:
    rows = []
    for market in MATRIX_MARKETS:
        for preset in PRESETS:
            started = time.time()
            payload = {
                "market": market,
                "preset": preset,
                "signal_mode": preset,
                "limit": 8,
                "period": 20,
                "pct": 5,
            }
            try:
                response = fetch_json(f"{base_url}/api/watchlist/group_scan", payload=payload)
                rows.append(
                    {
                        "market": market,
                        "preset": preset,
                        "elapsed_sec": round(time.time() - started, 2),
                        "scanned": response.get("scanned"),
                        "matched": response.get("matched"),
                        "error": None,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                rows.append(
                    {
                        "market": market,
                        "preset": preset,
                        "elapsed_sec": round(time.time() - started, 2),
                        "scanned": None,
                        "matched": None,
                        "error": str(exc),
                    }
                )
    return rows


def summarize_matrix(rows: list[dict]) -> dict:
    zero_rows = [row for row in rows if row.get("matched") == 0]
    nonzero_rows = [row for row in rows if row.get("matched") not in (None, 0)]
    error_rows = [row for row in rows if row.get("error")]
    return {
        "checked": len(rows),
        "zero_matches": len(zero_rows),
        "nonzero_matches": len(nonzero_rows),
        "error_count": len(error_rows),
        "nonzero_cases": nonzero_rows[:10],
        "error_cases": error_rows[:10],
    }


def build_hotfix_patch(html: str) -> str:
    patched = html
    patched = patched.replace("$('wg-market').value = 'US';", "$('wg-market').value = 'KOSPI';")
    patched = patched.replace("$('wg-preset').value = 'BUY_SETUP';", "$('wg-preset').value = '256';")
    patched = patched.replace("$('wg-period').value", "$('wl-period').value")
    patched = patched.replace("$('wg-pct').value", "$('wl-pct').value")
    return patched


def export_hotfix_html(base_url: str, output_path: str) -> str:
    html = fetch_text(base_url)
    patched = build_hotfix_patch(html)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(patched)
    return output_path


def run_realtime_probe(html: str) -> dict:
    return {
        "supports_native_realtime": ("WebSocket" in html) or ("EventSource" in html),
        "recommended_transport": "sse" if "/api/watchlist/update" in html else "http-polling",
    }


def build_summary(
    runtime: dict,
    defaults: dict,
    scans: list[dict],
    groups: list[dict],
    dom_bindings: dict,
    realtime_probe: dict,
) -> dict:
    group_zero_count = sum(1 for group in groups if not group.get("matched"))
    scan_has_results = all(item.get("result_count", 0) > 0 for item in scans)
    conclusions = []

    if runtime["uses_websocket"] or runtime["uses_sse"]:
        conclusions.append("실시간 스트리밍 연결이 감지되었습니다.")
    else:
        conclusions.append("브라우저는 WebSocket/SSE가 아니라 HTTP polling(setInterval + fetch) 방식으로 동작합니다.")

    if runtime.get("watchlist_default_refresh_seconds"):
        conclusions.append(
            f"감시 종목은 기본 {runtime['watchlist_default_refresh_seconds']}초 주기의 polling이며, 내부 카운트다운 타이머는 {runtime['watchlist_timer_tick_seconds']}초마다 동작합니다."
        )
    if runtime.get("account_poll_seconds"):
        conclusions.append(f"계좌 탭 자동 갱신 주기는 {runtime['account_poll_seconds']}초입니다.")

    if scan_has_results:
        conclusions.append("기본 스캔 API(/api/scan)는 실제로 종목을 반환하고 있습니다.")
    else:
        conclusions.append("기본 스캔 API 일부가 종목을 반환하지 않습니다.")

    if group_zero_count == len(groups):
        conclusions.append("검색 그룹 프리셋 API(/api/watchlist/group_scan)는 점검한 모든 기본 프리셋에서 0건을 반환했습니다.")
    elif group_zero_count:
        conclusions.append(f"검색 그룹 프리셋 중 {group_zero_count}개가 0건을 반환했습니다.")
    else:
        conclusions.append("검색 그룹 프리셋도 정상적으로 종목을 반환했습니다.")

    if dom_bindings.get("missing_dom_id_count"):
        conclusions.append(
            f"스크립트가 참조하지만 DOM에 없는 ID가 {dom_bindings['missing_dom_id_count']}개 있습니다: {', '.join(dom_bindings['missing_dom_ids'])}."
        )

    if not defaults.get("reset_group_market_is_valid"):
        conclusions.append(
            "프론트엔드 resetGroupForm() 기본값이 실제 옵션에 없는 market(US)으로 설정되어 있어 그룹 폼 초기화 UX에 버그가 있습니다."
        )

    if not defaults.get("reset_group_preset_is_valid"):
        conclusions.append(
            "프론트엔드 resetGroupForm() 기본값이 실제 옵션에 없는 preset(BUY_SETUP)으로 설정되어 있어 그룹 폼 초기화 UX에 버그가 있습니다."
        )

    return {
        "runtime": runtime,
        "frontend_defaults": defaults,
        "dom_bindings": dom_bindings,
        "basic_scans": scans,
        "group_scans": groups,
        "realtime_probe": realtime_probe,
        "conclusions": conclusions,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe the deployed dashboard and summarize polling/search behavior.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Dashboard base URL")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    parser.add_argument("--matrix", action="store_true", help="Run preset/market matrix checks")
    parser.add_argument("--export-hotfix-html", metavar="PATH", help="Write a patched HTML snapshot to PATH")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    html = fetch_text(args.base_url)

    if args.export_hotfix_html:
        export_hotfix_html(args.base_url, args.export_hotfix_html)

    runtime = detect_runtime_mode(html)
    defaults = inspect_frontend_defaults(html)
    dom_bindings = inspect_dom_bindings(html)
    scans = run_basic_scan_checks(args.base_url)
    groups = run_group_checks(args.base_url, DEFAULT_GROUPS)
    realtime_probe = run_realtime_probe(html)
    report = build_summary(runtime, defaults, scans, groups, dom_bindings, realtime_probe)

    if args.matrix:
        matrix_rows = run_group_matrix(args.base_url)
        report["group_matrix"] = summarize_matrix(matrix_rows)
        report["group_matrix_rows"] = matrix_rows

    if args.pretty:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
