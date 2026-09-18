"""
Supabase(Postgres) 연결 및 공통 헬퍼.

접속 정보는 환경변수 SUPABASE_DB_URL로 받는다 (Supabase 프로젝트 설정 > Database >
Connection string, "URI" 형식 그대로). 이 값은 절대 코드/커밋에 넣지 말고 로컬 .env,
GitHub Actions Secrets에만 넣을 것.
"""

import os
from datetime import datetime

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

# API의 stat(0~5) -> DB의 charger_status enum 매핑.
# 원본 코드는 charger_status_logs.source_status_code / chargers.source_status_code에 그대로 보존하므로
# 정보 손실은 없음. (2026-09-18 팀 확인: enum에 COMMUNICATION_ERROR, MAINTENANCE 추가 완료)
STATUS_MAP = {
    "0": "UNKNOWN",              # 알수없음
    "1": "COMMUNICATION_ERROR",  # 통신이상
    "2": "AVAILABLE",            # 사용가능
    "3": "CHARGING",             # 충전중
    "4": "OUT_OF_SERVICE",       # 운영중지
    "5": "MAINTENANCE",          # 점검중
}


def get_connection():
    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        raise RuntimeError("환경변수 SUPABASE_DB_URL이 설정되어 있지 않습니다.")
    return psycopg2.connect(db_url)


def map_status(raw_code) -> str:
    return STATUS_MAP.get(str(raw_code), "UNKNOWN")


def parse_api_datetime(value: str | None) -> datetime | None:
    """API가 주는 'YYYYMMDDHHMMSS' 형식 문자열을 datetime으로 변환한다. 빈 값이면 None."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%d%H%M%S")
    except ValueError:
        return None


def load_charger_id_map(conn, region_code: str) -> dict[tuple[str, str], str]:
    """(external_station_id, external_charger_id) -> chargers.id(uuid) 매핑을 한 번에 로드한다.

    poll.py가 매 항목마다 DB 조회하지 않고, 이 지역 전체 매핑을 한 번만 읽어 메모리에서 찾도록 함.
    charger_id가 여기 없으면 아직 마스터 목록에 없는 충전기 -> 그 상태 업데이트는 스킵된다
    (migrate_master_data.py를 먼저/주기적으로 돌려서 마스터 목록을 최신으로 유지해야 함).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.external_station_id, c.external_charger_id, c.id
            FROM chargers c
            JOIN stations s ON s.id = c.station_id
            WHERE s.region_code = %s
            """,
            (region_code,),
        )
        return {(row[0], row[1]): row[2] for row in cur.fetchall()}
