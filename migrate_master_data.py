"""
data/mokpo_chargers.csv, data/seoul_chargers.csv(충전소·충전기 마스터 정보)를
Supabase의 stations / chargers 테이블로 옮긴다(upsert).

poll.py / poll_seoul.py는 이 마스터 정보가 먼저 채워져 있어야 상태 로그를 어느
충전기(charger_id)에 연결할지 찾을 수 있으므로, Supabase 연동을 처음 시작할 때
(또는 fetch_mokpo_chargers.py / fetch_seoul_chargers.py로 마스터 목록을 새로 받았을 때)
먼저 실행해야 한다.

- 같은 external_station_id / external_charger_id가 이미 있으면 정보만 갱신(upsert)하고,
  없으면 새로 만든다. 여러 번 실행해도 안전하다.
- busiId(운영기관 코드)는 fetch_*.py를 최신 버전으로 다시 실행해야 CSV에 채워진다.
  없는 CSV(구버전)로 실행해도 나머지 필드는 정상적으로 들어가고 operator_code만 비어있다.

사용법:
    python migrate_master_data.py
"""

import csv
import os
import sys
from pathlib import Path

import psycopg2.extras

from db import get_connection

BASE_DIR = Path(__file__).resolve().parent

# (csv 파일, region_code) — stations.region_code에 들어갈 값. D와 이미 맞춘 값이 있다면 여기만 바꾸면 됨.
SOURCES = [
    (BASE_DIR / "data" / "mokpo_chargers.csv", "MOKPO"),
    (BASE_DIR / "data" / "seoul_chargers.csv", "SEOUL"),
]


def read_rows(csv_path: Path) -> list[dict]:
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def to_float(value: str):
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def upsert_stations(conn, rows: list[dict], region_code: str) -> dict[str, str]:
    """statId 기준으로 stations를 upsert하고 {external_station_id: station_id(uuid)}를 반환한다."""
    # 한 충전소(statId)에 충전기가 여러 대라 여러 행에 statNm/addr 등이 중복되므로 statId 기준으로 1건만 추출
    stations_by_id: dict[str, dict] = {}
    for row in rows:
        stat_id = row.get("statId", "").strip()
        if stat_id and stat_id not in stations_by_id:
            stations_by_id[stat_id] = row

    values = [
        (
            stat_id,
            row.get("statNm", ""),
            row.get("busiId", "") or None,
            row.get("busiNm", "") or None,
            row.get("addr", ""),
            region_code,
            to_float(row.get("lng")),
            to_float(row.get("lat")),
            row.get("useTime", "") or None,
        )
        for stat_id, row in stations_by_id.items()
    ]

    result: dict[str, str] = {}
    with conn.cursor() as cur:
        # fetch=True일 때 execute_values는 RETURNING 결과를 (페이지 단위로 나눠 실행하면서)
        # 함수의 반환값으로 직접 돌려준다 — cur.fetchall()로 따로 받는 게 아님에 주의.
        returned = psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO stations
                (external_station_id, name, operator_code, operator_name, address,
                 region_code, location, operating_hours)
            VALUES %s
            ON CONFLICT (external_station_id) DO UPDATE SET
                name = EXCLUDED.name,
                operator_code = EXCLUDED.operator_code,
                operator_name = EXCLUDED.operator_name,
                address = EXCLUDED.address,
                region_code = EXCLUDED.region_code,
                location = EXCLUDED.location,
                operating_hours = EXCLUDED.operating_hours,
                updated_at = now()
            RETURNING external_station_id, id
            """,
            values,
            template="""(
                %s, %s, %s, %s, %s, %s,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                %s
            )""",
            fetch=True,
            page_size=1000,
        )
        for external_station_id, station_id in returned:
            result[external_station_id] = station_id
    conn.commit()
    return result


def upsert_chargers(conn, rows: list[dict], station_id_map: dict[str, str]) -> tuple[int, int]:
    from db import map_status  # 순환 import 방지용 지역 import

    values = []
    skipped = 0
    for row in rows:
        stat_id = row.get("statId", "").strip()
        station_id = station_id_map.get(stat_id)
        if station_id is None:
            skipped += 1
            continue
        values.append((
            station_id,
            row.get("chgerId", "").strip(),
            row.get("chgerType", "") or None,
            to_float(row.get("output")),
            row.get("stat", ""),
            map_status(row.get("stat")),
        ))

    if not values:
        return 0, skipped

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO chargers
                (station_id, external_charger_id, charger_type, max_power_kw,
                 source_status_code, status)
            VALUES %s
            ON CONFLICT (station_id, external_charger_id) DO UPDATE SET
                charger_type = EXCLUDED.charger_type,
                max_power_kw = EXCLUDED.max_power_kw,
                updated_at = now()
            """,
            values,
        )
    conn.commit()
    return len(values), skipped


def main() -> None:
    total_stations = 0
    total_chargers = 0
    total_skipped = 0

    conn = get_connection()
    try:
        for csv_path, region_code in SOURCES:
            if not csv_path.exists():
                print(f"[건너뜀] {csv_path} 없음", file=sys.stderr)
                continue

            rows = read_rows(csv_path)
            print(f"{csv_path.name}: {len(rows)}행 읽음 (region={region_code})")

            station_id_map = upsert_stations(conn, rows, region_code)
            print(f"  stations upsert: {len(station_id_map)}건")
            total_stations += len(station_id_map)

            n_chargers, skipped = upsert_chargers(conn, rows, station_id_map)
            print(f"  chargers upsert: {n_chargers}건 (station 매칭 실패로 스킵 {skipped}건)")
            total_chargers += n_chargers
            total_skipped += skipped
    finally:
        conn.close()

    print(f"\n완료 — stations {total_stations}건, chargers {total_chargers}건, 스킵 {total_skipped}건")


if __name__ == "__main__":
    main()
