# mokpo-ev-charger-poller

목포 지역 전기차 충전기 상태를 5분 주기로 수집해 `data/status_log.csv`에 누적 기록하는 프로젝트입니다.
2026 빛가람 AI·ICT 경진대회 출품작의 "충전소 혼잡도 예측" 기능 학습용 데이터를 모으기 위한 파이프라인입니다.

## 동작 방식

공식 OpenAPI 활용가이드(v1.25) 확인 결과, 목포시는 지역구분상세코드 **`zscode=12110`**으로
서버 단에서 바로 필터링된다 (전남·광주 통합 시도코드는 `zcode=12`). 그래서 클라이언트에서
ID를 매칭할 필요 없이, API 호출 자체를 목포로 좁혀서 받는다.

1. GitHub Actions가 5분마다 `poll.py`를 실행한다.
2. `poll.py`는 환경공단 EvCharger Open API(`getChargerStatus`)를 `zscode=12110`, `period=10`(최근 10분 내
   상태 변경건), `dataType=JSON`으로 호출한다.
   - 전국 전체를 매번 조회하면 API 일일 호출 한도(1,000회)를 초과하므로, 지역+변경건 필터로 호출량을 줄인다.
3. 응답을 `data/status_log.csv`에 추가한다 (이미 목포로 필터링된 결과라 별도 매칭 불필요). `period`
   조회 구간(10분)이 폴링 주기(5분)보다 길어 같은 상태 변경 이벤트가 두 번 조회될 수 있는데,
   `(statId, chgerId, statUpdDt)` 조합이 이미 기록돼 있으면 건너뛰어 중복 기록을 막는다.
4. Actions가 변경된 `data/status_log.csv`를 자동으로 커밋·푸시한다.

혹시 이 중복 방지 로직 이전에 쌓인 데이터나, 다른 이유로 중복이 남아있다면 `python dedupe_status_log.py`로
한 번에 정리할 수 있다 (같은 키의 첫 기록만 남기고 제거).

`stat` 코드 의미: `0`=알수없음, `1`=통신이상, `2`=사용가능, `3`=충전중, `4`=운영중지, `5`=점검중
(공식 가이드 기준. `nowTsdt`는 현재 충전 세션이 시작된 일시라 혼잡도 모델에 바로 활용 가능)

## 준비물

- **`DATA_GO_KR_SERVICE_KEY`**: data.go.kr에서 발급받은 인증키를 저장소 Settings → Secrets and variables → Actions에 등록
- **`data/mokpo_chargers.csv`**: 목포 지역 충전소 마스터 목록(위치 포함). `fetch_mokpo_chargers.py`로 생성한다 (아래 참고).
  폴링 자체에는 더 이상 필요 없지만, 충전소 위·경도가 필요한 경로 추천 기능 등에서 재사용한다.

## 목포 충전소 마스터 목록 생성 (최초 1회, 또는 갱신 시)

```bash
pip install -r requirements.txt
export DATA_GO_KR_SERVICE_KEY="발급받은_인증키"
python fetch_mokpo_chargers.py
```

`getChargerInfo`를 `zscode=12110`으로 호출해 `data/mokpo_chargers.csv`를 만든다. 생성된 건수가
`data/reference_mokpo_stations_20250902.csv`(공공데이터, 1,804건)와 크게 다르면 데이터 시점 차이나
API 쪽 이슈일 수 있으니 점검한다.

## 로컬 테스트 (폴링)

```bash
pip install -r requirements.txt
export DATA_GO_KR_SERVICE_KEY="발급받은_인증키"
python poll.py
```
