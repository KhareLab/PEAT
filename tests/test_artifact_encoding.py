"""Text artifacts use the encoding advertised to HTTP clients on every host."""

from designagent.artifacts.store import ArtifactStore


def test_unicode_summary_and_notebook_survive_reload(tmp_path):
    store = ArtifactStore(tmp_path)
    text = "Sequence ΔG — catalytic β strand 🧬\n"
    ref = store.add("markdown", "ΔG summary", content=text, session_id="s1")

    reloaded = ArtifactStore(tmp_path)
    assert reloaded.read_bytes(ref["id"]).decode("utf-8") == text
    assert reloaded.list_for_session("s1")[0]["title"] == "ΔG summary"
    reloaded.add("markdown", "ΔG summary", content=text * 2, artifact_id=ref["id"])
    assert reloaded.read_bytes(ref["id"]).decode("utf-8") == text * 2
