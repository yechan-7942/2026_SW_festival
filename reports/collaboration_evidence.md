# 학제 간 협업 기록

2026-10-10 기준. 역할 계획과 실제 검토 증거를 구분한다. 이 문서의 빈 항목은 담당자가 실제로 검토한 뒤에만 채운다.

| 단계 | 현재 확인되는 산출물 | 검토자·일자·의견 | 반영 결과 | 상태 |
|---|---|---|---|---|
| 데이터 정제 | `data/processed/admin_units.parquet`, `facilities.parquet`, `reports/m0_m1_data_pipeline.md` | 데이터 품질 검토 기록 미첨부 | 행정동 29개, 의료시설 908건 처리 | 구현 산출물 확인, 인수 검토 미확인 |
| 접근성·격차 | `src/access/two_sfca.py`, `src/gap/score.py`, `reports/m6_validation.md` | 0.5/0.5의 GM 확정 의견 미첨부 | 중립 기본값으로 분석, 민감도 비교 완료 | 계산 확인, 가중치 승인 미확인 |
| 정책 카드 | `data/processed/policy_cards.parquet`, `reports/m5_policy_llm.md` | GM 정책 타당성 검토 기록 미첨부 | 29개 카드 생성, 자동 형식 검사 기록 | 사람 검토 미확인 |
| 파일럿 비교 | `config/pilot.yaml`, `scripts/build_pilot_review.py` | 블라인드 평가 미실시 | 5개 동 평가 양식 준비 | 대기 |

## 검토 기록 양식

다음 항목을 한 건씩 추가한다. 검토자가 확인한 뒤 원문 의견 또는 공유 문서 링크를 남기고, 코드·설정·카드 중 무엇이 바뀌었는지 커밋이나 파일 경로로 연결한다.

| 일자 | 검토자·전공 | 전달받은 입력과 버전 | 판단·근거 | 요청한 변경 | 반영 파일·결과 | 재검토 결과 |
|---|---|---|---|---|---|---|
| 기록 대기 |  |  |  |  |  |  |

0.5/0.5를 유지하더라도 “유지”를 결정한 근거와 대안 가중치 비교 결과를 기록해야 한다. 카드 평가는 `outputs/pilot_review/review_form.csv`에 작성하고, 평가가 끝날 때까지 Git 제외 대상인 `data/interim/pilot_review_answer_key.csv`를 평가자에게 전달하지 않는다.
