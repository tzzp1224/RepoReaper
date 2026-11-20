"""One job per process and working directory; never receives reference answers."""
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

from .data import read_json, write_json
from .meter import Ledger, Meter


async def build_full_index(source, store, repo_url, checkpoint_path, batch_chunks=500):
    """Index every production-filtered text file, checkpointing at file boundaries."""
    from app.services.chunking_service import UniversalChunker, ChunkingConfig

    checkpoint_path = Path(checkpoint_path)
    checkpoint = read_json(checkpoint_path) if checkpoint_path.exists() else {
        "revision": source.revision, "completed_files": [], "skipped_unreadable": []
    }
    if checkpoint["revision"] != source.revision:
        raise ValueError("Full-index checkpoint revision mismatch")
    if checkpoint_path.exists():
        # A prior process may have committed vectors after the last BM25 cache save.
        await store._rebuild_bm25_index()
    completed = set(checkpoint["completed_files"])
    skipped = set(checkpoint["skipped_unreadable"])
    all_files = await source.list_files()
    chunker = UniversalChunker(config=ChunkingConfig(min_chunk_size=50))
    pending_docs, pending_meta, pending_files = [], [], []

    async def flush():
        nonlocal pending_docs, pending_meta, pending_files
        if not pending_files:
            return
        await store.add_documents(pending_docs, pending_meta, rebuild_index=False)
        completed.update(path for path in pending_files if path in store.indexed_files)
        checkpoint.update(completed_files=sorted(completed), skipped_unreadable=sorted(skipped),
                          total_files=len(all_files), indexed_chunks=len(store._doc_store))
        write_json(checkpoint_path, checkpoint)
        pending_docs, pending_meta, pending_files = [], [], []

    for file_path in all_files:
        if file_path in completed or file_path in skipped:
            continue
        content = await source.read_file(file_path)
        if content is None:
            skipped.add(file_path)
            checkpoint.update(completed_files=sorted(completed), skipped_unreadable=sorted(skipped),
                              total_files=len(all_files), indexed_chunks=len(store._doc_store))
            write_json(checkpoint_path, checkpoint)
            continue
        chunks = await asyncio.to_thread(chunker.chunk_file, content, file_path)
        if not chunks:
            completed.add(file_path)
            continue
        if pending_files and len(pending_docs) + len(chunks) > batch_chunks:
            await flush()
        pending_files.append(file_path)
        for chunk in chunks:
            meta = chunk["metadata"]
            pending_docs.append(chunk["content"])
            pending_meta.append({
                "file": meta["file"], "type": meta["type"], "name": meta.get("name", ""),
                "class": meta.get("class") or "", "start_line": meta.get("start_line"),
                "end_line": meta.get("end_line"),
            })
    await flush()
    await store.finalize_deferred_index()
    failures = {path: reason for path, reason in store.index_failures.items()
                if path not in completed}
    checkpoint.update(completed_files=sorted(completed), skipped_unreadable=sorted(skipped),
                      total_files=len(all_files), indexed_chunks=len(store._doc_store),
                      index_failures=failures, status="completed" if not failures else "incomplete")
    write_json(checkpoint_path, checkpoint)
    return checkpoint


async def execute(job):
    # Must run before importing any application module or global singleton.
    os.environ.update(QDRANT_MODE="local", QDRANT_LOCAL_PATH=str(Path("data/qdrant_db").resolve()),
                      LANGFUSE_ENABLED="false", AUTO_EVAL_ENABLED="false", LOCK_BACKEND="file",
                      LOCK_DIR=str(Path("data/locks").resolve()),
                      RUNTIME_TRACE_DB_PATH=str(Path("data/runtime_traces.db").resolve()))
    if job.get("model"):
        os.environ["MODEL_NAME"] = job["model"]
    from app.core.config import settings
    from app.services import agent_service, chat_service, jev_gate, vector_service
    from app.services.repository_source import GitSnapshotSource, use_repository_source
    from app.storage.qdrant_store import close_shared_client
    from app.utils.llm_client import client
    from app.utils.session import clear_conversation_memory

    if settings.LLM_PROVIDER not in ("deepseek", "openai"):
        raise ValueError("Offline metering currently requires an OpenAI-compatible provider")
    if client is None:
        raise ValueError("Generation client unavailable")
    meter = Meter(Ledger(job["ledger"], job["budget_cny"]), job["job_id"], job["phase"], job["rates"])
    if job.get("variant") in ("G", "M"):
        os.environ.update(JEV_MODE="active", JEV_MODEL=job["jev_model"])
        jev_gate.request_choice = meter.wrap(jev_gate.request_choice, "jev")
    else:
        os.environ["JEV_MODE"] = "off"
    # SDK retries would otherwise be invisible. The existing application retry
    # policy remains active, and every attempt now has a durable ledger row.
    client._client = client._client.with_options(max_retries=0)
    measured = meter.wrap(client._client.chat.completions.create, "generation")
    async def create(**kwargs):
        # New DeepSeek V4 defaults to thinking. Preserve this project's original
        # non-thinking setting when explicitly selecting a V4 model offline.
        if settings.LLM_PROVIDER == "deepseek" and settings.default_model_name.startswith("deepseek-v4"):
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        return await measured(**kwargs)
    client._client.chat.completions.create = create
    embedding = vector_service.get_embedding()
    embedding._client = embedding._client.with_options(max_retries=0)
    embedding._client.embeddings.create = meter.wrap(embedding._client.embeddings.create, "embedding")
    source = GitSnapshotSource(job["repo_root"], job["repo"], job["commit_id"])
    session_id = "bench_seed"
    started = time.perf_counter()
    result = {"status": "error", "answer": ""}
    try:
        with use_repository_source(source):
            store = None
            if job["kind"] == "seed" or job.get("variant") != "A":
                store = vector_service.store_manager.get_store(session_id)
                await store.initialize()
            if job["kind"] == "full_seed":
                checkpoint = await build_full_index(
                    source, store, f"https://github.com/{source.repo}", "full_index_checkpoint.json")
                context = dict(store.global_context)
                context["full_static_index"] = {
                    "revision": source.revision, "total_files": checkpoint["total_files"],
                    "indexed_files": len(checkpoint["completed_files"]),
                    "skipped_unreadable": len(checkpoint["skipped_unreadable"]),
                    "chunks": checkpoint["indexed_chunks"],
                }
                await store.save_context(f"https://github.com/{source.repo}", context)
                result = {"status": "completed" if checkpoint["status"] == "completed" else "error",
                          "indexed_files": checkpoint["completed_files"],
                          "skipped_unreadable": checkpoint["skipped_unreadable"],
                          "index_failures": checkpoint["index_failures"],
                          "chunks": checkpoint["indexed_chunks"], "summary": store.global_context}
            elif job["kind"] == "seed":
                finished = False
                errors = []
                with Path("analysis.jsonl").open("w") as output:
                    async for event in agent_service.agent_stream(
                            f"https://github.com/{source.repo}", session_id, language="en"):
                        output.write(event + "\n")
                        parsed = json.loads(event)
                        finished |= parsed.get("step") == "finish"
                        if parsed.get("step") == "error":
                            errors.append(parsed.get("message"))
                result = {"status": "completed" if finished and not errors and store._doc_store else "error",
                          "error": errors, "indexed_files": sorted(store.indexed_files),
                          "index_failures": store.index_failures, "chunks": len(store._doc_store),
                          "summary": store.global_context}
            elif job["variant"] == "A":
                response = await client.chat.completions.create(
                    model=settings.default_model_name,
                    messages=[{"role": "system", "content": "Answer the repository question in English."},
                              {"role": "user", "content": f"Repository: {source.repo}\n{job['question']}"}],
                    temperature=chat_service.chat_config.temperature_final,
                    max_tokens=chat_service.chat_config.max_tokens)
                answer = response.choices[0].message.content or ""
                result = {"answer": answer, "status": "completed" if answer.strip() else "empty_answer",
                          "tool_calls": [], "evidence": [], "jit_rounds": 0}
            else:
                clear_conversation_memory(session_id)
                cfg = chat_service.ChatConfig(**job["chat_config"])
                if job["variant"] in ("B", "E"):
                    cfg.max_jit_rounds = 0
                elif job["variant"] == "D":
                    cfg.rewrite_query = False
                path_rankings = {"F": "legacy", "G": "legacy", "H": "components", "I": "components",
                                 "K": "source", "L": "source", "M": "source"}
                comparison = job["variant"] in path_rankings
                kwargs = {}
                if comparison:
                    cfg.jev_timeout = job["jev_timeout"]
                    cfg.jev_router_timeout = job["jev_timeout"]
                    cfg.path_ranking = path_rankings[job["variant"]]
                    cfg.exploration_mode = {"I": "auto", "L": "auto", "M": "jev"}.get(job["variant"], "off")
                    trace_id = chat_service.tracing_service.start_trace("jit_comparison", session_id)
                    kwargs = {"run_id": trace_id, "repo_url": f"https://github.com/{source.repo}", "compare_seed": True}
                async with jev_gate.connection_pool():
                    with Path("stream.txt").open("w") as output:
                        async for text in chat_service.process_chat_stream(job["question"], session_id, config=cfg, **kwargs):
                            output.write(text)
                result = asdict(chat_service.get_eval_data(trace_id if comparison else session_id))
                if comparison:
                    tracer = chat_service.tracing_service
                    result["jev_decisions"] = [json.loads(step["payload_json"]) for step in
                        tracer.runtime_store.list_steps(trace_id) if step["step_name"] == "jev_decision"]
                    tracer.end_trace({"stream_completed": result["status"] == "completed"})
                result["index_failures"] = store.index_failures
                result["chat_config"] = asdict(cfg)
    finally:
        await vector_service.store_manager.close_all()
        await close_shared_client()
    result.update(latency_ms=(time.perf_counter() - started) * 1000, reads=source.reads,
                  revision=source.revision, requested_model=settings.default_model_name)
    return result


def main():
    job, output = read_json(sys.argv[1]), Path(sys.argv[2])
    started = time.perf_counter()
    try:
        result = asyncio.run(asyncio.wait_for(execute(job), timeout=job["timeout_seconds"]))
    except TimeoutError:
        result = {"status": "timeout", "answer": "", "error": "Worker time limit exceeded"}
    except Exception as exc:
        # Keep provider credentials out of artifacts; detailed provider errors
        # may include request URLs. Error category is enough for denominator accounting.
        result = {"status": "error", "answer": "", "error": type(exc).__name__}
    result["latency_ms"] = (time.perf_counter() - started) * 1000
    write_json(output, result)


if __name__ == "__main__":
    main()
