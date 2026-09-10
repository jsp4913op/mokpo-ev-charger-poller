"""
data/status_log.csv에 이미 쌓인 중복 행을 정리한다.

poll.py가 (statId, chgerId, statUpdDt) 기준 중복 방지 로직을 갖추기 전에 쌓인 데이터,
또는 그 이후에도 혹시 모를 중복을 한 번씩 청소할 때 실행한다. 같은 키가 여러 번 있으면
가장 먼저(fetched_at이 가장 빠른) 기록만 남긴다.

사용법: python dedupe_status_log.py
"""

import csv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STATUS_LOG_PATH = BASE_DIR / "data" / "status_log.csv"


def main() -> None:
    if not STATUS_LOG_PATH.exists():
        print(f"[오류] {STATUS_LOG_PATH} 파일이 없습니다.")
        return

    with open(STATUS_LOG_PATH, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    seen = set()
    deduped = []
    for row in rows:
        key = (row.get("statId", ""), row.get("chgerId", ""), row.get("statUpdDt", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    removed = len(rows) - len(deduped)
    if removed == 0:
        print("중복 없음, 정리할 게 없습니다.")
        return

    with open(STATUS_LOG_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(deduped)

    print(f"전체 {len(rows)}건 중 중복 {removed}건 제거, {len(deduped)}건 남음")


if __name__ == "__main__":
    main()
