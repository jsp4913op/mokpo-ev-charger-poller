"""
getChargerInfo(충전소 정보 조회)를 zscode=12110(목포시)으로 호출해
data/mokpo_chargers.csv에 목포 지역 충전소 마스터 목록(위치 포함)을 생성한다.

- poll.py(getChargerStatus)와 달리 이건 정적 정보라 필요할 때(최초 1회, 또는 갱신 시)만 수동 실행한다.
- 위도/경도까지 받아오므로, 이후 경로 추천 기능에서도 재사용 가능하다.
"""

import csv
import os
import sys
from pathlib import Path

import requests

API_URL = "https://apis.data.go.kr/B552584/EvCharger/getChargerInfo"
MOKPO_ZSCODE = "12110"
NUM_OF_ROWS = 9999
MAX_PAGES = 10  # 목포로 좁혀졌으니 1페이지면 충분하지만 안전장치로 둠

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = BASE_DIR / "data" / "mokpo_chargers.csv"

FIELDNAMES = [
    "statId", "chgerId", "statNm", "addr", "addrDetail",
    "lat", "lng", "useTime", "busiNm", "chgerType", "stat",
]


def fetch_all_items(service_key: str) -> list[dict]:
    items: list[dict] = []
    page_no = 1

    while page_no <= MAX_PAGES:
        params = {
            "serviceKey": service_key,
            "pageNo": page_no,
            "numOfRows": NUM_OF_ROWS,
            "zscode": MOKPO_ZSCODE,
            "dataType": "JSON",
        }
        resp = requests.get(API_URL, params=params, timeout=60)
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

    return items


def write_csv(rows: list[dict]) -> None:
    if not rows:
        print("[경고] 목포로 매칭된 행이 없습니다. zscode 값을 확인하세요.", file=sys.stderr)
        return

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in FIELDNAMES})

    print(f"{OUTPUT_PATH}에 {len(rows)}건 저장 완료")


def main() -> None:
    service_key = os.environ.get("DATA_GO_KR_SERVICE_KEY")
    if not service_key:
        print("[오류] 환경변수 DATA_GO_KR_SERVICE_KEY가 설정되어 있지 않습니다.", file=sys.stderr)
        sys.exit(1)

    items = fetch_all_items(service_key)
    print(f"목포(zscode={MOKPO_ZSCODE}) 충전기 {len(items)}건 수신")

    write_csv(items)


if __name__ == "__main__":
    main()
