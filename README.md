# mokpo-ev-charger-poller

목포·서울 지역 전기차 충전기 상태를 주기적으로 수집해 Supabase(`charger_status_logs` 테이블)에
누적 기록하는 프로젝트입니다 (목포 5분 주기, 서울 10분 주기).
2026 빛가람 AI·ICT 경진대회 출품작의 "충전소 혼잡도 예측" 기능 학습용 데이터를 모으기 위한 파이프라인입니다.

> **2026-09-18부터 저장 방식이 CSV+git 커밋에서 Supabase 직접 저장으로 바뀌었습니다.**
> 그 이전까지 쌓인 데이터는 `data/*.csv`에 그대로 보존되어 있고(과거 이력 백업용, 더 이상 갱신 안 됨),
> `migrate_master_data.py`로 그 시점의 마스터 목록을 Supabase로 옮겨왔습니다.

## 동작 방식

공식 OpenAPI 활용가이드(v1.25) 확인 결과, 목포시는 지역구분상세코드 **`zscode=12110`**으로
서버 단에서 바로 필터링된다 (전남·광주 통합 시도코드는 `zcode=12`). 그래서 클라이언트에서
ID를 매칭할 필요 없이, API 호출 자체를 목포로 좁혀서 받는다.

1. **cron-job.org**가 5분(목포)/10분(서울)마다 GitHub Actions의 `workflow_dispatch` API를 호출해
   `poll.py`/`poll_seoul.py`를 실행시킨다. GitHub Actions 자체의 `schedule` 트리거는 무료 계정에서
   5분처럼 촘촘한 주기를 안정적으로 지켜주지 않아(실측상 몇 시간씩 벌어짐), 대신 정시 실행을
   보장하는 외부 스케줄러를 앞단에 둔 구조다.
2. `poll.py`는 환경공단 EvCharger Open API(`getChargerStatus`)를 `zscode=12110`, `period=10`(최근 10분 내
   상태 변경건), `dataType=JSON`으로 호출한다.
   - 전국 전체를 매번 조회하면 API 일일 호출 한도(1,000회)를 초과하므로, 지역+변경건 필터로 호출량을 줄인다.
3. 응답에 있는 `(statId, chgerId)`로 Supabase의 `stations`/`chargers` 테이블에서 `charger_id`(uuid)를
   찾아 `charger_status_logs`에 저장한다. `(charger_id, observed_at)` UNIQUE 제약 +
   `ON CONFLICT DO NOTHING`으로 중복을 막는다 — `period` 조회 구간(10분)이 폴링 주기(5분)보다 길어
   같은 상태 변경 이벤트가 두 번 조회될 수 있기 때문. 동시에 `chargers` 테이블의 "현재 상태"도 최신으로 갱신한다.
   - `stations`/`chargers`에 아직 없는 충전기(마스터 목록 갱신 전)는 매칭 실패로 건너뛰고 콘솔에 건수를 출력한다.

`stat` 코드는 아래처럼 `charger_status` enum으로 변환해서 저장한다 (원본 코드는
`source_status_code`에 그대로 보존):

| API `stat` | 의미 | `charger_status` |
|---|---|---|
| 0 | 알수없음 | `UNKNOWN` |
| 1 | 통신이상 | `COMMUNICATION_ERROR` |
| 2 | 사용가능 | `AVAILABLE` |
| 3 | 충전중 | `CHARGING` |
| 4 | 운영중지 | `OUT_OF_SERVICE` |
| 5 | 점검중 | `MAINTENANCE` |

(`nowTsdt`는 현재 충전 세션이 시작된 일시라 혼잡도 모델에 바로 활용 가능 — `charger_status_logs.now_tsdt`에 저장됨)

## 준비물

- **`DATA_GO_KR_SERVICE_KEY`**: data.go.kr에서 발급받은 인증키
- **`SUPABASE_DB_URL`**: Supabase 프로젝트 > Settings > Database > Connection string ("URI" 형식)
  둘 다 저장소 Settings → Secrets and variables → Actions에 등록. **로컬 `.env`에도 절대 커밋하지 말 것**
  (`.env.example` 참고, `.gitignore`에 이미 `.env` 제외돼 있음).
- Supabase 쪽에 `stations`, `chargers`, `charger_status_logs` 테이블과 `charger_status` enum
  (`AVAILABLE`, `CHARGING`, `COMMUNICATION_ERROR`, `MAINTENANCE`, `OUT_OF_SERVICE`, `UNKNOWN`)이
  미리 만들어져 있어야 한다 (D가 설계한 공용 스키마).

## 처음 연동할 때 순서

```bash
pip install -r requirements.txt
cp .env.example .env   # DATA_GO_KR_SERVICE_KEY, SUPABASE_DB_URL 채우기
```

1. **마스터 목록을 먼저 채운다** (poll.py가 charger_id를 찾으려면 stations/chargers가 있어야 함)
   ```bash
   python fetch_mokpo_chargers.py     # data/mokpo_chargers.csv 갱신
   python fetch_seoul_chargers.py     # data/seoul_chargers.csv 갱신
   python migrate_master_data.py      # 위 두 CSV를 Supabase stations/chargers로 upsert
   ```
2. **상태 폴링 테스트**
   ```bash
   python poll.py          # 목포
   python poll_seoul.py    # 서울
   ```
   `Supabase charger_status_logs에 N건 신규 기록 (중복 M건, charger 매칭 실패 K건 건너뜀)`이 정상 출력.
   매칭 실패가 많으면 1번(마스터 목록 동기화)을 다시 돌린다.

GitHub Actions에서는 `fetch-chargers.yml`/`fetch-seoul-chargers.yml`이 fetch 다음에
`migrate_master_data.py`를 자동으로 실행해 Supabase까지 동기화한다.

## `stations.region_code` 값

`migrate_master_data.py`가 목포는 `"MOKPO"`, 서울은 `"SEOUL"`로 넣는다(`poll.py`/`poll_seoul.py`의
`REGION_CODE`와 반드시 같은 값이어야 매칭됨). D가 다른 값으로 이미 맞춰둔 게 있다면
세 파일의 `REGION_CODE`/`SOURCES`를 그 값으로 바꿔야 한다.

## 외부 스케줄러(cron-job.org) 설정

1. GitHub Fine-grained personal access token 발급 (해당 저장소만, Actions: Read and write 권한만)
2. [cron-job.org](https://cron-job.org)에 아래 내용으로 cronjob 등록
   - URL: `https://api.github.com/repos/<owner>/<repo>/actions/workflows/poll.yml/dispatches`
   - Method: `POST`
   - 실행 주기: 5분마다
   - Headers: `Accept: application/vnd.github+json`, `Authorization: Bearer <PAT>`,
     `X-GitHub-Api-Version: 2022-11-28`, `Content-Type: application/json`
   - Body: `{"ref":"main"}`
   - 서울용은 URL의 `poll.yml`을 `poll_seoul.yml`로, 실행 주기를 10분으로 바꿔서 **별도 cronjob**으로 하나 더 등록

성공 시 GitHub이 `204 No Content`로 응답한다. Actions 실행 기록에는 PAT 소유자 이름으로
"Manually run by ..."라고 뜨는데, 이는 실제로 사람이 누른 게 아니라 토큰 인증 방식상 그렇게
표시되는 것뿐이다.

## 서울 폴러(poll_seoul.py)에 대해

서울시가 공개한 2023~2025년 이력 데이터는 혼잡도 예측 모델의 **학습용**으로 쓰고, `poll_seoul.py`로
지금부터 쌓는 2026년 실측 데이터는 그 모델이 실제로 잘 맞는지 **검증(테스트)용**으로 쓴다.
`poll.py`(목포)와 구조는 동일하되:
- `zscode` 대신 `zcode=11`(서울 시도 단위 전체) 사용
- 폴링 주기 **10분** (목포와 합쳐서 하루 호출 한도 1,000회를 넘지 않도록, `period` 최댓값과 맞춤)
- 서울은 충전기 수가 훨씬 많아(약 75,616건) `REGION_CODE="SEOUL"`로 별도 관리

## 과거 CSV 데이터 (`data/*.csv`)

Supabase 전환 이전(~2026-09-18)에 쌓인 이력이며 더 이상 자동 갱신되지 않는다. 필요하면
`dedupe_status_log.py`로 여전히 중복 정리는 가능하지만(`data/status_log.csv`만 대상), 새 데이터는
전부 Supabase `charger_status_logs`에 쌓이므로 앞으로의 분석은 거기서 조회하면 된다.

## 참고 문서

`docs/한국환경공단_전기자동차 충전소 정보_OpenAPI활용가이드_v1.25.docx` — 공식 API 명세.
