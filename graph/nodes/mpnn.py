from mpnn_tools import ProteinMPNNConfig, ensure_remote_mpnn_dirs, upload_pdb, build_patch_mpnn_script_cmd, run_proteinmpnn, download_proteinmpnn_results
from slurm_tools import wait_for_slurm_job, ssh_run, get_slurm_job_id, local_to_remote_path
from graph.state import PEATState
import os

def protein_mpnn(state: PEATState)-> dict:
    """Run ProteinMPNN on the provided PDB ID and return designed sequences."""
    pdb_id = state.get("pdb_id")
    if not pdb_id:
        raise ValueError("No PDB ID found in state for ProteinMPNN node.")

    cfg = ProteinMPNNConfig(
        remote_location= "/home/cd1061",  
    )

    for file in os.listdir("uploaded_files"):
        if file.endswith(".pdb"):
            local_pdb_path = os.path.join("uploaded_files", file)
            break

    remote_pdb_path = f"{cfg.remote_location}/{pdb_id}/mpnn/pdb"

    local_to_remote_path(local_pdb_path, remote_pdb_path, cfg.netid)



    job_id = run_proteinmpnn(
        input_file=remote_pdb_path,
        pdb_id=pdb_id,
        loc=f"{cfg.remote_location}",
        cfg=cfg,
        wait=True,
    )

    download_proteinmpnn_results(f"{cfg.remote_location}", pdb_id, cfg)

    return {"response_text": f"ProteinMPNN job submitted for {pdb_id}. Job ID: {job_id}", "mpnn_job_id": job_id}
