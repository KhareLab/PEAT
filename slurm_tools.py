import re
import subprocess
import time
import os
import shutil
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file
from pathlib import PurePosixPath
import shlex

def ssh_run(cmd: str, netid: str = os.getenv("HPC_USER", "cd1061")):
    #print(cmd)
    print("Running remote command via SSH...")
    return subprocess.run(
        ["ssh", f"{netid}@amarel.rutgers.edu", cmd],
        check=True,
        text=True,
        capture_output=True,
    )
    print("REMOTE STDOUT:")
    print(response.stdout)

    print("REMOTE STDERR:")
    print(response.stderr)

    return response     

def get_slurm_job_id(ssh_output: str) -> str:
    """Extract a Slurm job ID from sbatch output."""
    output = ssh_output.strip()

    # Output from: sbatch --parsable
    # It may be "58173701" or "58173701;cluster_name".
    match = re.fullmatch(r"(\d+)(?:;[^\s]+)?", output)
    if match:
        return match.group(1)

    # Output from regular: sbatch
    match = re.search(r"Submitted batch job\s+(\d+)", output)
    if match:
        return match.group(1)

    raise ValueError(
        f"Could not extract job ID from sbatch output: {ssh_output!r}"
    )


def local_to_remote_path(local_file: str , remote_base: str, netid: str = "cd1061") -> str:
    """Send file to remote server and return the remote path."""
    local_path = Path(local_file)
    #madke remote directories if they don't exist
    ssh_run(f"mkdir -p {remote_base}", netid)
    cmd = ["scp", str(local_file), f"{netid}@amarel.rutgers.edu:{remote_base}/"]
    subprocess.run(cmd, check=True)
    return f"{remote_base}/{local_path.name}"




def wait_for_slurm_job(job_id, netid, poll_interval=30, ssh_host="amarel.rutgers.edu"):
    """Wait until the remote Slurm job is no longer in squeue.

    Args:
        job_id: Slurm job id to monitor.
        netid: Remote user netid for ssh.
        poll_interval: Seconds between remote checks.
        ssh_host: Hostname for the remote ssh target.

    Returns:
        The final subprocess.CompletedProcess object for the empty squeue response.
    """
    remote_cmd = f"squeue -j {job_id} -h"
    while True:
        response = subprocess.run(
            ["ssh", f"{netid}@{ssh_host}", remote_cmd],
            capture_output=True,
            text=True,
            check=True,
        )
        print(response.stdout)
        if not response.stdout.strip():
            print(f"Slurm job {job_id} has finished.")
            return response
        time.sleep(poll_interval)
    

def get_current_job_ids(netid: str, ssh_host: str =os.getenv("HPC_HOST", "amarel")) -> set[str]:
    cmd = "squeue --me -h -o '%i'"
    response = subprocess.run(
        ["ssh", f"{netid}@{ssh_host}", cmd],
        capture_output=True,
        text=True,
        check=True,
    )
    
    return set(response.stdout.split())

def convert_cif_to_pdb(remote_base: str, cif_name: str, netid: str = os.getenv("HPC_USER", "cd1061")) -> str:
    remote_base_path = PurePosixPath(remote_base)
    cif_path = remote_base_path / cif_name

    if cif_path.suffix.lower() != ".cif":
        raise ValueError(f"Expected a .cif file, received: {cif_path}")

    pdb_path = cif_path.with_suffix(".pdb")

    print("Cif path:", cif_path)
    print("Pdb path:", pdb_path)

    remote_cmd = f"""
cd {remote_base}
set -e

source ~/.bashrc
conda activate /projects/f_sdk94_1/conda/envs/aifold

python - <<'PY'
from Bio.PDB import MMCIFParser, PDBIO
from pathlib import Path

cif_path = Path({str(cif_path)!r})
pdb_path = Path({str(pdb_path)!r})

if not cif_path.is_file():
    raise FileNotFoundError(f"CIF file does not exist: {{cif_path}}")

structure = MMCIFParser(QUIET=True).get_structure(
    cif_path.stem,
    str(cif_path),
)

io = PDBIO()
io.set_structure(structure)
io.save(str(pdb_path))

if not pdb_path.is_file() or pdb_path.stat().st_size == 0:
    raise RuntimeError(f"PDB conversion failed: {{pdb_path}}")

print(f"PDB_PATH:{{pdb_path}}")
PY
""".strip()

    response = ssh_run(remote_cmd, netid)

    if response.returncode != 0:
        raise RuntimeError(
            "CIF-to-PDB conversion failed.\n"
            f"stdout:\n{response.stdout}\n"
            f"stderr:\n{response.stderr}"
        )

    return str(pdb_path)
    