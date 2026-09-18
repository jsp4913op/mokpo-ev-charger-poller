"""
목포 지역(zscode=12110) 전기차 충전기 상태를 주기적으로 수집해 Supabase(charger_status_logs)에 저장한다.
DB 용량 초과 등 장애 상황을 대비한 백업으로 data/status_log.csv에도 계속 그대로 남긴다(이중 저장).

- 환경공단 EvCharger Open API의 getChargerStatus를 호출한다.
- zscode=12110으로 서버 단에서 목포 지역만 필터링해 받아온다 (공식 활용가이드 기준).
- period 파라미터로 "최근 N분 내 상태가 바뀐 건"만 받아와 API 호출량을 줄인다
  (전국 전체를 매번 받으면 하루 호출 한도 1,000회를 초과하기 때문).
- 어느 charger_id에 연결할지는 stations/chargers 테이블에서 찾는다 — 즉 이 스크립트를
  돌리기 전에 migrate_master_data.py로 마스터 목록이 먼저 채워져 있어야 한다.
  (charger 매칭에 실패해도 CSV 백업에는 원본 그대로 남는다.)
"""

import csv
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import psycopg2.extras
import requests

from db import get_connection, load_charger_id_map, map_status, parse_api_datetime

BASE_DIR = Path(__file__).resolve().parent
STATUS_LOG_PATH = BASE_DIR / "data" / "status_log.csv"

CSV_FIELDNAMES = [
    "fetched_at", "statId", "chgerId", "stat", "statUpdDt",
    "lastTsdt", "lastTedt", "nowTsdt", "busiId",
]

API_URL = "https://apis.data.go.kr/B552584/EvCharger/getChargerStatus"
MOKPO_ZSCODE = "12110"
REGION_CODE = "MOKPO"  # migrate_master_data.py가 stations.region_code에 넣는 값과 반드시 같아야 함
PERIOD_MINUTES = 10  # 공식 최대값. 스케줄러 지연을 감안해 폴링 주기(5분)보다 여유있게 잡음
NUM_OF_ROWS = 9999
MAX_PAGES = 5  # 목포로 이미 좁혀졌으니 사실상 1페이지면 충분하지만 안전장치로 둠
MAX_RETRIES = 5  # 일시적 네트워크 타임아웃 등으로 폴링 한 번을 통째로 날리지 않기 위한 재시도 횟수
RETRY_BACKOFF_SECONDS = 5
CONNECT_TIMEOUT_SECONDS = 10  # 정상 연결은 보통 1초 내 응답. 30초는 죽은 서버 판별에 과함 -
                              # 줄인 만큼 같은 시간 예산 안에서 재시도를 더 많이 돌린다.


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


def load_existing_csv_keys() -> set[tuple[str, str, str]]:
    """CSV 백업용 중복 방지 — 이미 기록된 (statId, chgerId, statUpdDt) 조합을 읽어온다."""
    if not STATUS_LOG_PATH.exists():
        return set()
    keys = set()
    with open(STATUS_LOG_PATH, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            keys.add((row.get("statId", ""), row.get("chgerId", ""), row.get("statUpdDt", "")))
    return keys


def append_status_log_csv(items: list[dict]) -> int:
    """DB 장애/용량 초과 대비 백업. charger 매칭 여부와 무관하게 원본 그대로 남긴다."""
    STATUS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_exists = STATUS_LOG_PATH.exists()
    existing_keys = load_existing_csv_keys()

    fetched_at = datetime.now(timezone.utc).isoformat()
    written = 0

    with open(STATUS_LOG_PATH, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        for item in items:
            key = (item.get("statId", ""), item.get("chgerId", ""), item.get("statUpdDt", ""))
            if key in existing_keys:
                continue
            existing_keys.add(key)
            writer.writerow({
                "fetched_at": fetched_at,
                "statId": item.get("statId", ""),
                "chgerId": item.get("chgerId", ""),
                "stat": item.get("stat", ""),
                "statUpdDt": item.get("statUpdDt", ""),
                "lastTsdt": item.get("lastTsdt", ""),
                "lastTedt": item.get("lastTedt", ""),
                "nowTsdt": item.get("nowTsdt", ""),
                "busiId": item.get("busiId", ""),
            })
            written += 1

    return written


def save_status_items(conn, items: list[dict]) -> tuple[int, int, int]:
    """반환: (신규 기록, 중복이라 건너뜀, charger 매칭 안 돼서 건너뜀)"""
    charger_map = load_charger_id_map(conn, REGION_CODE)

    rows = []
    unmatched = 0
    # statUpdDt 오름차순으로 처리해서, 같은 충전기가 이번 배치에 여러 번 나와도
    # chargers 테이블에는 가장 최신 상태가 남도록 함.
    for item in sorted(items, key=lambda i: i.get("statUpdDt") or ""):
        key = (item.get("statId", ""), item.get("chgerId", ""))
        charger_id = charger_map.get(key)
        if charger_id is None:
            unmatched += 1
            continue
        observed_at = parse_api_datetime(item.get("statUpdDt"))
        if observed_at is None:
            unmatched += 1
            continue
        rows.append((
            charger_id,
            map_status(item.get("stat")),
            item.get("stat", ""),
            observed_at,
            parse_api_datetime(item.get("lastTsdt")),
            parse_api_datetime(item.get("lastTedt")),
            parse_api_datetime(item.get("nowTsdt")),
        ))

    if not rows:
        return 0, 0, unmatched

    with conn.cursor() as cur:
        # fetch=True면 execute_values가 RETURNING 결과를 함수 반환값으로 직접 준다
        # (cur.fetchall()로 따로 받는 게 아님. 페이지=1000건 단위로 나눠 실행돼도 전체가 다 모인다).
        returned = psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO charger_status_logs
                (charger_id, status, source_status_code, observed_at, last_tsdt, last_tedt, now_tsdt)
            VALUES %s
            ON CONFLICT (charger_id, observed_at) DO NOTHING
            RETURNING id
            """,
            rows,
            template="(%s, %s::charger_status, %s, %s, %s, %s, %s)",
            fetch=True,
            page_size=1000,
        )
        inserted = len(returned)
        duplicate = len(rows) - inserted

        # chargers 테이블은 "현재 상태"를 들고 있으므로 최신 값으로 갱신한다.
        # 이미 더 최신(status_updated_at이 더 늦은) 값이 들어있으면 덮어쓰지 않는다(방어).
        psycopg2.extras.execute_values(
            cur,
            """
            UPDATE chargers AS c SET
                status = v.status::charger_status,
                source_status_code = v.source_status_code,
                status_updated_at = v.observed_at,
                updated_at = now()
            FROM (VALUES %s) AS v(charger_id, status, source_status_code, observed_at)
            WHERE c.id = v.charger_id
              AND (c.status_updated_at IS NULL OR c.status_updated_at <= v.observed_at)
            """,
            [(r[0], r[1], r[2], r[3]) for r in rows],
            # VALUES절 리터럴은 기본 text로 추론되어 uuid/timestamptz와 비교 시 타입 에러가 나므로 명시 캐스팅
            template="(%s::uuid, %s, %s, %s::timestamptz)",
        )

    conn.commit()
    return inserted, duplicate, unmatched


def main() -> None:
    # data.go.kr이 주는 인증키가 이미 URL-encoding된 형태(%3D%3D 등)일 수 있는데, requests의
    # params=에 그대로 넣으면 한 번 더 인코딩되어(%253D%253D) 403이 난다. 미리 decode해두면
    # 인코딩된 키/원본 키 둘 다 안전하게 동작한다.
    service_key = unquote((os.environ.get("DATA_GO_KR_SERVICE_KEY") or "").strip())
    if not service_key:
        print("[오류] 환경변수 DATA_GO_KR_SERVICE_KEY가 설정되어 있지 않습니다.", file=sys.stderr)
        sys.exit(1)

    items = fetch_status_items(service_key)
    print(f"목포(zscode={MOKPO_ZSCODE}) 상태 변경 {len(items)}건 수신")

    if not items:
        print("이번 주기에는 목포 지역 상태 변경 없음 (정상 상황일 수 있음)")
        return

    # CSV 백업을 먼저 남긴다 — DB 용량 초과/장애로 아래 Supabase 저장이 실패해도
    # 원본 데이터는 여기 그대로 남도록.
    csv_written = append_status_log_csv(items)
    print(f"{STATUS_LOG_PATH}에 {csv_written}건 백업 기록 (중복 {len(items) - csv_written}건 제외)")

    try:
        conn = get_connection()
        try:
            inserted, duplicate, unmatched = save_status_items(conn, items)
        finally:
            conn.close()
        print(
            f"Supabase charger_status_logs에 {inserted}건 신규 기록 "
            f"(중복 {duplicate}건, charger 매칭 실패 {unmatched}건 건너뜀)"
        )
    except Exception as e:
        # DB가 꽉 찼거나 접속이 안 되는 등 문제가 있어도, 위 CSV 백업은 이미 끝난 상태라
        # 데이터 자체는 안전하다 — 여기서는 경고만 남기고 워크플로우가 CSV는 커밋하게 둔다.
        print(f"[경고] Supabase 저장 실패 (CSV 백업은 완료됨): {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
