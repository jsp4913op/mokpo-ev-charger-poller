# mokpo-ev-charger-poller

목포·서울 지역 전기차 충전기 상태를 주기적으로 수집해 각각 `data/status_log.csv`(목포, 5분 주기),
`data/seoul_status_log.csv`(서울, 10분 주기)에 누적 기록하는 프로젝트입니다.
2026 빛가람 AI·ICT 경진대회 출품작의 "충전소 혼잡도 예측" 기능 학습용 데이터를 모으기 위한 파이프라인입니다.

## 동작 방식

공식 OpenAPI 활용가이드(v1.25) 확인 결과, 목포시는 지역구분상세코드 **`zscode=12110`**으로
서버 단에서 바로 필터링된다 (전남·광주 통합 시도코드는 `zcode=12`). 그래서 클라이언트에서
ID를 매칭할 필요 없이, API 호출 자체를 목포로 좁혀서 받는다.

1. **cron-job.org**가 5분마다 GitHub Actions의 `workflow_dispatch` API를 호출해 `poll.py`를 실행시킨다.
   GitHub Actions 자체의 `schedule` 트리거는 무료 계정에서 5분처럼 촘촘한 주기를 안정적으로
   지켜주지 않아(실측상 몇 시간씩 벌어짐), 대신 정시 실행을 보장하는 외부 스케줄러를 앞단에 둔 구조다.
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

## 외부 스케줄러(cron-job.org) 설정

1. GitHub Fine-grained personal access token 발급 (해당 저장소만, Actions: Read and write 권한만)
2. [cron-job.org](https://cron-job.org)에 아래 내용으로 cronjob 등록
   - URL: `https://api.github.com/repos/<owner>/<repo>/actions/workflows/poll.yml/dispatches`
   - Method: `POST`
   - 실행 주기: 5분마다
   - Headers: `Accept: application/vnd.github+json`, `Authorization: Bearer <PAT>`,
     `X-GitHub-Api-Version: 2022-11-28`, `Content-Type: application/json`
   - Body: `{"ref":"main"}`

성공 시 GitHub이 `204 No Content`로 응답한다. Actions 실행 기록에는 PAT 소유자 이름으로
"Manually run by ..."라고 뜨는데, 이는 실제로 사람이 누른 게 아니라 토큰 인증 방식상 그렇게
표시되는 것뿐이다.

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


### data/status_log.csv — 5분마다 상태 기록이 쌓이는 파일
- `fetched_at` 저희가 이 데이터를 가져온 시각 (폴링한 시점, 우리 스크립트가 붙인 값)
- `statId` 충전소 ID (예: ME184089 — 충전소 하나를 식별하는 고유번호)
- `chgerId` 충전기 ID (한 충전소 안에 충전기가 여러 대 있을 수 있어서, 그중 몇 번인지)
- `stat` 충전기 상태 코드: 0=알수없음, 1=통신이상, 2=사용가능, 3=충전중, 4=운영중지, 5=점검중
- `statUpdDt` 이 상태로 바뀐 시각 (마지막으로 상태가 갱신된 일시)
- `lastTsdt` 가장 최근 충전이 시작된 시각
- `lastTedt` 가장 최근 충전이 종료된 시각
- `nowTsdt` 지금 충전 중이라면, 그 충전이 시작된 시각 (충전중 아니면 보통 비어있음)
- `busiId` 운영기관 코드 (예: ME = 환경부)

### data/mokpo_chargers.csv, data/seoul_chargers.csv — 충전소 마스터 목록(위치 포함)
- `statNm` 충전소명
- `addr` 주소
- `lat`, `lng` 위도·경도
- `useTime` 이용가능시간
- `busiNm` 운영기관명
- `chgerType` 충전기 타입

## 서울 충전소 마스터 목록 생성 (최초 1회, 또는 갱신 시)

```bash
pip install -r requirements.txt
export DATA_GO_KR_SERVICE_KEY="발급받은_인증키"
python fetch_seoul_chargers.py
```

`getChargerInfo`를 `zcode=11`(서울, 시도 단위 전체)로 호출해 `data/seoul_chargers.csv`를 만든다.
위치 정보 보강용이며, `output`(충전기 출력 kW) 필드도 포함한다 — 총비용 계산 시 요금 구간을
정하는 데 필요하다 (mokpo_chargers.csv도 동일).

## 서울 실시간 상태 폴링 — poll_seoul.py

서울시가 공개한 2023~2025년 이력 데이터는 혼잡도 예측 모델의 **학습용**으로 쓰고, `poll_seoul.py`로
지금부터 쌓는 2026년 실측 데이터는 그 모델이 실제로 잘 맞는지 **검증(테스트)용**으로 쓴다.

`poll.py`(목포)와 구조는 동일하되 다음이 다르다:
- `zscode` 대신 `zcode=11`(서울 시도 단위 전체) 사용
- 폴링 주기 **10분** (5분이 아님 — 목포와 합쳐서 하루 호출 한도 1,000회를 넘지 않도록, 그리고
  `period` 최댓값(10분)과 정확히 맞춰서 구멍 없이 이어지도록 한 값). 실측 결과 한 번에 1페이지
  (약 1,300여 건)로 충분했다.
- 결과는 `data/status_log.csv`가 아니라 **`data/seoul_status_log.csv`**에 별도로 쌓인다

```bash
pip install -r requirements.txt
export DATA_GO_KR_SERVICE_KEY="발급받은_인증키"
python poll_seoul.py
```

cron-job.org에는 목포용과 별개로 **두 번째 cronjob**을 등록해야 한다 — URL의
`poll.yml`을 `poll_seoul.yml`로, 실행 주기를 10분으로 바꾸는 것 외엔 동일하다 (아래
"외부 스케줄러" 절차 참고).


