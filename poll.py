"""
목포 지역(zscode=12110) 전기차 충전기 상태를 주기적으로 수집해 data/status_log.csv에 누적 기록한다.

- 환경공단 EvCharger Open API의 getChargerStatus를 호출한다.
- zscode=12110으로 서버 단에서 목포 지역만 필터링해 받아온다 (공식 활용가이드 기준).
- period 파라미터로 "최근 N분 내 상태가 바뀐 건"만 받아와 API 호출량을 줄인다
  (전국 전체를 매번 받으면 하루 호출 한도 1,000회를 초과하기 때문).
"""

import csv
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API_URL = "https://apis.data.go.kr/B552584/EvCharger/getChargerStatus"
MOKPO_ZSCODE = "12110"
PERIOD_MINUTES = 10  # 공식 최대값. 스케줄러 지연을 감안해 폴링 주기(5분)보다 여유있게 잡음
NUM_OF_ROWS = 9999
MAX_PAGES = 5  # 목포로 이미 좁혀졌으니 사실상 1페이지면 충분하지만 안전장치로 둠
MAX_RETRIES = 3  # 일시적 네트워크 타임아웃 등으로 폴링 한 번을 통째로 날리지 않기 위한 재시도 횟수
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
STATUS_LOG_PATH = BASE_DIR / "data" / "status_log.csv"

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
            "zscode": MOKPO_ZSCODE,
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
        if isinstance(page_items, dict):  # 결과 1건일 때 리스트가 아니라 dict로 오는 경우 방어
            page_items = [page_items]
        if not page_items:
            break

        items.extend(page_items)

        total_count = data.get("totalCount")
        if total_count is None or len(items) >= int(total_count):
            break
        page_no += 1

    return items


def load_existing_keys() -> set[tuple[str, str, str]]:
    """이미 기록된 (statId, chgerId, statUpdDt) 조합을 읽어온다. 같은 상태 변경 이벤트를
    period 조회 구간이 겹쳐서 여러 번 다시 받아오더라도 중복 기록하지 않기 위함."""
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
    print(f"목포(zscode={MOKPO_ZSCODE}) 상태 변경 {len(items)}건 수신")

    if items:
        written = append_status_log(items)
        skipped = len(items) - written
        print(f"{STATUS_LOG_PATH}에 {written}건 신규 기록 (중복 {skipped}건 건너뜀)")
    else:
        print("이번 주기에는 목포 지역 상태 변경 없음 (정상 상황일 수 있음)")


if __name__ == "__main__":
    main()
