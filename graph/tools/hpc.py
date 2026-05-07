from langchain_core.tools import tool

from hpc_tools import run_hpc_command as _run_hpc_command
from hpc_tools import submit_minimization as _submit_minimization
from hpc_tools import check_job_status as _check_job_status
from hpc_tools import download_job_results as _download_job_results


@tool
def run_hpc_command(command: str) -> str:
    """Execute a whitelisted HPC shell command (gmx, sbatch, squeue, etc.)."""
    return _run_hpc_command(command)


@tool
def submit_minimization(pdb_id: str) -> dict:
    """Submit a GROMACS energy minimization job for a protein structure on HPC via Globus Compute."""
    return _submit_minimization(pdb_id)


@tool
def check_job_status(job_id: str) -> str:
    """Check the SLURM status of a submitted HPC job."""
    return _check_job_status(job_id)


@tool
def download_job_results(job_id: str, pdb_id: str) -> dict:
    """Download results from a completed HPC energy minimization job."""
    return _download_job_results(job_id, pdb_id)
