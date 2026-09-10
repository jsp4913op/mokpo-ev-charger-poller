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

- **`DATA_GO_KR_SERVICE_KEY`**: data.go.kr에서 발급받은 인증키(Decoding 키)를 저장소 Settings → Secrets and variables → Actions에 등록
- **`data/mokpo_chargers.csv`**: 목포 지역 충전소의 (statId, chgerId) 목록. 현재는 빈 템플릿 상태이며, "전라남도 목포시_전기차 충전소현황" 공공데이터를 기반으로 채워야 한다.

## 로컬 테스트

```bash
pip install -r requirements.txt
export DATA_GO_KR_SERVICE_KEY="발급받은_디코딩_키"
python poll.py
```
