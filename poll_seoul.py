"""
서울 지역(zcode=11) 전기차 충전기 상태를 10분 주기로 수집해 Supabase(charger_status_logs)에 저장한다.

poll.py(목포, 5분 주기)와 거의 동일한 구조이며 다른 점만 정리하면:
- zscode(목포 상세코드) 대신 zcode(서울 시도코드)를 씀
- 폴링 주기가 5분이 아니라 10분 (period 최댓값과 맞춤, 구멍 없이 이어지도록)
- 서울은 충전기 수가 목포보다 훨씬 많아(약 75,598건) 한 번에 여러 페이지가 나올 수 있어
  MAX_PAGES를 넉넉히 잡음
- 목적: 2023~2025년 서울시 공개 이력 데이터로 학습한 혼잡도 예측 모델을,
  지금부터 쌓이는 2026년 실측 데이터로 검증(테스트)하기 위한 것. 목포처럼
  실서비스 지역이 아니라서 5분보다 느슨한 10분 주기로도 충분하다.
- 어느 charger_id에 연결할지는 stations/chargers 테이블에서 찾는다 — migrate_master_data.py로
  마스터 목록이 먼저 채워져 있어야 한다.
"""

import os
import sys
import time
from urllib.parse import unquote

import psycopg2.extras
import requests

from db import get_connection, load_charger_id_map, map_status, parse_api_datetime

API_URL = "https://apis.data.go.kr/B552584/EvCharger/getChargerStatus"
SEOUL_ZCODE = "11"
REGION_CODE = "SEOUL"  # migrate_master_data.py가 stations.region_code에 넣는 값과 반드시 같아야 함
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


def save_status_items(conn, items: list[dict]) -> tuple[int, int, int]:
    """반환: (신규 기록, 중복이라 건너뜀, charger 매칭 안 돼서 건너뜀)"""
    charger_map = load_charger_id_map(conn, REGION_CODE)

    rows = []
    unmatched = 0
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
    print(f"서울(zcode={SEOUL_ZCODE}) 상태 변경 {len(items)}건 수신")

    if not items:
        print("이번 주기에는 서울 지역 상태 변경 없음 (정상 상황일 수 있음)")
        return

    conn = get_connection()
    try:
        inserted, duplicate, unmatched = save_status_items(conn, items)
    finally:
        conn.close()

    print(
        f"Supabase charger_status_logs에 {inserted}건 신규 기록 "
        f"(중복 {duplicate}건, charger 매칭 실패 {unmatched}건 건너뜀)"
    )


if __name__ == "__main__":
    main()
