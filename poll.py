"""
목포 지역 전기차 충전기 상태를 주기적으로 수집해 data/status_log.csv에 누적 기록한다.

- 환경공단 EvCharger Open API의 getChargerStatus를 호출한다.
- period 파라미터로 "최근 N분 내 상태가 바뀐 건"만 받아와 API 호출량을 줄인다
  (전국 전체를 매번 받으면 하루 호출 한도를 초과하기 때문).
- 응답 중 data/mokpo_chargers.csv에 등록된 (statId, chgerId)만 걸러서 저장한다.
"""

import csv
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests

API_URL = "https://apis.data.go.kr/B552584/EvCharger/getChargerStatus"
PERIOD_MINUTES = 10  # 스케줄러 지연을 감안해 폴링 주기(5분)보다 여유있게 잡음
NUM_OF_ROWS = 9999
MAX_PAGES = 5  # 혹시 변경건이 몰려도 과도한 호출을 막기 위한 안전장치

BASE_DIR = Path(__file__).resolve().parent
CHARGERS_REF_PATH = BASE_DIR / "data" / "mokpo_chargers.csv"
STATUS_LOG_PATH = BASE_DIR / "data" / "status_log.csv"


def load_mokpo_charger_ids() -> set[tuple[str, str]]:
    """data/mokpo_chargers.csv에서 (statId, chgerId) 목록을 읽는다."""
    if not CHARGERS_REF_PATH.exists():
        print(f"[경고] {CHARGERS_REF_PATH} 파일이 없습니다.", file=sys.stderr)
        return set()

    ids = set()
    with open(CHARGERS_REF_PATH, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stat_id = (row.get("statId") or "").strip()
            chger_id = (row.get("chgerId") or "").strip()
            if stat_id and chger_id:
                ids.add((stat_id, chger_id))
    return ids


def fetch_status_items(service_key: str) -> list[dict]:
    """getChargerStatus를 호출해 item 목록을 반환한다 (필요시 페이지네이션)."""
    items: list[dict] = []
    page_no = 1

    while page_no <= MAX_PAGES:
        params = {
            "serviceKey": service_key,
            "pageNo": page_no,
            "numOfRows": NUM_OF_ROWS,
            "period": PERIOD_MINUTES,
        }
        resp = requests.get(API_URL, params=params, timeout=30)
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
            items.append({
                "statId": (item.findtext("statId") or "").strip(),
                "chgerId": (item.findtext("chgerId") or "").strip(),
                "stat": (item.findtext("stat") or "").strip(),
                "statUpdDt": (item.findtext("statUpdDt") or "").strip(),
            })

        total_count = root.findtext(".//totalCount")
        try:
            total_count = int(total_count)
        except (TypeError, ValueError):
            total_count = None

        if total_count is None or len(items) >= total_count:
            break
        page_no += 1

    return items


def append_status_log(rows: list[dict]) -> None:
    STATUS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_exists = STATUS_LOG_PATH.exists()

    fetched_at = datetime.now(timezone.utc).isoformat()

    with open(STATUS_LOG_PATH, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["fetched_at", "statId", "chgerId", "stat", "statUpdDt"])
        if not file_exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({"fetched_at": fetched_at, **row})


def main() -> None:
    service_key = os.environ.get("DATA_GO_KR_SERVICE_KEY")
    if not service_key:
        print("[오류] 환경변수 DATA_GO_KR_SERVICE_KEY가 설정되어 있지 않습니다.", file=sys.stderr)
        sys.exit(1)

    mokpo_ids = load_mokpo_charger_ids()
    if not mokpo_ids:
        print("[경고] 목포 충전기 참조 목록이 비어 있어 매칭되는 데이터가 없습니다.", file=sys.stderr)

    items = fetch_status_items(service_key)
    matched = [item for item in items if (item["statId"], item["chgerId"]) in mokpo_ids]

    print(f"전체 변경건 {len(items)}개 중 목포 매칭 {len(matched)}개")

    if matched:
        append_status_log(matched)
        print(f"{STATUS_LOG_PATH}에 {len(matched)}건 기록 완료")
    else:
        print("이번 주기에는 목포 지역 상태 변경 없음 (정상 상황일 수 있음)")


if __name__ == "__main__":
    main()
