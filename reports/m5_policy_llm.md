# M5 — LLM 정책 리포트 생성 (`src/policy/`)

작성일: 2026-09-21
작성자: Claude Code
근거: `reports/m4_nlp_substitute.md`가 정리한 "M4 대체 수치를 M5 프롬프트 배경근거로 쓴다"는 설계 방향

## 배경 — Claude API 대신 NVIDIA build

README 메모에 있듯 처음엔 Claude API(`anthropic` 패키지, `pyproject.toml`에 이미 있었음)를 염두에 뒀는데, 종량제 결제가 필요해 팀 예산으로 부담이었다. 대안으로 NVIDIA build(NIM API 카탈로그, OpenAI 호환 엔드포인트)를 무료 크레딧으로 쓰기로 했다 — `pyproject.toml`의 `anthropic` 의존성을 `openai`로 교체했다(어차피 `anthropic`은 `src/`에서 한 번도 쓰인 적 없었다).

## 모델 선정 — 카탈로그에 있다고 다 되는 게 아니다

`/v1/models`에 82개 모델이 뜨지만, 실제로 호출(`/v1/chat/completions`)해보면 계정별로 활성화된 것만 된다. 직접 시도한 결과:

| 모델 | 결과 |
|---|---|
| `nvidia/nemotron-3-super-120b-a12b` | 200 정상 |
| `nvidia/llama-3.1-nemotron-70b-instruct` | 404 `Not found for account` |
| `nvidia/nemotron-4-340b-instruct` | 404 `Not found for account` |
| `google/gemma-3-12b-it` | 404 `Not found for account` |
| `google/gemma-4-31b-it` | 404 `Not found for account` |

카탈로그 목록만 보고 코드를 짜면 런타임에야 404를 만나게 되므로, 실제 호출 테스트를 먼저 거친 뒤 `config/pipeline.yaml`에 확정된 모델 하나만 박아뒀다.

## 발견 — reasoning 모델이 내부 사고 중 근거를 지어낸다

`nemotron-3-super-120b`는 추론모델이다. system 프롬프트 없이 기본 호출하면 `content` 필드에 영어로 된 사고 과정이 그대로 노출되는데, 그 사고 과정 안에서 **실제로 존재하지 않는 통계("2023년 포항시 외국인 주민 의료 이용률 68%")를 예시로 만들어내는 걸 직접 목격했다.** README §1 메모("LLM 리포트는 ... 수치 지어내면 안 되니까")가 우려하는 바로 그 실패 모드다.

`{"role": "system", "content": "detailed thinking off"}`를 추가하면 사고 과정이 `reasoning_content` 필드로 완전히 분리되고 `content`엔 최종 답변만 남는다 — 이걸로 해결했다. 다만 `max_tokens`가 부족하면(300~400) 사고 과정만 쓰다 잘려서(`finish_reason="length"`) 답변 자체가 안 나온다는 것도 확인했다(2문장짜리 짧은 답변에도 reasoning 포함 완성 토큰이 약 580개 들었다) — `config/pipeline.yaml`의 `llm.max_tokens: 1500`은 이 실측을 근거로 잡은 값이다.

## 가드레일 — `[근거]` 줄 강제

`src/policy/report.py`의 `generate_policy_card()`는 응답에 `"[근거]"`로 시작하는 줄이 없으면 그 카드를 버리고 `ValueError`를 낸다. 완전한 검증은 아니다 — 모델이 `[근거]` 줄은 넣었는데 그 안의 수치를 미묘하게 바꿔 썼을 가능성까지는 코드로 못 잡는다. 최소한의 기계적 가드레일이고, 실제 배포 전엔 사람이 각 카드를 훑어봐야 한다.

## 구조

- `src/policy/context.py` — M4 대체 데이터(여가부 결과보고서 전국 수치)를 상수로 갖고 있다가 `build_context_block(fac_type)`로 프롬프트에 붙일 텍스트를 만든다. "전국 집계치, 포항 전용 아님" caveat을 텍스트 안에 강제로 포함시켜, LLM이 이 수치를 포항 고유값처럼 서술하지 않게 한다.
- `src/policy/report.py` — `build_prompt()`가 context 블록 + 행정동의 gap_score/rank를 합쳐 프롬프트를 만들고, `generate_policy_card()`가 NVIDIA build API를 호출해 카드 1건을 만든다. `build_policy_cards()`는 `ThreadPoolExecutor(max_workers=6)`로 29개 행정동을 병렬 생성한다(순차로 하면 호출당 ~10초라 5분 가까이 걸림 — 병렬로 94초).

## 실행 결과 (2026-09-21, `data/processed/policy_cards.parquet`)

`uv run python scripts/run_pipeline.py --stage policy` — 29개 행정동 전부 성공, `[근거]` 줄 누락 0건, 94초 소요.

### ⚠ 알려진 결함 — 한자 혼입

29건 중 **중앙동 카드에서 한글 대신 한자("設置")가 섞여 나온 걸 확인**했다(스모크 테스트 때는 호미곶면 카드에서 같은 종류의 결함이 발생 — 재현 가능한 문제로 보인다). 다국어 모델이 드물게 한자문화권 언어를 헷갈리는 것으로 보인다. 코드로 자동 감지·차단하지 않았다 — 어설픈 정규식 치환은 다른 정상적인 한자어(사자성어 등)까지 건드릴 위험이 있어서다. **제출 전 사람이 29건을 한 번씩 읽어보고 이런 표기 오류를 걸러내야 한다.**

## 남은 일

1. 사람이 29건 정책 카드를 검수 — 근거 수치 정확성(가드레일이 못 잡는 부분), 한자 혼입 같은 표기 오류.
2. `src/viz/`에 정책 카드를 히트맵/랭킹과 함께 보여주는 출력 형식 추가 (현재는 parquet 원자료만 있고 사람이 읽을 리포트 형태 산출물은 없음).
3. `README.md` §1 "NLP" 단계 설명 문구를 이 구조(nlp 없이 policy가 동작)에 맞게 GM 담당자와 상의 후 수정.
