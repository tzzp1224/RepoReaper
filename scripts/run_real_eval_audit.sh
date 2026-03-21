#!/usr/bin/env bash
set -euo pipefail

# 真实评估链路审计脚本（不使用测试桩，不调用 pytest）
# 功能：
# 1) 启动真实服务（可选）
# 2) 对真实 GitHub 仓库执行 analyze
# 3) 发送真实 /chat 对话触发在线评估
# 4) 收集评估指标、落盘结果、审核队列，并输出审计摘要

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

API_BASE="${API_BASE:-http://127.0.0.1:8000}"
REPO_URL="${1:-${REPO_URL:-https://github.com/tiangolo/fastapi}}"
LANGUAGE="${LANGUAGE:-zh}"
START_SERVER="${START_SERVER:-1}"
PYTHON_BIN="${PYTHON_BIN:-/opt/homebrew/Caskroom/miniforge/base/envs/ai_env/bin/python}"

for cmd in curl jq sed awk grep tail wc date mktemp; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "❌ Missing required command: $cmd"
    exit 1
  fi
done

if [ ! -x "$PYTHON_BIN" ]; then
  echo "❌ PYTHON_BIN not executable: $PYTHON_BIN"
  exit 1
fi

AUDIT_TAG="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="$ROOT_DIR/evaluation/audit_runs/$AUDIT_TAG"
mkdir -p "$OUT_DIR"

SERVER_PID=""
cleanup() {
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

count_lines() {
  local f="$1"
  if [ -f "$f" ]; then
    wc -l <"$f" | tr -d ' '
  else
    echo 0
  fi
}

BASE_EVAL_COUNT="$(count_lines "$ROOT_DIR/evaluation/sft_data/eval_results.jsonl")"
BASE_POS_COUNT="$(count_lines "$ROOT_DIR/evaluation/sft_data/positive_samples.jsonl")"
BASE_NEG_COUNT="$(count_lines "$ROOT_DIR/evaluation/sft_data/negative_samples.jsonl")"
BASE_SKIP_COUNT="$(count_lines "$ROOT_DIR/evaluation/sft_data/skipped_samples.jsonl")"

if [ "$START_SERVER" = "1" ]; then
  echo "🚀 Starting real server ..."
  export AUTO_EVAL_USE_RAGAS="${AUTO_EVAL_USE_RAGAS:-true}"
  export AUTO_EVAL_RAGAS_SAMPLE_RATE="${AUTO_EVAL_RAGAS_SAMPLE_RATE:-1.0}"
  export AUTO_EVAL_DIFF_THRESHOLD="${AUTO_EVAL_DIFF_THRESHOLD:-0.08}"
  export AUTO_EVAL_VISUALIZE_ONLY="${AUTO_EVAL_VISUALIZE_ONLY:-false}"
  export AUTO_EVAL_ENABLED="${AUTO_EVAL_ENABLED:-true}"
  export AUTO_EVAL_ASYNC="${AUTO_EVAL_ASYNC:-true}"
  export AUTO_EVAL_QUEUE_ENABLED="${AUTO_EVAL_QUEUE_ENABLED:-true}"

  "$PYTHON_BIN" -m app.main >"$OUT_DIR/server.log" 2>&1 &
  SERVER_PID="$!"

  for _ in $(seq 1 120); do
    if curl -fsS "$API_BASE/health" >/dev/null 2>&1; then
      echo "✅ Server is healthy: $API_BASE"
      break
    fi
    sleep 1
  done

  if ! curl -fsS "$API_BASE/health" >/dev/null 2>&1; then
    echo "❌ Server did not become healthy in 120s. Check $OUT_DIR/server.log"
    exit 1
  fi
else
  echo "ℹ️ START_SERVER=0, assuming server is already running at $API_BASE"
fi

echo "🔎 Step 1: create/check session for repo: $REPO_URL"
curl -sS "$API_BASE/api/repo/check" \
  -H "Content-Type: application/json" \
  -d "$(jq -nc --arg url "$REPO_URL" --arg language "$LANGUAGE" '{url:$url,language:$language}')" \
  | tee "$OUT_DIR/repo_check.json" >/dev/null

SESSION_ID="$(jq -r '.session_id // empty' "$OUT_DIR/repo_check.json")"
if [ -z "$SESSION_ID" ]; then
  echo "❌ Failed to obtain session_id. See $OUT_DIR/repo_check.json"
  exit 1
fi
echo "✅ session_id: $SESSION_ID"

ENCODED_REPO_URL="$("$PYTHON_BIN" - <<'PY' "$REPO_URL"
import sys, urllib.parse
print(urllib.parse.quote(sys.argv[1], safe=""))
PY
)"

echo "📚 Step 2: analyze real repository (this may take a while)"
curl -N -sS "$API_BASE/analyze?url=${ENCODED_REPO_URL}&session_id=${SESSION_ID}&language=${LANGUAGE}&regenerate_only=false" \
  | tee "$OUT_DIR/analyze.sse" >/dev/null

if grep -q '"step": "finish"' "$OUT_DIR/analyze.sse"; then
  echo "✅ analyze finished"
else
  echo "⚠️ analyze stream did not show explicit finish marker, continue to chat audit"
fi

if [ "$LANGUAGE" = "zh" ]; then
  QUERIES=(
    "这个仓库里依赖注入主流程在哪些文件？请给出关键代码位置。"
    "APIRouter 和 FastAPI 在路由注册职责上有什么差异？请结合源码解释。"
    "请求校验、异常处理、响应序列化在这个仓库里是如何串起来的？"
  )
else
  QUERIES=(
    "Which files implement dependency injection flow in this repository?"
    "What are the responsibilities split between APIRouter and FastAPI?"
    "How are validation, exception handling, and response serialization connected?"
  )
fi

echo "💬 Step 3: run real chat rounds to trigger online auto-eval"
: >"$OUT_DIR/metrics_timeline.jsonl"
for i in "${!QUERIES[@]}"; do
  round=$((i + 1))
  q="${QUERIES[$i]}"
  echo "  - chat round $round: $q"

  curl -N -sS "$API_BASE/chat" \
    -H "Content-Type: application/json" \
    -d "$(jq -nc --arg query "$q" --arg sid "$SESSION_ID" --arg repo "$REPO_URL" '{query:$query,session_id:$sid,repo_url:$repo}')" \
    | tee "$OUT_DIR/chat_round_${round}.txt" >/dev/null

  curl -sS "$API_BASE/auto-eval/metrics" \
    | jq -c --arg ts "$(date -Iseconds)" --argjson round "$round" '{ts:$ts,round:$round,metrics:.}' \
    >>"$OUT_DIR/metrics_timeline.jsonl"
done

echo "⏳ Step 4: wait for async sidecar queue to drain"
for _ in $(seq 1 90); do
  snapshot="$(curl -sS "$API_BASE/auto-eval/metrics")"
  qsize="$(printf '%s' "$snapshot" | jq -r '.metrics.queue_size // 0')"
  inflight="$(printf '%s' "$snapshot" | jq -r '.metrics.inflight // 0')"
  enq="$(printf '%s' "$snapshot" | jq -r '.metrics.enqueued // 0')"
  term="$(printf '%s' "$snapshot" | jq -r '(.metrics.processed // 0) + (.metrics.failed // 0)')"
  if [ "$qsize" = "0" ] && [ "$inflight" = "0" ] && [ "$term" -ge "$enq" ]; then
    break
  fi
  sleep 1
done

echo "📊 Step 5: collect runtime snapshots"
curl -sS "$API_BASE/auto-eval/metrics" >"$OUT_DIR/auto_eval_metrics.json"
curl -sS "$API_BASE/auto-eval/stats" >"$OUT_DIR/auto_eval_stats.json"
curl -sS "$API_BASE/evaluation/stats" >"$OUT_DIR/evaluation_stats.json"
curl -sS "$API_BASE/auto-eval/review-queue" >"$OUT_DIR/review_queue_before.json"

REVIEW_SIZE="$(jq -r '.queue_size // 0' "$OUT_DIR/review_queue_before.json")"
if [ "$REVIEW_SIZE" != "0" ]; then
  SAMPLE_ID="$(jq -r '.samples[0].sample_id // empty' "$OUT_DIR/review_queue_before.json")"
  if [ -n "$SAMPLE_ID" ]; then
    echo "🧾 Step 6: approve first review sample by sample_id=$SAMPLE_ID"
    curl -sS -X POST "$API_BASE/auto-eval/approve-by-id/$SAMPLE_ID" >"$OUT_DIR/review_approve.json"
  fi
  curl -sS "$API_BASE/auto-eval/review-queue" >"$OUT_DIR/review_queue_after.json"
fi

NEW_EVAL_COUNT="$(count_lines "$ROOT_DIR/evaluation/sft_data/eval_results.jsonl")"
NEW_POS_COUNT="$(count_lines "$ROOT_DIR/evaluation/sft_data/positive_samples.jsonl")"
NEW_NEG_COUNT="$(count_lines "$ROOT_DIR/evaluation/sft_data/negative_samples.jsonl")"
NEW_SKIP_COUNT="$(count_lines "$ROOT_DIR/evaluation/sft_data/skipped_samples.jsonl")"

tail -n 30 "$ROOT_DIR/evaluation/sft_data/eval_results.jsonl" >"$OUT_DIR/eval_results_tail.jsonl" 2>/dev/null || true
tail -n 30 "$ROOT_DIR/evaluation/sft_data/positive_samples.jsonl" >"$OUT_DIR/positive_tail.jsonl" 2>/dev/null || true
tail -n 30 "$ROOT_DIR/evaluation/sft_data/negative_samples.jsonl" >"$OUT_DIR/negative_tail.jsonl" 2>/dev/null || true
tail -n 50 "$ROOT_DIR/logs/traces/score_$(date +%Y%m%d).jsonl" >"$OUT_DIR/langfuse_score_tail.jsonl" 2>/dev/null || true
tail -n 80 "$ROOT_DIR/logs/traces/event_$(date +%Y%m%d).jsonl" >"$OUT_DIR/langfuse_event_tail.jsonl" 2>/dev/null || true

echo "🧠 Step 7: run analyzer on real accumulated eval results"
"$PYTHON_BIN" "$ROOT_DIR/evaluation/analyze_eval_results.py" summary >"$OUT_DIR/analyze_summary.txt" 2>&1 || true
"$PYTHON_BIN" "$ROOT_DIR/evaluation/analyze_eval_results.py" report >"$OUT_DIR/analyze_report_stdout.md" 2>&1 || true

{
  echo "# Real Eval Audit Summary"
  echo
  echo "- audit_time: $(date -Iseconds)"
  echo "- repo_url: $REPO_URL"
  echo "- language: $LANGUAGE"
  echo "- session_id: $SESSION_ID"
  echo "- api_base: $API_BASE"
  echo
  echo "## Delta"
  echo "- eval_results: $BASE_EVAL_COUNT -> $NEW_EVAL_COUNT (delta=$((NEW_EVAL_COUNT - BASE_EVAL_COUNT)))"
  echo "- positive_samples: $BASE_POS_COUNT -> $NEW_POS_COUNT (delta=$((NEW_POS_COUNT - BASE_POS_COUNT)))"
  echo "- negative_samples: $BASE_NEG_COUNT -> $NEW_NEG_COUNT (delta=$((NEW_NEG_COUNT - BASE_NEG_COUNT)))"
  echo "- skipped_samples: $BASE_SKIP_COUNT -> $NEW_SKIP_COUNT (delta=$((NEW_SKIP_COUNT - BASE_SKIP_COUNT)))"
  echo
  echo "## Runtime Snapshot"
  echo "- auto_eval_metrics: $(jq -c '.' "$OUT_DIR/auto_eval_metrics.json")"
  echo "- auto_eval_stats: $(jq -c '.' "$OUT_DIR/auto_eval_stats.json")"
  echo "- evaluation_stats: $(jq -c '.' "$OUT_DIR/evaluation_stats.json")"
} >"$OUT_DIR/SUMMARY.md"

echo
echo "✅ Real audit completed."
echo "📁 Output directory: $OUT_DIR"
echo "📌 Read first: $OUT_DIR/SUMMARY.md"
