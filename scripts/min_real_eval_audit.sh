#!/usr/bin/env bash
set -euo pipefail

# 最小真实审计脚本（无 mock / 无 pytest）
# 目标：验证评估链路是否真正打通
# 1) 真实仓库 repo_check + analyze
# 2) 真实 chat 对话触发在线评估
# 3) 采集 auto-eval / evaluation 结果与持久化产物
# 4) 若有审核队列，按 sample_id 执行一次审批（验证 Phase5 审核闭环）

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

API_BASE="${API_BASE:-http://127.0.0.1:8000}"
REPO_URL="${1:-${REPO_URL:-https://github.com/tiangolo/fastapi}}"
LANGUAGE="${LANGUAGE:-zh}"
START_SERVER="${START_SERVER:-0}"
PYTHON_BIN="${PYTHON_BIN:-/opt/homebrew/Caskroom/miniforge/base/envs/ai_env/bin/python}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing command: $1"; exit 1; }
}

line_count() {
  local f="$1"
  if [ -f "$f" ]; then
    wc -l <"$f" | tr -d ' '
  else
    echo 0
  fi
}

need_cmd curl
need_cmd jq
need_cmd grep
need_cmd wc
need_cmd tail

if [ "$START_SERVER" = "1" ]; then
  if [ ! -x "$PYTHON_BIN" ]; then
    echo "PYTHON_BIN not executable: $PYTHON_BIN"
    exit 1
  fi
fi

RUN_TAG="min_$(date +%Y%m%d_%H%M%S)"
OUT_DIR="$ROOT_DIR/evaluation/audit_runs/$RUN_TAG"
mkdir -p "$OUT_DIR"

EVAL_FILE="$ROOT_DIR/evaluation/sft_data/eval_results.jsonl"
POS_FILE="$ROOT_DIR/evaluation/sft_data/positive_samples.jsonl"
NEG_FILE="$ROOT_DIR/evaluation/sft_data/negative_samples.jsonl"
SKIP_FILE="$ROOT_DIR/evaluation/sft_data/skipped_samples.jsonl"

BASE_EVAL="$(line_count "$EVAL_FILE")"
BASE_POS="$(line_count "$POS_FILE")"
BASE_NEG="$(line_count "$NEG_FILE")"
BASE_SKIP="$(line_count "$SKIP_FILE")"

SERVER_PID=""
cleanup() {
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if [ "$START_SERVER" = "1" ]; then
  export AUTO_EVAL_ENABLED="${AUTO_EVAL_ENABLED:-true}"
  export AUTO_EVAL_ASYNC="${AUTO_EVAL_ASYNC:-true}"
  export AUTO_EVAL_QUEUE_ENABLED="${AUTO_EVAL_QUEUE_ENABLED:-true}"
  export AUTO_EVAL_USE_RAGAS="${AUTO_EVAL_USE_RAGAS:-true}"
  export AUTO_EVAL_RAGAS_SAMPLE_RATE="${AUTO_EVAL_RAGAS_SAMPLE_RATE:-1.0}"

  "$PYTHON_BIN" -m app.main >"$OUT_DIR/server.log" 2>&1 &
  SERVER_PID="$!"
fi

for _ in $(seq 1 120); do
  if curl -fsS "$API_BASE/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl -fsS "$API_BASE/health" >"$OUT_DIR/health.json"

curl -sS "$API_BASE/api/repo/check" \
  -H "Content-Type: application/json" \
  -d "$(jq -nc --arg url "$REPO_URL" --arg language "$LANGUAGE" '{url:$url,language:$language}')" \
  >"$OUT_DIR/repo_check.json"

SESSION_ID="$(jq -r '.session_id // empty' "$OUT_DIR/repo_check.json")"
if [ -z "$SESSION_ID" ]; then
  echo "No session_id returned. See $OUT_DIR/repo_check.json"
  exit 1
fi

ENCODED_REPO_URL="$(printf '%s' "$REPO_URL" | jq -sRr @uri)"

curl -N -sS "$API_BASE/analyze?url=${ENCODED_REPO_URL}&session_id=${SESSION_ID}&language=${LANGUAGE}&regenerate_only=false" \
  >"$OUT_DIR/analyze.sse"

if ! grep -q '"step": "finish"' "$OUT_DIR/analyze.sse"; then
  echo "Analyze stream did not include finish marker. See $OUT_DIR/analyze.sse"
fi

if [ "$LANGUAGE" = "zh" ]; then
  Q1="请基于仓库源码说明核心调用链，并给出关键文件路径。"
  Q2="请基于仓库源码说明错误处理链路，并指出关键实现位置。"
else
  Q1="Explain the core execution path with key files based on source code."
  Q2="Explain the error handling path with key implementation files."
fi

curl -N -sS "$API_BASE/chat" \
  -H "Content-Type: application/json" \
  -d "$(jq -nc --arg query "$Q1" --arg sid "$SESSION_ID" --arg repo "$REPO_URL" '{query:$query,session_id:$sid,repo_url:$repo}')" \
  >"$OUT_DIR/chat_round_1.txt"

curl -N -sS "$API_BASE/chat" \
  -H "Content-Type: application/json" \
  -d "$(jq -nc --arg query "$Q2" --arg sid "$SESSION_ID" --arg repo "$REPO_URL" '{query:$query,session_id:$sid,repo_url:$repo}')" \
  >"$OUT_DIR/chat_round_2.txt"

for _ in $(seq 1 120); do
  SNAPSHOT="$(curl -sS "$API_BASE/auto-eval/metrics")"
  QSIZE="$(printf '%s' "$SNAPSHOT" | jq -r '.metrics.queue_size // 0')"
  INFLIGHT="$(printf '%s' "$SNAPSHOT" | jq -r '.metrics.inflight // 0')"
  ENQ="$(printf '%s' "$SNAPSHOT" | jq -r '.metrics.enqueued // 0')"
  TERM="$(printf '%s' "$SNAPSHOT" | jq -r '(.metrics.processed // 0) + (.metrics.failed // 0)')"
  if [ "$QSIZE" = "0" ] && [ "$INFLIGHT" = "0" ] && [ "$TERM" -ge "$ENQ" ]; then
    break
  fi
  sleep 1
done

curl -sS "$API_BASE/auto-eval/metrics" >"$OUT_DIR/auto_eval_metrics.json"
curl -sS "$API_BASE/auto-eval/stats" >"$OUT_DIR/auto_eval_stats.json"
curl -sS "$API_BASE/evaluation/stats" >"$OUT_DIR/evaluation_stats.json"
curl -sS "$API_BASE/auto-eval/review-queue" >"$OUT_DIR/review_queue_before.json"

REVIEW_SIZE="$(jq -r '.queue_size // 0' "$OUT_DIR/review_queue_before.json")"
if [ "$REVIEW_SIZE" != "0" ]; then
  SAMPLE_ID="$(jq -r '.samples[0].sample_id // empty' "$OUT_DIR/review_queue_before.json")"
  if [ -n "$SAMPLE_ID" ]; then
    curl -sS -X POST "$API_BASE/auto-eval/approve-by-id/$SAMPLE_ID" >"$OUT_DIR/review_approve.json"
  fi
  curl -sS "$API_BASE/auto-eval/review-queue" >"$OUT_DIR/review_queue_after.json"
fi

NEW_EVAL="$(line_count "$EVAL_FILE")"
NEW_POS="$(line_count "$POS_FILE")"
NEW_NEG="$(line_count "$NEG_FILE")"
NEW_SKIP="$(line_count "$SKIP_FILE")"

TODAY="$(date +%Y%m%d)"
tail -n 20 "$EVAL_FILE" >"$OUT_DIR/eval_results_tail.jsonl" 2>/dev/null || true
tail -n 20 "$ROOT_DIR/logs/traces/score_${TODAY}.jsonl" >"$OUT_DIR/langfuse_score_tail.jsonl" 2>/dev/null || true
tail -n 20 "$ROOT_DIR/logs/traces/event_${TODAY}.jsonl" >"$OUT_DIR/langfuse_event_tail.jsonl" 2>/dev/null || true

cat >"$OUT_DIR/SUMMARY.md" <<EOF
# Minimal Real Eval Audit

- audit_time: $(date -Iseconds)
- repo_url: $REPO_URL
- session_id: $SESSION_ID
- api_base: $API_BASE

## Delta
- eval_results: $BASE_EVAL -> $NEW_EVAL (delta=$((NEW_EVAL - BASE_EVAL)))
- positive_samples: $BASE_POS -> $NEW_POS (delta=$((NEW_POS - BASE_POS)))
- negative_samples: $BASE_NEG -> $NEW_NEG (delta=$((NEW_NEG - BASE_NEG)))
- skipped_samples: $BASE_SKIP -> $NEW_SKIP (delta=$((NEW_SKIP - BASE_SKIP)))

## Snapshot
- auto_eval_metrics: $(jq -c '.' "$OUT_DIR/auto_eval_metrics.json")
- auto_eval_stats: $(jq -c '.' "$OUT_DIR/auto_eval_stats.json")
- evaluation_stats: $(jq -c '.' "$OUT_DIR/evaluation_stats.json")
EOF

echo "Audit done: $OUT_DIR"
echo "Read: $OUT_DIR/SUMMARY.md"
