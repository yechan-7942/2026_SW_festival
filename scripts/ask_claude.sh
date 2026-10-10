#!/usr/bin/env bash
# Codex → Claude Code 읽기 전용 호출 래퍼.
# 사용: scripts/ask_claude.sh "요청 내용"
#       git diff | scripts/ask_claude.sh "이 diff를 리뷰해줘"   (stdin은 컨텍스트로 전달)
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "usage: $0 \"prompt\"" >&2
  exit 2
fi

# 파일 수정·셸 실행 도구는 막고 읽기·검색만 허용한다.
exec /opt/homebrew/bin/claude -p "$1" \
  --permission-mode plan \
  --disallowedTools "Edit Write NotebookEdit Bash"
