"""Orbit interface against a real localhost broker + endpoint.

Exercises the actual client path -- websocket, plugin sessions, pushed status
events, PSI/J log tailing by byte offset, and cancellation -- with no HPC
allocation. Skipped unless the Orbit CLI scripts are importable and runnable.

Run explicitly:
    pytest tests/test_orbit_local.py -q
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from designagent.tasks.base import TaskSpec, TaskState
from designagent.tasks.hpc.base import drain_logs

pytestmark = pytest.mark.live


def _orbit_available() -> bool:
    try:
        import radical.orbit  # noqa: F401
    except ImportError:
        return False
    from designagent.tasks.hpc.local_orbit import _script

    return bool(_script("radical-orbit-broker.py") and _script("radical-orbit-endpoint.py"))


pytest.importorskip("radical.orbit", reason="radical.orbit is not installed")
if not _orbit_available():
    pytest.skip("Orbit CLI scripts not found", allow_module_level=True)


@pytest.fixture
async def orbit(tmp_path):
    """A connected OrbitInterface over a throwaway local stack."""
    from designagent.tasks.hpc.local_orbit import LocalOrbitStack
    from designagent.tasks.hpc.orbit import OrbitInterface

    stack = LocalOrbitStack(work_dir=tmp_path / "orbit")
    try:
        await stack.start(timeout=90)
    except Exception as exc:
        pytest.skip(f"could not start a local Orbit stack: {exc}")

    interface = OrbitInterface(
        broker_url=stack.broker_url,
        endpoint="local",
        # The broker serves TLS with a throwaway self-signed cert, so pin it.
        cert=str(stack.cert),
        rhapsody_backends=["concurrent"],
        poll_interval=1.0,
        connect_timeout=45.0,
    )
    try:
        await interface.connect()
    except Exception as exc:
        await stack.stop()
        pytest.skip(f"could not connect to the local Orbit stack: {exc}")

    yield interface
    await interface.close()
    await stack.stop()


async def test_connects_and_finds_the_endpoint(orbit):
    assert orbit.connected
    assert orbit.capabilities.supports_push_events
    assert orbit.capabilities.supports_log_stream


async def test_executable_task_runs_and_returns_output(orbit):
    """Needs rhapsody, so it needs a host Orbit will load rhapsody on.

    The dev stack's own host decides that: `is_enabled` admits the `compute`
    and `standalone` roles only. On a VM or a laptop there is no batch system,
    so the role is `standalone` and it loads; run the same `-m live` tier on an
    HPC login node and the role is `login`, where it is vetoed (backlog D4).
    """
    if not orbit.capabilities.supports_function_tasks:
        pytest.skip(
            "the local stack's endpoint serves no rhapsody plugin: this host "
            "is a login node, where Orbit's role gate vetoes it (D4)"
        )

    spec = TaskSpec(
        name="echo",
        kind="function",
        params={"task_dict": {"executable": "/bin/echo", "arguments": ["hello-orbit"]}},
    )
    handle = await orbit.submit(spec)
    assert not handle.done  # submission returned immediately

    result = await asyncio.wait_for(handle.future, timeout=120)
    assert result["state"] == "DONE"
    assert result["exit_code"] in (0, None)
    assert "hello-orbit" in (result.get("stdout") or "")
    assert await orbit.status(handle) is TaskState.DONE


async def test_psij_job_streams_logs_by_offset(orbit):
    """The one place either backend can tail a running job."""
    job = {
        "executable": "/bin/bash",
        "arguments": [
            "-c",
            "for i in 1 2 3; do echo line-$i; sleep 1; done",
        ],
        "duration_sec": 120,
    }
    spec = TaskSpec(name="tail", kind="job", params={"job_spec": job, "executor": "local"})
    handle = await orbit.submit(spec)

    chunks: list[str] = []

    async def collect(_handle, text):
        chunks.append(text)

    drain = asyncio.ensure_future(drain_logs(orbit, handle, collect, interval=0.5))
    result = await asyncio.wait_for(handle.future, timeout=180)
    drain.cancel()

    assert result["state"] == "DONE"
    combined = "".join(chunks) + (result.get("stdout") or "")
    assert "line-1" in combined and "line-3" in combined
    # Offsets advanced, so the tail was incremental rather than refetched whole.
    assert handle.log_offset > 0


async def test_failing_job_is_reported_as_failed(orbit):
    spec = TaskSpec(
        name="fail",
        kind="job",
        params={
            "job_spec": {"executable": "/bin/bash", "arguments": ["-c", "exit 3"]},
            "executor": "local",
        },
    )
    handle = await orbit.submit(spec)
    with pytest.raises(Exception):
        await asyncio.wait_for(handle.future, timeout=120)
    assert handle.state is TaskState.FAILED
    assert handle.error


async def test_cancel_stops_a_running_job(orbit):
    spec = TaskSpec(
        name="sleeper",
        kind="job",
        params={
            "job_spec": {
                "executable": "/bin/sleep",
                "arguments": ["120"],
                "duration_sec": 300,
            },
            "executor": "local",
        },
    )
    handle = await orbit.submit(spec)
    await asyncio.sleep(3)  # let it actually start

    assert await orbit.cancel(handle) is True
    assert handle.state is TaskState.CANCELED


async def test_manager_routes_through_orbit(orbit, tmp_path):
    """The manager prefers the hpc interface when one is connected."""
    from designagent.config import Settings
    from designagent.lake.store import DesignHistory
    from designagent.tasks.manager import TaskManager

    history = DesignHistory(Settings(data_dir=tmp_path / "lake", anthropic_api_key=""))
    manager = TaskManager(hpc=orbit, history=history)
    try:
        assert manager.hpc_available
        name, _ = manager.interface_for(TaskSpec(name="proteinmpnn"))
        assert name == "hpc"
    finally:
        history.close()


async def test_job_stdout_is_not_duplicated_by_the_log_drain(orbit):
    """A finished job's stdout holds each line exactly once.

    The regression this pins: `drain_logs` and `_poll_job` tailed the same file
    from independent cursors and both appended to `handle.log_tail`, which
    `_finish_job` then returned as the job's output. Every existing assertion
    here is a substring check, which passes happily on duplicated output.
    """
    # The output has to dribble out over several seconds, or the job finishes
    # before the 1 s poller ever reads a chunk and only the drain writes --
    # which is why this race hid for so long. 20 lines at 0.3 s spans both.
    n = 20
    job = {
        "executable": "/bin/bash",
        "arguments": [
            "-c",
            f"for i in $(seq 1 {n}); do echo line-$i; sleep 0.3; done",
        ],
        "duration_sec": 120,
    }
    spec = TaskSpec(name="dup", kind="job", params={"job_spec": job, "executor": "local"})
    handle = await orbit.submit(spec)

    async def collect(_handle, _text):
        return None

    # A drain running concurrently with the manager's poller is the production
    # arrangement: `manager.submit` starts one for every interface that
    # advertises `supports_log_stream`.
    drain = asyncio.ensure_future(drain_logs(orbit, handle, collect, interval=0.2))
    result = await asyncio.wait_for(handle.future, timeout=180)
    drain.cancel()

    assert result["state"] == "DONE"
    lines = [ln for ln in (result["stdout"] or "").splitlines() if ln.startswith("line-")]
    assert len(lines) == n, f"expected {n} lines, got {len(lines)}"
    assert lines == [f"line-{i}" for i in range(1, n + 1)]


async def test_a_large_stdout_payload_survives_intact(orbit):
    """Stdout is the only way a job can return a file, so it must be exact."""
    # 1200 x 50 characters, well past the 8000-char UI tail cap.
    job = {
        "executable": "/bin/bash",
        "arguments": ["-c", 'for i in $(seq 1 1200); do printf "%050d\n" "$i"; done'],
        "duration_sec": 120,
    }
    spec = TaskSpec(name="big", kind="job", params={"job_spec": job, "executor": "local"})
    handle = await orbit.submit(spec)
    result = await asyncio.wait_for(handle.future, timeout=180)

    assert result["state"] == "DONE"
    expected = "".join(f"{i:050d}\n" for i in range(1, 1201))
    assert result["stdout"] == expected
    assert not result.get("stdout_truncated")


async def test_declared_outputs_come_back_through_a_real_psij_job(orbit):
    """Stage-in and stage-out over a real broker, endpoint and PSI/J executor.

    The offline tests run the generated script with bash directly; this is the
    same protocol with the whole transport underneath it, which is where a
    dropped spec field or a clipped stdout would actually show up.
    """
    job = {
        "executable": "bash",
        "arguments": [
            "-c",
            'mkdir -p out && wc -c < in.txt > out/size.txt '
            '&& tr "a-z" "A-Z" < in.txt > out/upper.txt',
        ],
        "inputs": {"in.txt": "staged through argv\n"},
        "outputs": ["out/*.txt"],
        "duration_sec": 120,
    }
    spec = TaskSpec(name="stage", kind="job", params={"job_spec": job, "executor": "local"})
    handle = await orbit.submit(spec)
    result = await asyncio.wait_for(handle.future, timeout=180)

    assert result["state"] == "DONE"
    assert not result.get("artifacts_error"), result.get("artifacts_error")
    assert not result.get("artifacts_truncated")
    artifacts = result["artifacts"]
    assert sorted(artifacts) == ["out/size.txt", "out/upper.txt"]
    assert artifacts["out/size.txt"].decode().strip() == "20"
    assert artifacts["out/upper.txt"].decode() == "STAGED THROUGH ARGV\n"


async def test_a_jobs_own_chatter_stays_out_of_the_staged_files(orbit):
    job = {
        "executable": "bash",
        "arguments": ["-c", 'echo noisy; echo ">looks like fasta"; printf "real\n" > r.txt'],
        "outputs": ["*.txt"],
        "duration_sec": 120,
    }
    spec = TaskSpec(name="noisy", kind="job", params={"job_spec": job, "executor": "local"})
    handle = await orbit.submit(spec)
    result = await asyncio.wait_for(handle.future, timeout=180)

    assert result["state"] == "DONE"
    assert result["artifacts"]["r.txt"].decode() == "real\n"
    # The chatter went to stderr, so it is diagnosable but not in the payload.
    assert "noisy" in (result.get("stderr") or "")


async def test_the_manager_turns_staged_files_into_blob_paths(orbit, tmp_path):
    """Bytes must not survive into a record: state and the graph see paths."""
    from designagent.config import Settings
    from designagent.lake.store import DesignHistory
    from designagent.tasks.manager import TaskManager

    history = DesignHistory(Settings(data_dir=tmp_path / "lake", anthropic_api_key=""))
    manager = TaskManager(hpc=orbit, history=history)
    try:
        job = {
            "executable": "bash",
            "arguments": ["-c", 'printf "payload\n" > kept.fa'],
            "outputs": ["*.fa"],
            "duration_sec": 120,
        }
        records = await manager.run_many(
            [TaskSpec(
                name="proteinmpnn",
                kind="job",
                params={"job_spec": job, "executor": "local", "_interface": "hpc"},
            )],
            campaign_id="artifacts-test",
            timeout=300,
        )
        assert len(records) == 1 and records[0]["ok"], records[0].get("error")
        artifacts = records[0]["result"]["artifacts"]
        path = artifacts["kept.fa"]
        assert isinstance(path, str), "the manager should have stored a path"
        assert Path(path).read_text() == "payload\n"
    finally:
        history.close()


# --- real ProteinMPNN, on this host, through the real transport --------------


def _helix_pdb(n: int = 30, chain: str = "A") -> str:
    """An ideal alpha helix with a full N/CA/C/O backbone.

    Synthetic so the test needs no committed structure and no network. The
    sequences ProteinMPNN returns for a poly-alanine helix are degenerate, which
    is fine: what is under test is the transport and the adapter, with the real
    model and real weights at the far end rather than a stub.
    """
    import math

    offset = {"N": -0.45, "CA": 0.0, "C": 0.55, "O": 0.75}
    radius = {"N": 2.2, "CA": 2.3, "C": 2.4, "O": 3.3}
    rows, serial = [], 1
    for i in range(1, n + 1):
        for atom in ("N", "CA", "C", "O"):
            t = i + offset[atom]
            ang = math.radians(100.0 * t)
            r = radius[atom]
            rows.append(
                f"ATOM  {serial:5d}  {atom:<3s} ALA {chain}{i:4d}    "
                f"{r * math.cos(ang):8.3f}{r * math.sin(ang):8.3f}{1.5 * t:8.3f}"
                f"  1.00 50.00           {atom[0]}"
            )
            serial += 1
    return "\n".join([*rows, "TER", "END"]) + "\n"


def _mpnn_command() -> str:
    """The local CPU install, or a skip naming what is missing."""
    import shutil

    root = Path(__file__).resolve().parent.parent
    python = root / ".venv-mpnn" / "bin" / "python"
    script = root / "refcodes" / "ProteinMPNN" / "protein_mpnn_run.py"
    if not python.exists() or not script.exists():
        pytest.skip("ProteinMPNN is not installed; run ./scripts/setup_mpnn.sh")
    if not shutil.which("sha256sum") or not shutil.which("base64"):
        pytest.skip("the staging protocol needs base64 and sha256sum")
    return f"{python} {script}"


async def test_real_proteinmpnn_runs_and_its_samples_reach_the_adapter(orbit):
    """The whole chain, with the real model: stage in, run, stage out, adapt.

    This is what makes a round attributable to specific weights. It says
    nothing about a scheduler, a queue or a GPU -- the local PSI/J executor
    forks a process. See plans/AMAREL_ENDPOINT.md, rung 4, for the real thing.
    """
    from designagent.tools.proteinmpnn import mpnn_job_spec, variants_from_mpnn_fasta

    command = _mpnn_command()
    structure = _helix_pdb(30)
    spec_dict = mpnn_job_spec(structure, num_sequences=3, command=command)
    # This host has no GPU, and the local executor would be asked for one.
    spec_dict["resources"].pop("gpus", None)

    spec = TaskSpec(
        name="proteinmpnn",
        kind="job",
        params={"job_spec": spec_dict, "executor": "local"},
    )
    handle = await orbit.submit(spec)
    result = await asyncio.wait_for(handle.future, timeout=600)

    assert result["state"] == "DONE", result.get("stderr", "")[-800:]
    assert not result.get("artifacts_error"), result["artifacts_error"]
    assert not result.get("artifacts_truncated")

    fasta = next(
        v.decode() for k, v in result["artifacts"].items() if k.endswith(".fa")
    )
    parent = "A" * 30
    adapted = variants_from_mpnn_fasta(fasta, parent_sequence=parent, requested=3)
    assert not adapted.get("error"), adapted["error"]
    assert adapted["n"] == 3

    # Provenance the model itself declared -- a stub could not produce these.
    assert adapted["provenance"]["model_name"].startswith("v_48_")
    assert len(adapted["provenance"]["git_hash"]) == 40

    for variant in adapted["variants"]:
        assert variant["source"] == "proteinmpnn"
        assert "mpnn_score" in variant["metrics"]
    # ProteinMPNN redesigns many positions at once; the heuristic table never
    # emits more than one substitution per variant.
    assert max(len(v["mutations"]) for v in adapted["variants"]) > 1
