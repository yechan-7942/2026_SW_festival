# 에이전트 협업 규칙

## 역할
- Codex: 구현과 파일 수정 담당.
- Claude Code: 읽기 전용 리뷰·자문 담당. Claude가 파일을 직접 수정하지 않는다.

## Claude 호출 (리뷰·자문)
- 큰 변경을 마쳤거나 판단이 애매할 때 아래 래퍼로 Claude의 의견을 구한다.
  ```bash
  scripts/ask_claude.sh "최근 변경(git diff)을 리뷰하고 버그·누락만 알려줘"
  git diff | scripts/ask_claude.sh "이 diff를 리뷰해줘"
  ```
- 래퍼는 plan 모드이며 Edit/Write/Bash를 막아 읽기 전용으로만 동작한다. 우회하지 않는다.
- Claude의 지적은 참고용이다. 반영 전에 직접 확인하고 테스트로 검증한다.

## 충돌 방지
- 같은 파일을 동시에 수정하지 않는다. Claude가 수정해야 하는 작업은 별도 worktree에서 한다.
- 커밋은 변경 파일을 지정해 나눠 담는다.
- Git 커밋 작성자는 `yechan-7942 <yechanhwangahah@gmail.com>`로 하고, AI·어시스턴트 이름을 작성자나 공동 작성자로 넣지 않는다.
