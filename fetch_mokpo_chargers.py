"""
getChargerInfo(충전소 정보 조회)를 전국 대상으로 페이지네이션 호출한 뒤,
주소에 "목포"가 포함된 충전소만 걸러 data/mokpo_chargers.csv를 생성한다.

- 매 5분 도는 poll.py와 달리 이 스크립트는 필요할 때(최초 1회, 또는 갱신 시)만 수동 실행한다.
- getChargerStatus와 달리 getChargerInfo는 정적 정보라 period 파라미터가 없다.
- 응답 필드명을 미리 단정하지 않고, item의 모든 자식 태그를 그대로 딕셔너리로 수집한 뒤
  값 중 "목포"가 포함된 행만 남긴다 (필드명이 addr인지 location인지 등을 API가 명확히
  공개하지 않아, 값 기준으로 안전하게 필터링).
"""

import csv
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

API_URL = "https://apis.data.go.kr/B552584/EvCharger/getChargerInfo"
NUM_OF_ROWS = 9999
MAX_PAGES = 60  # 전국 데이터 대응 (1회성 실행이라 넉넉히 잡음)

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = BASE_DIR / "data" / "mokpo_chargers.csv"


def fetch_all_items(service_key: str) -> list[dict]:
    items: list[dict] = []
    page_no = 1

    while page_no <= MAX_PAGES:
        params = {
            "serviceKey": service_key,
            "pageNo": page_no,
            "numOfRows": NUM_OF_ROWS,
        }
        resp = requests.get(API_URL, params=params, timeout=60)
        if not resp.ok:
            print(f"[오류 응답 본문]\n{resp.text}", file=sys.stderr)
        resp.raise_for_status()

        root = ET.fromstring(resp.text)

        result_code = root.findtext(".//resultCode")
        result_msg = root.findtext(".//resultMsg")
        if result_code not in (None, "00"):
            raise RuntimeError(f"API 오류: resultCode={result_code}, resultMsg={result_msg}")

        page_items = root.findall(".//item")
        if not page_items:
            break

        for item in page_items:
            items.append({child.tag: (child.text or "").strip() for child in item})

        total_count = root.findtext(".//totalCount")
        try:
            total_count = int(total_count)
        except (TypeError, ValueError):
            total_count = None

        print(f"page {page_no}: 누적 {len(items)}건" + (f" / 전체 {total_count}건" if total_count else ""))

        if total_count is None or len(items) >= total_count:
            break
        page_no += 1

    return items


def filter_mokpo(items: list[dict]) -> list[dict]:
    matched = []
    for item in items:
        if any("목포" in v for v in item.values()):
            matched.append(item)
    return matched


def write_csv(rows: list[dict]) -> None:
    if not rows:
        print("[경고] 목포로 매칭된 행이 없습니다. 필터링 로직을 확인하세요.", file=sys.stderr)
        return

    # statId, chgerId를 앞에 오도록 컬럼 순서 정리 (poll.py가 이 두 컬럼을 사용)
    all_keys = []
    for row in rows:
        for k in row.keys():
            if k not in all_keys:
                all_keys.append(k)
    priority = [k for k in ("statId", "chgerId") if k in all_keys]
    rest = [k for k in all_keys if k not in priority]
    fieldnames = priority + rest

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"{OUTPUT_PATH}에 {len(rows)}건 저장 완료")


def main() -> None:
    service_key = os.environ.get("DATA_GO_KR_SERVICE_KEY")
    if not service_key:
        print("[오류] 환경변수 DATA_GO_KR_SERVICE_KEY가 설정되어 있지 않습니다.", file=sys.stderr)
        sys.exit(1)

    items = fetch_all_items(service_key)
    print(f"전국 총 {len(items)}건 수신")

    mokpo_items = filter_mokpo(items)
    print(f"목포 매칭 {len(mokpo_items)}건")

    write_csv(mokpo_items)


if __name__ == "__main__":
    main()
