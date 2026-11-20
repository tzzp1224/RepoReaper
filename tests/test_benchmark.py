import asyncio
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from evaluation.benchmark.data import prepare, prepare_jit, jobs, load_manifest, read_json, write_json
from evaluation.benchmark.meter import BudgetExceeded, Ledger, Meter
from evaluation.benchmark.report import paired_bootstrap, generate_report
from evaluation.benchmark.runner import restore_seed, tree_hash


@pytest.mark.parametrize("failed", [0, 1, 2])
def test_embedding_failure_preserves_original_positions(failed):
    from app.utils.embedding import EmbeddingConfig, EmbeddingService

    class Fake(EmbeddingService):
        def __init__(self):
            self.config = EmbeddingConfig(batch_size=2)
            self._stats = {"total_texts": 0}

        async def _embed_batch_with_semaphore(self, texts, index):
            await asyncio.sleep(.001 * (2-index))
            if index == failed:
                raise RuntimeError("injected")
            return index, [[float(t)] for t in texts]

    result = asyncio.run(Fake().embed_batch([str(i) for i in range(5)]))
    assert result == [[] if i//2 == failed else [float(i)] for i in range(5)]


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def test_snapshot_uses_exact_commit_and_rejects_escape(tmp_path):
    from app.services.repository_source import GitSnapshotSource, use_repository_source, bound_source
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "a.py").write_text("version = 1\n")
    (tmp_path / "link.py").symlink_to("a.py")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "one")
    first = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "a.py").write_text("version = 2\n")
    git(tmp_path, "commit", "-qam", "two")
    second = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "a.py").write_text("UNCOMMITTED\n")
    one = GitSnapshotSource(tmp_path, "owner/repo", first)
    two = GitSnapshotSource(tmp_path, "owner/repo", second)
    assert asyncio.run(one.read_file("a.py")) == "version = 1\n"
    assert asyncio.run(two.read_file("a.py")) == "version = 2\n"
    assert asyncio.run(one.read_file("link.py")) is None
    assert asyncio.run(one.read_file("missing.py")) is None
    with pytest.raises(ValueError):
        asyncio.run(one.read_file("../a.py"))
    with use_repository_source(one):
        assert bound_source("https://github.com/owner/repo") is one
        with pytest.raises(ValueError):
            bound_source("https://github.com/other/repo")
    assert bound_source("https://github.com/owner/repo") is None


def test_legacy_source_binding_never_initializes_github_client(monkeypatch):
    from app.services import github_service
    from app.services.repository_source import use_repository_source
    class Source:
        repo, revision = "owner/repo", "a" * 40
        async def list_files(self): return ["a.py"]
        async def read_file(self, path): return "local" if path == "a.py" else None
    def forbidden(): raise AssertionError("Live GitHub call")
    monkeypatch.setattr(github_service, "get_github_client", forbidden)
    async def run():
        with use_repository_source(Source()):
            assert await github_service.get_repo_structure("https://github.com/owner/repo") == ["a.py"]
            assert await github_service.get_file_content("https://github.com/owner/repo", "a.py") == "local"
            assert await github_service.get_file_content("https://github.com/owner/repo", "missing") is None
    asyncio.run(run())


def test_partial_upsert_only_publishes_acknowledged_chunks(monkeypatch, tmp_path):
    from app.services import vector_service
    class Embed:
        async def embed_batch(self, docs, **kwargs): return [[1.0]*1024 for _ in docs]
    class Store:
        async def add_documents(self, docs, embeddings):
            self.last_added_ids = [docs[1].id]
            return 1
    store = vector_service.VectorStore("partial")
    store._initialized, store._qdrant = True, Store()
    store._context_file = str(tmp_path / "context.json")
    store._context_lock_file = str(tmp_path / "context.lock")
    store._cache_file = str(tmp_path / "cache.pkl")
    store._rebuild_bm25_sync = lambda: None
    monkeypatch.setattr(vector_service, "get_embedding", lambda: Embed())
    added = asyncio.run(store.add_documents(["alpha", "beta"], [{"file": "a.py"}, {"file": "a.py"}]))
    assert added == 1
    assert [d.content for d in store._doc_store] == ["beta"]
    assert "a.py" not in store.indexed_files
    assert "a.py" in read_json(store._context_file)["index_failures"]


def dataset():
    return [{"repo": f"owner/repo{repo}", "commit_id": str(repo+1)*40, "question": f"Question {i}",
             "answer": f"SECRET_REFERENCE_{repo}_{i}", "qa_type": {"class_name": "How"}}
            for repo in range(2) for i in range(10)]


def test_selection_is_stable_reference_separated_and_complete(tmp_path):
    one = prepare(dataset(), {"revision": "fixed"}, tmp_path / "one")
    two = prepare(list(reversed(dataset())), {"revision": "fixed"}, tmp_path / "two")
    assert one == two
    assert len([s for s in one["samples"] if s["split"] == "dev"]) == 2
    assert len(list(jobs(one, "test"))) == 32
    assert len(list(jobs(one, "test", repeats=True))) == 8
    assert "SECRET_REFERENCE" not in json.dumps(one)
    assert len(read_json(tmp_path / "one/references.json")) == 10
    one["samples"][0]["question"] = "tampered"
    write_json(tmp_path / "one/manifest.json", one)
    with pytest.raises(ValueError, match="hash"):
        load_manifest(tmp_path / "one/manifest.json")


def test_seed_copies_do_not_share_jit_mutations(tmp_path):
    seed = tmp_path / "seed"
    (seed / "data").mkdir(parents=True)
    (seed / "data/index").write_text("seed")
    write_json(seed / "sealed.json", {"data_hash": tree_hash(seed / "data")})
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    restore_seed(seed, a)
    (a / "data/index").write_text("JIT mutation")
    restore_seed(seed, b)
    assert (b / "data/index").read_text() == "seed"
    assert (seed / "data/index").read_text() == "seed"
    (seed / "data/index").write_text("tampered")
    with pytest.raises(ValueError): restore_seed(seed, tmp_path / "third")


def test_jit_pilot_holds_out_questions_and_rotates_group_order(tmp_path):
    historic = prepare(dataset(), {"revision": "fixed"}, tmp_path / "historic")
    repos = {sample["repo"] for sample in historic["samples"]}
    pilot = prepare_jit(dataset(), {"revision": "fixed"}, historic, tmp_path / "pilot", repos)
    again = prepare_jit(list(reversed(dataset())), {"revision": "fixed"}, historic, tmp_path / "again", repos)
    assert pilot == again
    assert {sample["id"] for sample in pilot["samples"]}.isdisjoint(s["id"] for s in historic["samples"])
    assert "SECRET_REFERENCE" not in json.dumps(pilot)
    assert [job["variant"] for job in jobs(pilot, "dev", variants=pilot["variants"])] == list("CFGFGC")


def test_budget_unknown_usage_is_not_free_and_resume_is_durable(tmp_path):
    ledger = Ledger(tmp_path / "usage.db", limit=1)
    call = ledger.reserve("a", "dev", .75, {})
    ledger.finish(call, {"status": "error"})
    with pytest.raises(BudgetExceeded):
        Ledger(tmp_path / "usage.db", limit=1).reserve("b", "dev", .3, {})
    assert ledger.records()[0]["cost_cny"] == .75
    ledger.finish(call, {"usage_kind": "actual"}, .1)
    ledger.reserve("b", "dev", .3, {})
    assert len(ledger.records()) == 2


def test_meter_blocks_request_before_network_and_records_actual_usage(tmp_path):
    ledger = Ledger(tmp_path / "usage.db", limit=.000001)
    rate = {"input_cny_per_million": 1, "output_cny_per_million": 1,
            "source": "test-only", "verified_at": "test-only"}
    async def forbidden(**kwargs): raise AssertionError("Network must not start")
    call = Meter(ledger, "job", "dev", {"generation": rate}).wrap(forbidden, "generation")
    with pytest.raises(BudgetExceeded):
        asyncio.run(call(model="fake", messages=[{"role": "user", "content": "hi"}], max_tokens=5))
    ledger.limit = 1
    async def fake(**kwargs):
        return NS(id="resp", model="pinned", choices=[], usage=NS(prompt_tokens=10, completion_tokens=5))
    call = Meter(ledger, "job", "dev", {"generation": rate}).wrap(fake, "generation")
    asyncio.run(call(model="fake", messages=[], max_tokens=5))
    row = ledger.records()[0]
    assert row["input_tokens"] == 10 and row["cost_cny"] == pytest.approx(.000015)
    assert row["returned_model"] == "pinned"


def test_jev_meter_records_actual_usage_without_storing_request_state(tmp_path):
    ledger = Ledger(tmp_path / "usage.db", limit=1)
    rate = {"input_cny_per_million": 1, "output_cny_per_million": 0,
            "source": "test-only", "verified_at": "test-only"}

    async def fake(**kwargs):
        return {"model": "jev-pinned", "usage": {"input_tokens": 40, "output_tokens": 8}}

    call = Meter(ledger, "job", "dev", {"jev": rate}).wrap(fake, "jev")
    asyncio.run(call(model="jev-pinned", state={"question": "do not persist this"}, questions={"route": {}}))
    row = ledger.records()[0]
    assert row["input_tokens"] == 40 and row["output_tokens"] == 8
    assert row["returned_model"] == "jev-pinned" and row["cost_cny"] == pytest.approx(.000040)
    assert row["request_config"] == {"model": "jev-pinned"}


def test_final_answer_excludes_tool_round_and_jit_is_ordered(monkeypatch):
    from app.services import chat_service as chat
    from app.utils.session import clear_conversation_memory
    responses = iter(["Need files <tool_code>b.py</tool_code><tool_code>a.py</tool_code><tool_code>b.py</tool_code>",
                      "Final answer supported by code."])
    requests = []
    async def create(**kwargs):
        requests.append(kwargs)
        text = next(responses)
        async def chunks():
            for part in (text[:15], text[15:]):
                yield NS(choices=[NS(delta=NS(content=part))])
        return chunks()
    class Store:
        repo_url = "https://github.com/owner/repo"
        global_context = {"summary": "seed", "file_tree": "a.py\nb.py"}
        indexed_files = {"a.py", "b.py"}
        async def search_hybrid(self, *args, **kwargs): return []
        def get_documents_by_file(self, path): return [{"file": path, "content": "evidence"}]
    monkeypatch.setattr(chat, "client", NS(chat=NS(completions=NS(create=create))))
    monkeypatch.setattr(chat.store_manager, "get_store", lambda sid: Store())
    clear_conversation_memory("bench-final")
    async def run():
        return [text async for text in chat.process_chat_stream("Explain code", "bench-final",
            config=chat.ChatConfig(rewrite_query=False))]
    stream = "".join(asyncio.run(run()))
    result = chat.get_eval_data("bench-final")
    assert "Need files" in stream  # HTTP stream compatibility
    assert result.answer == "Final answer supported by code."
    assert [c["file"] for c in result.tool_calls] == ["b.py", "a.py"]
    assert len(result.rounds) == 2 and result.status == "completed"


def test_static_rag_disables_tools_in_first_prompt(monkeypatch):
    from app.services import chat_service as chat
    from app.utils.session import clear_conversation_memory
    async def create(**kwargs):
        assert "DO NOT use <tool_code>" in kwargs["messages"][0]["content"]
        async def chunks(): yield NS(choices=[NS(delta=NS(content="answer"))])
        return chunks()
    class Store:
        global_context = {}
        async def search_hybrid(self, *args, **kwargs): return []
    monkeypatch.setattr(chat, "client", NS(chat=NS(completions=NS(create=create))))
    monkeypatch.setattr(chat.store_manager, "get_store", lambda sid: Store())
    clear_conversation_memory("static-test")
    async def run():
        async for _ in chat.process_chat_stream("question", "static-test", config=chat.ChatConfig(max_jit_rounds=0, rewrite_query=False)):
            pass
    asyncio.run(run())
    assert chat.get_eval_data("static-test").answer == "answer"


def test_missing_results_are_in_denominator_and_prevent_comparison(tmp_path):
    manifest = prepare(dataset(), {}, tmp_path)
    write_json(tmp_path / "schedule_test.json", list(jobs(manifest, "test")))
    summary = generate_report(tmp_path)
    assert not summary["ready_for_comparison"]
    assert summary["groups"]["C"]["expected"] == 8
    assert summary["groups"]["C"]["statuses"] == {"missing": 8}
    assert summary["comparisons"] == {}


def test_bootstrap_pairs_same_questions_and_ignores_repeats():
    rows = [{"repo": repo, "sample_id": "one", "variant": variant, "repeat": 0, "score": score}
            for repo in ("a", "b") for variant, score in (("B", 20), ("C", 25))]
    rows.append({"repo": "a", "sample_id": "one", "variant": "C", "repeat": 1, "score": 50})
    result = paired_bootstrap(rows, "C", "B")
    assert result == {"paired_questions": 2, "repositories": 2, "delta": 5, "ci95": [5, 5]}


def test_e_schedule_contains_only_full_static_jobs(tmp_path):
    manifest = prepare(dataset(), {"revision": "fixed"}, tmp_path / "e-schedule")
    scheduled = list(jobs(manifest, "test", variants=("E",)))
    assert scheduled and {j["variant"] for j in scheduled} == {"E"}


def test_stream_is_not_replayed_after_partial_output():
    from app.utils.llm_providers.base import _CompletionsNamespace
    attempts = []
    class Provider:
        async def chat_completions_create_stream(self, **kwargs):
            attempts.append(1)
            yield NS(choices=[NS(delta=NS(content="partial"))])
            raise ConnectionError("injected after first token")
    async def run():
        chunks = []
        with pytest.raises(ConnectionError):
            async for chunk in _CompletionsNamespace(Provider())._create_stream_with_retry(
                    messages=[], model="fake", temperature=0, max_tokens=10, timeout=1):
                chunks.append(chunk)
        assert len(chunks) == 1
    asyncio.run(run())
    assert len(attempts) == 1


def test_isolated_worker_with_real_sdk_and_qdrant_mock_http_only(tmp_path):
    """Exercise production analysis/chat in fresh interpreters, with no network or paid APIs."""
    import os
    import shutil
    import sys
    from evaluation.benchmark.runner import ROOT
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "a.py").write_text('def primary_entry_point():\n    """Entry point with enough text to become a chunk."""\n    return 42\n')
    (repo / "b.py").write_text('def secondary_helper():\n    """A second function available for just in time reading."""\n    return 43\n')
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "fixture")
    revision = git(repo, "rev-parse", "HEAD")
    rate = {"input_cny_per_million": 1, "output_cny_per_million": 1,
            "source": "mock-http-test", "verified_at": "test"}
    base = {"ledger": str(tmp_path / "ledger.db"), "budget_cny": 1,
            "rates": {k: rate for k in ("generation", "embedding", "judge")},
            "timeout_seconds": 30, "chat_config": {"rewrite_query": True},
            "repo_root": str(repo), "repo": "owner/repo", "commit_id": revision}
    code = r'''
import json, sys, httpx, openai
real = openai.AsyncOpenAI
def handle(request):
    body = json.loads(request.content)
    if request.url.path.endswith('/embeddings'):
        return httpx.Response(200, json={'object':'list','model':'BAAI/bge-m3',
            'data':[{'index':i,'object':'embedding','embedding':[.01]*1024} for i in range(len(body['input']))],
            'usage':{'prompt_tokens':20,'total_tokens':20}})
    messages = body['messages']
    content = '\n'.join(m['content'] for m in messages)
    if 'MOST CRITICAL' in content:
        text = '[]' if "['a.py']" in content else '["a.py"]'
    elif 'Convert the user' in content:
        text = '["helper"]'
    elif body.get('stream') and 'Senior GitHub Repository Analyst' in content:
        text = 'Final supported answer.' if 'FINAL ROUND' in content or len(messages)>2 else '<tool_code>b.py</tool_code>'
    else:
        text = 'Architecture report from source.'
    if body.get('stream'):
        chunks = [{'id':'mock','object':'chat.completion.chunk','created':1,'model':'mock-model',
                   'choices':[{'index':0,'delta':{'content':text},'finish_reason':None}]},
                  {'id':'mock','object':'chat.completion.chunk','created':1,'model':'mock-model',
                   'choices':[],'usage':{'prompt_tokens':30,'completion_tokens':8,'total_tokens':38}}]
        data = ''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n'
        return httpx.Response(200, content=data.encode(),
                              headers={'content-type':'text/event-stream'})
    return httpx.Response(200, json={'id':'mock','object':'chat.completion','created':1,'model':'mock-model',
        'choices':[{'index':0,'message':{'role':'assistant','content':text},'finish_reason':'stop'}],
        'usage':{'prompt_tokens':30,'completion_tokens':8,'total_tokens':38}})
def factory(*args, **kwargs):
    kwargs['http_client'] = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    return real(*args, **kwargs)
openai.AsyncOpenAI = factory
from evaluation.benchmark.worker import main
main()
'''
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHON_DOTENV_DISABLED="1", LLM_PROVIDER="deepseek",
               DEEPSEEK_API_KEY="fake", SILICON_API_KEY="fake", LANGFUSE_ENABLED="false", AUTO_EVAL_ENABLED="false")
    def execute(work, payload):
        work.mkdir(exist_ok=True)
        write_json(work / "job.json", payload)
        proc = subprocess.run([sys.executable, "-c", code, str(work / "job.json"), str(work / "result.json")],
                              env=env, cwd=work, capture_output=True, text=True, timeout=45)
        assert proc.returncode == 0, proc.stderr
        result = read_json(work / "result.json")
        assert result["status"] == "completed", (result, proc.stdout, proc.stderr)
        return result
    seed = tmp_path / "seed"
    execute(seed, dict(base, kind="seed", job_id="seed", phase="seed"))
    original_hash = tree_hash(seed / "data")
    full_seed = tmp_path / "full-seed"
    full_seed.mkdir()
    shutil.copytree(seed / "data", full_seed / "data")
    full_result = execute(full_seed, dict(base, kind="full_seed", job_id="full-seed", phase="full_seed"))
    assert set(full_result["indexed_files"]) == {"a.py", "b.py"}
    assert read_json(full_seed / "full_index_checkpoint.json")["status"] == "completed"
    for variant in ("A", "B", "C", "D", "E"):
        work = tmp_path / variant
        work.mkdir()
        if variant != "A":
            shutil.copytree((full_seed if variant == "E" else seed) / "data", work / "data")
        result = execute(work, dict(base, kind="question", job_id=variant, phase="dev",
                                   variant=variant, question="Explain the helper"))
        if variant == "A":
            assert result["answer"] == "Architecture report from source."
            assert result["reads"] == []
            assert not (work / "data" / "qdrant_db").exists()
        else:
            assert result["answer"] == "Final supported answer."
            assert result["jit_rounds"] == (0 if variant in ("B", "E") else 1)
    assert tree_hash(seed / "data") == original_hash
    calls = Ledger(tmp_path / "ledger.db").records()
    assert calls and all(c["usage_kind"] == "actual" for c in calls)
