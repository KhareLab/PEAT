from af3_tools import run_af3_monomer, AF3Config
from slurm_tools import local_to_remote_path
from graph.state import PEATState
import os
from slurm_tools import ssh_run, wait_for_slurm_job


def alphafold3(state: PEATState) -> dict:
    """Run AlphaFold3 on the provided PDB ID and return designed sequences."""
    uniport_id = state.get("uniprot_id")
    if not uniport_id:
        return {"response_text": "No UniProt ID provided for AlphaFold3 analysis."}
    # Create an AF3Config from the state, with defaults
    cfg= AF3Config(
        remote_base=f"/home/cd1061",  # Base directory on the remote server for AF3 jobs
    )

    ssh_run(f"mkdir -p {cfg.remote_base}/{uniport_id}/af3/af_input", cfg.netid)


    for file in os.listdir("uploaded_files"):
        if file.endswith(".fa") or file.endswith(".fasta"):
            local_pdb_path = os.path.join("uploaded_files", file)

            with open(local_pdb_path, "r", newline=None) as f:
                fasta_text = f.read()

            fasta_text = fasta_text.replace("\r", "")

            with open(local_pdb_path, "w", newline="\n") as f:
                f.write(fasta_text)
            break
    
    remote_pdb_path = local_to_remote_path(local_pdb_path, f"{cfg.remote_base}/{uniport_id}/af3", cfg.netid)

    print("remote_pdb_path:", remote_pdb_path)

    # Run AlphaFold3
    job_id = run_af3_monomer(
        target_name=uniport_id,
        input_fasta_file=remote_pdb_path,
        cfg=cfg
    )

    return {"response_text": f"AlphaFold3 job submitted for {uniport_id}. Job ID: {job_id}", "af3_job_id": job_id}
