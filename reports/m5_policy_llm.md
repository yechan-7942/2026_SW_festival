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

## 가드레일 — `[근거]` 줄만으로는 부족했다

처음엔 `"[근거]"` 문자열이 content에 있는지만 봤는데, **사람이 29건을 직접 읽어보고 실제 결함을 2종류 더 찾아냈다**:

1. **reasoning 통째로 누출**: 송도동 카드가 정책 문구가 아니라 모델의 영어 사고 과정 그대로였다("We need to propose..."로 시작해 문장 중간에 끊김, `finish_reason="length"`). 그 사고 과정 안에 우연히 "[근거]"라는 단어가 있어서 기존 가드레일을 통과해버렸다.
2. **한자 혼입**: "설치"를 써야 할 자리에 한자 "設置"가 나오는 결함이 서로 다른 실행에서 최소 5개 행정동(호미곶면·중앙동·대송면·죽장면·두호동)에 반복 관측됐다. 한 단어짜리라 전체 글자 수 대비 비중이 작다.

`generate_policy_card()`를 세 단계로 보강했다:
- `finish_reason == "stop"` 확인 (끝까지 완성된 응답인지)
- content의 한글 비율이 `MIN_HANGUL_RATIO`(0.3) 이상인지 확인 — reasoning 누출은 거의 다 영어라 이 비율이 급격히 낮다. 이 두 조건 중 하나라도 실패하면 재시도(`retries=2`)한다.
- `KNOWN_HANJA_LEAKS`("設置"→"설치") 치환 — 정확히 이 문자열로만 반복 관측된 결함이라 좁게 고쳤다. 범용 한자→한글 변환기는 만들지 않았다(정상적인 한자어까지 건드릴 위험).

보강 후 29건 재생성 → `[근거]` 누락 0건, 한자 혼입 0건으로 확인. 그래도 완전한 검증은 아니다 — 모델이 `[근거]` 줄은 넣었는데 그 안의 수치를 미묘하게 바꿔 썼을 가능성까지는 코드로 못 잡는다. 최소한의 기계적 가드레일이고, 실제 배포 전엔 사람이 각 카드를 훑어봐야 한다.

## 구조

- `src/policy/context.py` — M4 대체 데이터(여가부 전국 + 경상북도 1권역 결과보고서, `reports/m4_nlp_substitute.md` 참고)를 상수로 갖고 있다가 `build_context_block(fac_type)`로 프롬프트에 붙일 텍스트를 만든다. 경북 데이터를 "배경 근거 1"로 먼저, 전국 데이터를 "배경 근거 2"로 뒤에 붙여 더 가까운 지리적 근거를 우선시하되, 각 수치가 어느 조사 출처인지 라벨을 명확히 남긴다. "포항 단독 수치 아님" caveat도 텍스트 안에 강제로 포함시켜, LLM이 이 수치를 포항 고유값처럼 서술하지 않게 한다.
- `src/policy/report.py` — `build_prompt()`가 context 블록 + 행정동의 gap_score/rank를 합쳐 프롬프트를 만들고, `generate_policy_card()`가 NVIDIA build API를 호출해 카드 1건을 만든다. `build_policy_cards()`는 `ThreadPoolExecutor(max_workers=6)`로 29개 행정동을 병렬 생성한다(순차로 하면 호출당 ~10초라 5분 가까이 걸림 — 병렬로 94초).

## 실행 결과 (2026-09-21, `data/processed/policy_cards.parquet`)

`uv run python scripts/run_pipeline.py --stage policy` — 29개 행정동 전부 성공, `[근거]` 줄 누락 0건, 한자 혼입 0건.

가드레일 보강(위 "가드레일" 절) 전 첫 실행에서는 사람 검수로 송도동 reasoning 누출 1건, 여러 실행에 걸쳐 한자 혼입 5건을 발견했다. 이후 경북 데이터를 추가해 프롬프트가 길어진 재실행에서는 **29개를 동시에 병렬 호출하니 NVIDIA build free-tier에서 503(Service Unavailable, 과부하) 에러도 실제로 났다** — `generate_policy_card()`에 API 호출 자체도 재시도 대상으로 포함시켰다(지수 백오프).

### ⚠ 자기 진단 실수 — `| tail -N` 파이프가 진짜 종료 코드를 가렸다

503 대응 코드를 넣고 재실행했을 때 `uv run python scripts/run_pipeline.py --stage policy 2>&1 | tail -10`로 돌렸는데, 스크립트가 재시도를 다 소진하고 실제로는 `ValueError`로 죽었다. 그런데 **셸이 보고하는 종료 코드는 파이프의 마지막 명령(`tail`)의 것**이라 `tail`은 정상 종료(0)했고, 그래서 "exit code 0 = 성공"으로 잘못 판단했다. 파일도 재생성 안 된 채(이전 성공본 그대로) 남아 있었는데, "행 수 29·근거 누락 0·한자 0"이라는 검증도 전부 통과해서 겉보기엔 멀쩡해 보였다 — 실은 그냥 예전 결과였을 뿐이다. `cmp`로 파일이 이전 커밋과 완전히 동일한 바이트인 걸 발견하고서야 알아챘다. 파이프 없이 직접 실행하고 `$?`를 확인해서 재현·수정했다. **교훈: 셸 파이프 뒤에 붙는 `| tail`/`| head` 등은 원본 명령의 실패를 숨긴다 — 성공 여부가 중요한 명령은 파이프 없이 돌리거나 `pipefail`을 켤 것.**

## 남은 일

1. 그래도 사람이 29건 정책 카드를 한 번은 읽어보는 게 좋다 — 가드레일이 못 잡는 영역(근거 수치가 배경 근거와 미묘하게 다르게 인용됐는지)은 기계적으로 검증 안 됨.
2. `src/viz/`에 정책 카드를 히트맵/랭킹과 함께 보여주는 출력 형식 추가 (현재는 parquet 원자료만 있고 사람이 읽을 리포트 형태 산출물은 없음).
3. `README.md` §1 "NLP" 단계 설명 문구를 이 구조(nlp 없이 policy가 동작)에 맞게 GM 담당자와 상의 후 수정.
