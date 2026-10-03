"""
getChargerInfo(충전소 정보 조회)를 제주시(zscode=50110)와 서귀포시(zscode=50130)로
각각 호출해 data/jeju/chargers.csv에 제주 지역 충전소 마스터 목록(위치 포함)을 생성한다.

fetch_mokpo_chargers.py와 동일한 구조이며, 제주는 시가 두 개(제주시/서귀포시)라
zscode를 두 번 순회해서 합친다는 점만 다르다.

- poll.py(getChargerStatus)와 달리 이건 정적 정보라 필요할 때(최초 1회, 또는 갱신 시)만 수동 실행한다.
- 위도/경도까지 받아오므로, 이후 경로 추천 기능에서도 재사용 가능하다.
"""

import csv
import os
import sys
import time
from pathlib import Path
from urllib.parse import unquote

import requests

API_URL = "https://apis.data.go.kr/B552584/EvCharger/getChargerInfo"
JEJU_ZSCODES = {"제주시": "50110", "서귀포시": "50130"}
NUM_OF_ROWS = 9999
MAX_PAGES = 10  # 시 단위로 좁혔으니 1~2페이지면 충분하지만 안전장치로 둠
MAX_RETRIES = 6  # 일시적 네트워크 타임아웃 등으로 조회 한 번을 통째로 날리지 않기 위한 재시도 횟수
RETRY_BACKOFF_SECONDS = 5
CONNECT_TIMEOUT_SECONDS = 10  # 정상 연결은 보통 1초 내 응답. 60초는 죽은 서버 판별에 과함 -
                              # 줄인 만큼 같은 시간 예산 안에서 재시도를 더 많이 돌린다.

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = BASE_DIR / "data" / "jeju" / "chargers.csv"

FIELDNAMES = [
    "statId", "chgerId", "statNm", "addr", "addrDetail",
    "lat", "lng", "useTime", "busiId", "busiNm", "chgerType", "output", "stat",
    # 외부인 이용 가능 여부 판단용(이용자 제한 여부/사유, 시설 구분 코드, 안내문)
    "limitYn", "limitDetail", "kind", "kindDetail", "note",
]


def request_with_retry(url: str, params: dict) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return requests.get(url, params=params, timeout=CONNECT_TIMEOUT_SECONDS)
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as e:
            last_error = e
            print(f"[경고] 연결 실패 (시도 {attempt}/{MAX_RETRIES}): {e}", file=sys.stderr)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS)
    raise last_error


def fetch_all_items_for_zscode(service_key: str, zscode: str, label: str) -> list[dict]:
    items: list[dict] = []
    page_no = 1

    while page_no <= MAX_PAGES:
        params = {
            "serviceKey": service_key,
            "pageNo": page_no,
            "numOfRows": NUM_OF_ROWS,
            "zscode": zscode,
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
        print(f"{label} page {page_no}: 누적 {len(items)}건" + (f" / 전체 {total_count}건" if total_count else ""))

        if total_count is None or len(items) >= int(total_count):
            break
        page_no += 1

    return items


def write_csv(rows: list[dict]) -> None:
    if not rows:
        print("[경고] 제주로 매칭된 행이 없습니다. zscode 값을 확인하세요.", file=sys.stderr)
        return

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in FIELDNAMES})

    print(f"{OUTPUT_PATH}에 {len(rows)}건 저장 완료")


def main() -> None:
    # data.go.kr 인증키가 이미 URL-encoding된 형태일 수 있어 미리 decode (이중 인코딩 방지)
    service_key = unquote((os.environ.get("DATA_GO_KR_SERVICE_KEY") or "").strip())
    if not service_key:
        print("[오류] 환경변수 DATA_GO_KR_SERVICE_KEY가 설정되어 있지 않습니다.", file=sys.stderr)
        sys.exit(1)

    all_items: list[dict] = []
    for label, zscode in JEJU_ZSCODES.items():
        items = fetch_all_items_for_zscode(service_key, zscode, label)
        print(f"{label}(zscode={zscode}) 충전기 {len(items)}건 수신")
        all_items.extend(items)

    write_csv(all_items)


if __name__ == "__main__":
    main()
