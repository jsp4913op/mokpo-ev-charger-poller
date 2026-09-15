"""
getChargerInfo(충전소 정보 조회)를 zcode=11(서울특별시)으로 호출해
data/seoul_chargers.csv에 서울 지역 충전소 마스터 목록(위치 포함)을 생성한다.

- fetch_mokpo_chargers.py와 동일한 방식이며, 필터만 zscode(목포 상세코드) 대신
  zcode(서울 시도코드)를 쓴다 — 서울은 특별시라 시군구 단위 상세코드 없이
  시도코드만으로 전체를 커버한다.
- 정적 정보라 필요할 때(최초 1회, 또는 갱신 시)만 수동 실행한다.
- 목포 데이터(mokpo_chargers.csv, poll.py)는 이 스크립트와 무관하게 그대로 유지된다.
"""

import csv
import os
import sys
import time
from pathlib import Path

import requests

API_URL = "https://apis.data.go.kr/B552584/EvCharger/getChargerInfo"
SEOUL_ZCODE = "11"
NUM_OF_ROWS = 9999
MAX_PAGES = 10  # 서울 전체 규모를 감안해 목포보다 여유있게 잡음 (필요시 늘릴 것)
MAX_RETRIES = 4
RETRY_BACKOFF_SECONDS = 5


def request_with_retry(url: str, params: dict) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return requests.get(url, params=params, timeout=60)
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as e:
            last_error = e
            print(f"[경고] 연결 실패 (시도 {attempt}/{MAX_RETRIES}): {e}", file=sys.stderr)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS)
    raise last_error

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = BASE_DIR / "data" / "seoul_chargers.csv"

FIELDNAMES = [
    "statId", "chgerId", "statNm", "addr", "addrDetail",
    "lat", "lng", "useTime", "busiNm", "chgerType", "output", "stat",
]


def fetch_all_items(service_key: str) -> list[dict]:
    items: list[dict] = []
    page_no = 1

    while page_no <= MAX_PAGES:
        params = {
            "serviceKey": service_key,
            "pageNo": page_no,
            "numOfRows": NUM_OF_ROWS,
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
        print(f"page {page_no}: 누적 {len(items)}건" + (f" / 전체 {total_count}건" if total_count else ""))

        if total_count is None or len(items) >= int(total_count):
            break
        page_no += 1

        if page_no > MAX_PAGES:
            print(f"[경고] MAX_PAGES({MAX_PAGES})에 도달했지만 전체 {total_count}건 중 {len(items)}건만 받음. "
                  "MAX_PAGES를 늘려서 다시 실행하세요.", file=sys.stderr)

    return items


def write_csv(rows: list[dict]) -> None:
    if not rows:
        print("[경고] 서울로 매칭된 행이 없습니다. zcode 값을 확인하세요.", file=sys.stderr)
        return

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in FIELDNAMES})

    print(f"{OUTPUT_PATH}에 {len(rows)}건 저장 완료")


def main() -> None:
    service_key = (os.environ.get("DATA_GO_KR_SERVICE_KEY") or "").strip()
    if not service_key:
        print("[오류] 환경변수 DATA_GO_KR_SERVICE_KEY가 설정되어 있지 않습니다.", file=sys.stderr)
        sys.exit(1)

    items = fetch_all_items(service_key)
    print(f"서울(zcode={SEOUL_ZCODE}) 충전기 {len(items)}건 수신")

    write_csv(items)


if __name__ == "__main__":
    main()
