# mokpo-ev-charger-poller

목포 지역 전기차 충전기 상태를 5분 주기로 수집해 `data/status_log.csv`에 누적 기록하는 프로젝트입니다.
2026 빛가람 AI·ICT 경진대회 출품작의 "충전소 혼잡도 예측" 기능 학습용 데이터를 모으기 위한 파이프라인입니다.

## 동작 방식

1. GitHub Actions가 5분마다 `poll.py`를 실행한다.
2. `poll.py`는 환경공단 EvCharger Open API(`getChargerStatus`)를 `period=10`(최근 10분 내 상태 변경건)으로 호출한다.
   - 전국 전체를 매번 조회하면 API 일일 호출 한도(1,000회)를 초과하기 때문에, "변경된 건만" 받아온다.
3. 응답 중 `data/mokpo_chargers.csv`에 등록된 (statId, chgerId)만 걸러서 `data/status_log.csv`에 추가한다.
4. Actions가 변경된 `data/status_log.csv`를 자동으로 커밋·푸시한다.

## 준비물

- **`DATA_GO_KR_SERVICE_KEY`**: data.go.kr에서 발급받은 인증키를 저장소 Settings → Secrets and variables → Actions에 등록
- **`data/mokpo_chargers.csv`**: 목포 지역 충전소의 (statId, chgerId) 목록. `fetch_mokpo_chargers.py`로 자동 생성한다 (아래 참고).

## 목포 충전소 참조 목록 생성 (최초 1회, 또는 갱신 시)

`data/reference_mokpo_stations_20250902.csv`는 "전라남도 목포시_전기차 충전소현황" 공공데이터(정적 파일)로,
충전소명·주소는 있지만 API가 요구하는 `statId`가 없어 그대로는 못 쓴다. 대신 `getChargerInfo` API를
호출해 주소에 "목포"가 포함된 충전소를 걸러 `data/mokpo_chargers.csv`를 만든다:

```bash
pip install -r requirements.txt
export DATA_GO_KR_SERVICE_KEY="발급받은_인증키"
python fetch_mokpo_chargers.py
```

생성된 건수가 `reference_mokpo_stations_20250902.csv`의 1,804건과 크게 다르면(누락/과다)
필터링 로직이나 데이터 시점 차이를 점검한다.

## 로컬 테스트 (폴링)

```bash
pip install -r requirements.txt
export DATA_GO_KR_SERVICE_KEY="발급받은_인증키"
python poll.py
```
