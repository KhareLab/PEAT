from langgraph.types import Command
from dotenv import load_dotenv
from graph.state import PEATState
from stabilization_tools import run_full_pipeline
load_dotenv()
import os

def stabilization_workflow(state: PEATState) -> Command:
    """Run the stabilization workflow."""
    #get fasta file from data/uploaded_files
    upload_dir = "data/uploaded_files"
    sequence_file = None
    if os.path.isdir(upload_dir):
        for file in os.listdir(upload_dir):
            if file.endswith(".fasta") or file.endswith(".fa"):
                sequence_file = os.path.join(upload_dir, file)
                break

    target_name = state.get("target_name")
    output_dir = "output_stabilization"
    email = os.getenv("UNPAYWALL_EMAIL")  # Use the email from the environment variable
    catalytic_residual= state.get("m_csa_sites")
    print(f"Target name: {target_name}, Sequence file: {sequence_file}, Output dir: {output_dir}, Email: {email}")

    missing = []
    if not sequence_file:
        missing.append("a .fasta/.fa file in data/uploaded_files")
    if not target_name:
        missing.append("target_name (say e.g. 'run stabilization <name>')")
    if not email:
        missing.append("UNPAYWALL_EMAIL environment variable")

    if missing:
        return Command(
            update={
                "response_text": "Cannot run stabilization workflow. Missing: "
                + "; ".join(missing)
            },
            goto="format_response",
        )

    os.makedirs(output_dir, exist_ok=True)

    try:
        result = run_full_pipeline(target_name, sequence_file, output_dir, email, catalytic_residual)
    except Exception as e:
        return Command(
            update={"response_text": f"Stabilization workflow failed: {e}"},
            goto="format_response",
        )

    summary = _summarize(target_name, result)
    return Command(
        update={"stabilization_result": result, "response_text": summary},
        goto="format_response",
    )


def _summarize(target_name: str, result: dict) -> str:
    step1 = result.get("step1", {}) or {}
    lines = [f"**Stabilization workflow completed for `{target_name}`.**", ""]
    if step1.get("chosen_uniprot_id"):
        lines.append(f"- UniProt: `{step1['chosen_uniprot_id']}`")
    if step1.get("chosen_pdb_id"):
        lines.append(f"- Structure: `{step1['chosen_pdb_id']}` ({step1.get('structure_source')})")
    for step in ("step2", "step3", "step4"):
        val = result.get(step)
        if isinstance(val, dict) and val.get("job_id"):
            lines.append(f"- {step}: submitted job `{val['job_id']}`")
    return "\n".join(lines)

