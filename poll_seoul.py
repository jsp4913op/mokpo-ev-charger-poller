"""
서울 지역(zcode=11) 전기차 충전기 상태를 10분 주기로 수집해 data/seoul_status_log.csv에 누적 기록한다.

poll.py(목포, 5분 주기)와 거의 동일한 구조이며 다른 점만 정리하면:
- zscode(목포 상세코드) 대신 zcode(서울 시도코드)를 씀
- 폴링 주기가 5분이 아니라 10분 (period 최댓값과 맞춤, 구멍 없이 이어지도록)
- 서울은 충전기 수가 목포보다 훨씬 많아(약 75,598건) 한 번에 여러 페이지가 나올 수 있어
  MAX_PAGES를 넉넉히 잡음
- 목적: 2023~2025년 서울시 공개 이력 데이터로 학습한 혼잡도 예측 모델을,
  지금부터 쌓이는 2026년 실측 데이터로 검증(테스트)하기 위한 것. 목포처럼
  실서비스 지역이 아니라서 5분보다 느슨한 10분 주기로도 충분하다.
"""

import csv
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API_URL = "https://apis.data.go.kr/B552584/EvCharger/getChargerStatus"
SEOUL_ZCODE = "11"
PERIOD_MINUTES = 10  # 공식 최대값. 폴링 주기(10분)와 맞춰서 구멍 없이 이어지게 함
NUM_OF_ROWS = 9999
MAX_PAGES = 20  # 서울은 충전기 수가 많아 목포보다 여유있게 잡음
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5


def request_with_retry(url: str, params: dict) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return requests.get(url, params=params, timeout=30)
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as e:
            last_error = e
            print(f"[경고] 연결 실패 (시도 {attempt}/{MAX_RETRIES}): {e}", file=sys.stderr)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS)
    raise last_error


BASE_DIR = Path(__file__).resolve().parent
STATUS_LOG_PATH = BASE_DIR / "data" / "seoul_status_log.csv"

FIELDNAMES = [
    "fetched_at", "statId", "chgerId", "stat", "statUpdDt",
    "lastTsdt", "lastTedt", "nowTsdt", "busiId",
]


def fetch_status_items(service_key: str) -> list[dict]:
    items: list[dict] = []
    page_no = 1

    while page_no <= MAX_PAGES:
        params = {
            "serviceKey": service_key,
            "pageNo": page_no,
            "numOfRows": NUM_OF_ROWS,
            "period": PERIOD_MINUTES,
            "zcode": SEOUL_ZCODE,
            "dataType": "JSON",
        }
        resp = request_with_retry(API_URL, params)
        if not resp.ok:
            print(f"[오류 응답 본문]\n{resp.text}", file=sys.stderr)
        resp.raise_for_status()

        data = resp.json()

        result_code = data.get("resultCode")
        result_msg = data.get("resultMsg")
        if result_code not in (None, "00"):
            raise RuntimeError(f"API 오류: resultCode={result_code}, resultMsg={result_msg}")

        page_items = (data.get("items") or {}).get("item") or []
        if isinstance(page_items, dict):
            page_items = [page_items]
        if not page_items:
            break

        items.extend(page_items)

        total_count = data.get("totalCount")
        if total_count is None or len(items) >= int(total_count):
            break
        page_no += 1

        if page_no > MAX_PAGES:
            print(f"[경고] MAX_PAGES({MAX_PAGES})에 도달했지만 전체 {total_count}건 중 "
                  f"{len(items)}건만 받음. 일일 호출 한도를 감안해 MAX_PAGES 조정 필요.", file=sys.stderr)

    return items


def load_existing_keys() -> set[tuple[str, str, str]]:
    if not STATUS_LOG_PATH.exists():
        return set()

    keys = set()
    with open(STATUS_LOG_PATH, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            keys.add((row.get("statId", ""), row.get("chgerId", ""), row.get("statUpdDt", "")))
    return keys


def append_status_log(rows: list[dict]) -> int:
    STATUS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_exists = STATUS_LOG_PATH.exists()
    existing_keys = load_existing_keys()

    fetched_at = datetime.now(timezone.utc).isoformat()
    written = 0

    with open(STATUS_LOG_PATH, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        for row in rows:
            key = (row.get("statId", ""), row.get("chgerId", ""), row.get("statUpdDt", ""))
            if key in existing_keys:
                continue
            existing_keys.add(key)
            writer.writerow({
                "fetched_at": fetched_at,
                "statId": row.get("statId", ""),
                "chgerId": row.get("chgerId", ""),
                "stat": row.get("stat", ""),
                "statUpdDt": row.get("statUpdDt", ""),
                "lastTsdt": row.get("lastTsdt", ""),
                "lastTedt": row.get("lastTedt", ""),
                "nowTsdt": row.get("nowTsdt", ""),
                "busiId": row.get("busiId", ""),
            })
            written += 1

    return written


def main() -> None:
    service_key = (os.environ.get("DATA_GO_KR_SERVICE_KEY") or "").strip()
    if not service_key:
        print("[오류] 환경변수 DATA_GO_KR_SERVICE_KEY가 설정되어 있지 않습니다.", file=sys.stderr)
        sys.exit(1)

    items = fetch_status_items(service_key)
    print(f"서울(zcode={SEOUL_ZCODE}) 상태 변경 {len(items)}건 수신")

    if items:
        written = append_status_log(items)
        skipped = len(items) - written
        print(f"{STATUS_LOG_PATH}에 {written}건 신규 기록 (중복 {skipped}건 건너뜀)")
    else:
        print("이번 주기에는 서울 지역 상태 변경 없음 (정상 상황일 수 있음)")


if __name__ == "__main__":
    main()
