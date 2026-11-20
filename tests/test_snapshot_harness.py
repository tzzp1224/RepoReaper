import asyncio
import json
import subprocess
from types import SimpleNamespace as NS

import httpx
import pytest
from qdrant_client import AsyncQdrantClient

from app.services import agent_service, chat_service, jev_gate, repo_index, vector_service
from app.services.github_service import GitHubService
from app.services.repo_explorer import evidence_gap, explore
from app.services.repo_tool_router import investigate_with_jev
from app.services.repository_evidence import RepositoryEvidence, select_skill
from app.services.repository_source import GitSnapshotSource
from app.storage import qdrant_store
from app.storage.runtime_store import RuntimeTraceStore
from app.storage.snapshot_leases import collection_lease, pin_snapshot
from app.snapshot_gc import collect
from app.utils.github_client import GitHubRepo


REPO_URL = "https://github.com/owner/repo"


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def commit(root, message):
    git(root, "add", "-A")
    git(root, "commit", "-qm", message)
    return git(root, "rev-parse", "HEAD")


def test_explorer_uses_fixed_commit_grep_semantic_and_source_ranges(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "xarray/backends").mkdir(parents=True)
    (tmp_path / "xarray/backends/rasterio_.py").write_text("def open_rasterio():\n    return 'committed'\n")
    (tmp_path / "xarray/backends/api.py").write_text("def register():\n    return 'wrapper'\n")
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples/rasterio_conversion.py").write_text("open_rasterio()\n")
    (tmp_path / "MANIFEST.in").write_text("include xarray/backends/*\n")
    (tmp_path / "tox.ini").write_text("[tox]\nenvlist = py\n")
    (tmp_path / ".env").write_text("open_rasterio=private\n")
    sha = commit(tmp_path, "source")
    (tmp_path / "xarray/backends/rasterio_.py").write_text("def open_rasterio():\n    return 'dirty worktree'\n")

    async def run():
        source = GitSnapshotSource(tmp_path, "pydata/xarray", sha)
        tools = RepositoryEvidence(source)
        query = "How does xarray implement the rasterio backend?"
        skill = select_skill(query)
        assert (await tools.find_paths(query, skill, []))[0] == "xarray/backends/rasterio_.py"
        query_with_noise = "Where is the rasterio conversion implemented in xarray?"
        paths = await tools.find_paths(query_with_noise, skill, [])
        assert paths[0] == "xarray/backends/rasterio_.py" and "MANIFEST.in" not in paths
        ci_query = "How does the CI pipeline execute testing?"
        assert (await tools.find_paths(ci_query, select_skill(ci_query), []))[0] == "tox.ini"
        initial = [await tools.read("xarray/backends/api.py", query, [])]
        assert evidence_gap("Where is `open_rasterio` defined?", initial, skill, False) == "unresolved_identifier"

        async def semantic(text):
            assert text == "rasterio entry point"
            return [{"file": "xarray/backends/rasterio_.py", "metadata": {"start_line": 1}}]

        async def plan(messages):
            payload = json.loads(messages[-1]["content"])
            if not payload["previous_observations"]:
                return json.dumps({"calls": [{"tool": "grep", "query": "open_rasterio"},
                                             {"tool": "search_semantic", "query": "rasterio entry point"},
                                             {"tool": "read_spans", "path": "../out.py"}]})
            assert ".env" not in json.dumps(payload) and "dirty worktree" not in json.dumps(payload)
            assert "xarray/backends/rasterio_.py" in json.dumps(payload)
            return json.dumps({"calls": [{"tool": "read_spans", "path": "xarray/backends/rasterio_.py",
                                          "start_line": 1, "end_line": 2}]})

        result = await explore(query, "test gap", tools, initial, semantic, plan, timeout=5)
        assert {call["tool"] for call in result.calls} == {"grep", "search_semantic", "read_spans"}
        assert result.reads[0].spans[0]["content"] == "def open_rasterio():\n    return 'committed'"
        assert result.reads[0].spans[0]["metadata"]["commit"] == sha

        async def invalid_plan(_):
            return '{"calls":[{"tool":"read_spans","path":"../private.txt","start_line":1,"end_line":2}]}'

        rejected = await explore(query, "test", tools, initial, semantic, invalid_plan, timeout=3)
        assert rejected.status == "invalid_plan" and not rejected.calls

    asyncio.run(run())


def test_jev_chooses_tools_and_source_targets_in_the_same_request(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "known.py").write_text("placeholder = True\n")
    (tmp_path / "routes.py").write_text("def plan_route():\n    return 'pinned implementation'\n")
    (tmp_path / "fallbacks.py").write_text("def failover():\n    return 'fallback implementation'\n")
    revision = commit(tmp_path, "initial tools")
    (tmp_path / "routes.py").write_text("def plan_route():\n    return 'dirty worktree'\n")
    monkeypatch.setenv("TYPESAFE_API_KEY", "fixture")
    requests = []

    def answer(choice):
        return {"choice": choice, "probabilities": {choice: 0.98}, "confidence": 0.9}

    async def classify(*, model, state, questions):
        assert state["commit"] == revision
        assert "pinned implementation" not in json.dumps(state)
        actions = questions["action"]["criteria"]
        action = ("grep", "search_semantic", "read_spans", "read_spans")[len(requests)]
        assert action in actions
        if not requests:
            assert set(actions) == {"grep", "search_semantic", "read_spans"}
            assert state["candidate_arguments"]["grep"]["literal_1"] == "plan_route"
            assert state["candidate_arguments"]["read_spans"] == {"file_1": "known.py"}
        requests.append(questions)
        answers = {"action": answer(action)}
        if action == "grep":
            answers[action] = answer("literal_1")
        if action == "read_spans":
            prefix = "routes.py" if len(requests) == 3 else "fallbacks.py"
            options = state["candidate_arguments"][action]
            assert questions[action]["criteria"] == dict.fromkeys(options)
            answers[action] = answer(next(key for key, description in options.items() if description.startswith(prefix)))
        return {"answers": answers}

    monkeypatch.setattr(jev_gate, "request_choice", classify)

    async def semantic(text):
        return [{"file": "fallbacks.py", "metadata": {"start_line": 1}}]

    async def run():
        tools = RepositoryEvidence(GitSnapshotSource(tmp_path, "owner/repo", revision))
        question = "Where does `plan_route` handle this behavioral fallback?"
        result = await investigate_with_jev(question, tools, ["known.py"], semantic, timeout=5, choice_timeout=1)
        assert [call["tool"] for call in result.calls] == ["grep", "search_semantic", "read_spans", "read_spans"]
        assert [r.path for r in result.reads] == ["routes.py", "fallbacks.py"]
        assert "pinned implementation" in result.reads[0].spans[0]["content"]
        assert all(call["commit"] == revision for call in result.calls)
        forced = await jev_gate.choose_tool({}, {"read_spans": "Read source"},
                                            {"read_spans": {"file_1": "known.py"}}, timeout=1)
        assert forced == jev_gate.ToolSelection("read_spans", "file_1") and len(requests) == 4

    asyncio.run(run())


def test_incremental_analysis_and_snapshot_chat(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Test")
    (root / ".github" / "workflows").mkdir(parents=True)
    workflow = root / ".github" / "workflows" / "ci.yml"
    workflow.write_text("name: CI\njobs:\n  lint:\n    runs-on: ubuntu-latest\n")
    (root / "answer.py").write_text("def changed():\n    return 1\n\ndef stable_symbol():\n    return 42\n")
    (root / "gone.py").write_text("def gone():\n    return 8\n")
    (root / "move.py").write_text("def relocated():\n    return 7\n")
    first = commit(root, "initial")
    state = NS(head=first, fail_embeddings=False, lose_write_ack=False, checked_writer_lease=False)
    calls = NS(embedded=[], decisions=0, reports=0, answer_prompts=[], refs=[], sources=[])

    class Snapshots:
        async def open_snapshot(self, url, revision=None):
            assert url == REPO_URL
            calls.refs.append(revision)
            source = GitSnapshotSource(root, "owner/repo", revision or state.head)
            calls.sources.append(source)
            return source

    class Embeddings:
        def embedding_input(self, content):
            return content.replace("\n", " ").strip()

        async def embed_batch(self, contents):
            calls.embedded.extend(contents)
            return [] if state.fail_embeddings else [[1.0, 0.0]] * len(contents)

        async def embed_text(self, _):
            return [1.0, 0.0]

    async def stream(text):
        yield NS(choices=[NS(delta=NS(content=text))])

    async def analysis_model(**kwargs):
        if kwargs.get("stream"):
            calls.reports += 1
            return stream("# Repository report")
        calls.decisions += 1
        return NS(choices=[NS(message=NS(content='["answer.py", "gone.py", "move.py"]'))])

    async def chat_model(**kwargs):
        assert kwargs.get("stream"), "Exact evidence should skip query rewrite and planner calls"
        calls.answer_prompts.append(kwargs["messages"])
        return stream("Lint runs in the CI workflow [E1].")

    class Traces:
        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    monkeypatch.setattr(vector_service.config, "context_dir", str(tmp_path))
    monkeypatch.setattr(vector_service.config, "embedding_dimensions", 2)
    monkeypatch.setattr(vector_service, "get_embedding", Embeddings)
    monkeypatch.setattr(repo_index, "get_embedding", Embeddings)
    manager = vector_service.VectorStoreManager()
    traces = Traces()
    memory = RuntimeTraceStore(str(tmp_path / "runtime.db"))
    for service, model in ((agent_service, analysis_model), (chat_service, chat_model)):
        monkeypatch.setattr(service, "store_manager", manager)
        monkeypatch.setattr(service, "get_github_service", Snapshots)
        monkeypatch.setattr(service, "tracing_service", traces)
        monkeypatch.setattr(service, "client", NS(chat=NS(completions=NS(create=model))))
    monkeypatch.setattr(chat_service, "runtime_trace_store", memory)
    monkeypatch.setattr(agent_service.agent_config, "max_rounds", 1)

    original_add = qdrant_store.QdrantVectorStore.add_documents

    async def add_documents(backend, documents, embeddings):
        if not state.checked_writer_lease:
            with pytest.raises(TimeoutError):
                async with collection_lease(tmp_path, backend.collection_name, exclusive=True, timeout=0):
                    pytest.fail("A writer's staged document must be protected from collection")
            state.checked_writer_lease = True
        added = await original_add(backend, documents, embeddings)
        return 0 if state.lose_write_ack else added

    monkeypatch.setattr(qdrant_store.QdrantVectorStore, "add_documents", add_documents)

    async def run():
        qdrant = AsyncQdrantClient(":memory:")

        async def local_client(_):
            return qdrant

        monkeypatch.setattr(qdrant_store, "get_shared_client", local_client)
        monkeypatch.setenv("QDRANT_VECTOR_SIZE", "2")
        store = manager.get_store("test-session")

        async def analyze():
            return [json.loads(event) async for event in agent_service._agent_stream_inner(
                REPO_URL, "test-session", "en", False, "test", "trace", 0,
            )]

        try:
            first_events = await analyze()
            assert first_events[-1]["step"] == "finish", first_events[-1]
            assert state.checked_writer_lease
            old_manifest = store.load_context()["snapshot_index"]
            old_view = store.snapshot_index(old_manifest)
            assert old_manifest["revision"] == first
            original_count = len(calls.embedded)
            same = await analyze()
            assert same[-1]["commit"] == first
            assert len(calls.embedded) == original_count
            assert calls.reports == calls.decisions == 1

            (root / "answer.py").write_text("def changed():\n    return 2\n\ndef stable_symbol():\n    return 42\n")
            (root / "move.py").rename(root / "renamed.py")
            (root / "gone.py").unlink()
            second = state.head = commit(root, "modify, rename, delete")
            update = await analyze()
            assert update[-1]["step"] == "finish", update[-1]
            new_manifest = store.load_context()["snapshot_index"]
            assert new_manifest["revision"] == second
            assert set(new_manifest["files"]) == {"answer.py", "renamed.py"}
            assert calls.decisions == 1
            assert len(calls.embedded) == original_count + 1
            assert set(old_manifest["files"]["answer.py"]["docs"]) & set(new_manifest["files"]["answer.py"]["docs"])
            old_results = await old_view.search("gone", top_k=10)
            assert "gone.py" in {hit["file"] for hit in old_results}
            new_results = await store.snapshot_index(new_manifest).search("gone", top_k=10)
            assert {hit["file"] for hit in new_results} <= {"answer.py", "renamed.py"}

            (root / "answer.py").write_text("def changed():\n    return 3\n")
            workflow.write_text("name: Later workflow\n")
            state.head = commit(root, "new unpublished head")
            state.fail_embeddings = True
            failed = await analyze()
            assert failed[-1]["step"] == "error"
            assert store.load_context()["snapshot_index"] == new_manifest

            output = "".join([fragment async for fragment in chat_service.process_chat_stream(
                "How does the CI workflow run lint?", "test-session", repo_url=REPO_URL,
                run_id="run-1", conversation_id="browser-1",
            )])
            data = chat_service.get_eval_data("run-1")
            assert calls.refs[-1] == data.revision == second
            assert data.status == "completed" and len(calls.answer_prompts) == 1
            assert "runs-on: ubuntu-latest" in data.retrieved_context and "Later workflow" not in data.retrieved_context
            assert f"/blob/{second}/.github/workflows/ci.yml#L1-L4" in output
            assert memory.recent_turns("browser-1", "owner/repo", second)
            assert not memory.recent_turns("browser-2", "owner/repo", second)
            assert not memory.recent_turns("browser-1", "owner/repo", first)

            routed_searches = []

            async def track_search(*args):
                routed_searches.append(args)
                return []

            async def classify(*, model, state, questions):
                assert state["commit"] == second
                choices = questions["action"]["criteria"]
                if "answer" in choices:
                    return {"answers": {"action": {"choice": "answer", "probabilities": {"answer": 1}, "confidence": 1}}}
                assert set(choices) >= {"read_spans", "search_semantic"}
                candidates = state["candidate_arguments"]["read_spans"]
                target = next(key for key, path in candidates.items() if path == ".github/workflows/ci.yml")
                return {"answers": {"action": {"choice": "read_spans", "probabilities": {"read_spans": 1}, "confidence": 1},
                                    "read_spans": {"choice": target}}}

            with monkeypatch.context() as routed:
                routed.setenv("JEV_MODE", "active")
                routed.setenv("TYPESAFE_API_KEY", "fixture")
                routed.setattr(jev_gate, "request_choice", classify)
                routed.setattr(repo_index.SnapshotIndex, "search", track_search)
                routed_output = "".join([fragment async for fragment in chat_service.process_chat_stream(
                    "How does the CI workflow run lint?", "test-session", repo_url=REPO_URL,
                    run_id="run-jev", config=chat_service.ChatConfig(exploration_mode="jev"),
                )])
            routed_data = chat_service.get_eval_data("run-jev")
            assert routed_data.status == "completed" and len(calls.answer_prompts) == 2 and not routed_searches
            assert routed_data.tool_calls[0]["tool"] == "read_spans" and routed_data.revision == second
            assert "runs-on: ubuntu-latest" in routed_data.retrieved_context
            assert f"/blob/{second}/.github/workflows/ci.yml#L1-L4" in routed_output

            matched = await calls.sources[-1].find_literal_paths(["stable_symbol"])
            assert matched == ["answer.py"]
            tools = RepositoryEvidence(calls.sources[-1])
            assert (await tools.read(".github/workflows", "CI", [])).status == "directory"
            assert (await tools.read("../outside", "CI", [])).status == "invalid_path"

            state.fail_embeddings = False
            state.lose_write_ack = True
            assert (await analyze())[-1]["step"] == "error"
            assert store.load_context()["snapshot_index"] == new_manifest
            writes_before_retry = len(calls.embedded)
            state.lose_write_ack = False
            assert (await analyze())[-1]["commit"] == state.head
            assert len(calls.embedded) == writes_before_retry

            git(root, "checkout", "-q", "--detach", first)
            (root / "new.py").write_text("def added():\n    return 9\n")
            state.head = commit(root, "replacement branch history")
            replaced = await analyze()
            assert replaced[-1]["commit"] == state.head
            replacement = store.load_context()["snapshot_index"]
            assert "renamed.py" not in replacement["tree"] and "new.py" in replacement["files"]
            assert "renamed.py" in {hit["file"] for hit in new_results}

            backend = store.snapshot_index(replacement).backend
            expired = set(new_manifest["files"]["renamed.py"]["docs"])
            live = set(replacement["files"]["answer.py"]["docs"])
            assert expired <= (await backend.retrieve_documents(list(expired))).keys()
            async with pin_snapshot(store):
                held = await collect(REPO_URL, apply=True, grace_seconds=0, keep=0)
                assert held[replacement["namespace"]]["status"] == "in_flight"
                assert expired <= (await backend.retrieve_documents(list(expired))).keys()
            applied = await collect(REPO_URL, apply=True, grace_seconds=0, keep=0)
            assert applied[replacement["namespace"]]["status"] == "collected"
            assert not await backend.retrieve_documents(list(expired))
            assert live <= (await backend.retrieve_documents(list(live))).keys()
        finally:
            chat_service.clear_eval_data("run-1")
            chat_service.clear_eval_data("run-jev")
            await qdrant.close()

    asyncio.run(run())


def test_github_fallback_keeps_requested_revision_and_blob():
    revision, blob = "a" * 40, "b" * 40

    class Mirror:
        def local_git_dir(self, _):
            raise OSError("No local mirror")

    class API:
        async def get_revision_tree(self, repo, ref):
            assert repo == GitHubRepo("owner", "repo")
            assert ref == revision
            return revision, "c" * 40

        async def get_tree_entries(self, repo, tree):
            assert tree == "c" * 40
            return [{"path": ".github/workflows/ci.yml", "type": "blob", "mode": "100644", "sha": blob},
                    {"path": "link", "type": "blob", "mode": "120000", "sha": blob}]

        async def get_blob(self, repo, sha):
            assert sha == blob
            return b"jobs: {}\n"

    async def run():
        source = await GitHubService(client=API(), mirror_store=Mirror()).open_snapshot(REPO_URL, revision)
        assert source.revision == revision
        assert await source.read_file(".github/workflows/ci.yml") == "jobs: {}\n"
        assert await source.read_file("link") is None

    asyncio.run(run())


def test_jev_only_accepts_a_valid_high_confidence_batch(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setenv("JEV_MODE", "shadow")
    choices = {"valid": "batch_2", "invalid": "unknown"}
    state = NS(which="valid")
    real_client = httpx.AsyncClient
    created = []

    async def handle(request):
        assert request.headers["Authorization"] == "Bearer test"
        payload = json.loads(request.content)
        assert payload["questions"]["batch"]["type"] == "choice"
        assert set(payload["questions"]["batch"]["criteria"]) == {"batch_1", "batch_2", "none"}
        choice = choices[state.which]
        return httpx.Response(200, json={"answers": {"batch": {
            "choice": choice, "confidence": 0.9, "probabilities": {choice: 0.93},
        }}})

    def client(**kwargs):
        instance = real_client(transport=httpx.MockTransport(handle), **kwargs)
        created.append(instance)
        return instance

    monkeypatch.setattr(jev_gate.httpx, "AsyncClient", client)
    batches = {"batch_1": ["alpha.py"], "batch_2": ["beta.py"]}

    async def run():
        async with jev_gate.connection_pool():
            assert await jev_gate.choose_batch("where?", [], batches) == "batch_2"
            state.which = "invalid"
            assert await jev_gate.choose_batch("where?", [], batches) is None
            assert len(created) == 1 and not created[0].is_closed
        assert created[0].is_closed

    asyncio.run(run())
