import re
import subprocess
import time
import os
import shutil
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file

def ssh_run(cmd: str, netid: str = os.getenv("HPC_USER", "cd1061")):
    #print(cmd)
    print("Running remote command via SSH...")
    return subprocess.run(
        ["ssh", f"{netid}@amarel.rutgers.edu", cmd],
        check=True,
        text=True,
        capture_output=True,
    )

def get_slurm_job_id(ssh_output: str) -> str:
    """Extract the Slurm job ID from the output of an sbatch command."""
    match = re.search(r"Submitted batch job (\d+)", ssh_output)
    if not match:
        raise ValueError(f"Could not extract job ID from sbatch output: {ssh_output}")
    return match.group(1)


def local_to_remote_path(local_file: str, remote_base: str, netid: str = "cd1061") -> str:
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