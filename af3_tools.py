import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv
import os
import time

from slurm_tools import ssh_run, get_slurm_job_id, wait_for_slurm_job, get_current_job_ids

load_dotenv()  # Load environment variables from .env file

@dataclass
class AF3Config:
    netid: str= "cd1061"
    remote_host: str="amarel.rutgers.edu"
    conda_env: str="/projects/f_sdk94_1/conda/envs/shared_als515"
    partition: str="gpu-redhat"
    wait: bool=True
    af3_tools_dir: str="/projects/f_sdk94_1/Tools/AlphaFold3/af3_monomer"
    remote_base: str="/home/cd1061/Alpha"  # Base directory on the remote server for AF3 jobs
    local_base: str="output"  # Base directory on the local machine for AF3 results


def copy_stabilization_scripts(remote_dir: str, cfg: AF3Config) -> None:
    remote_cmd = " && ".join([
        f"mkdir -p {remote_dir}",
        f"cd {remote_dir}",
        f"cp {cfg.af3_tools_dir}/* .",
    ])

    ssh_run(remote_cmd, cfg.netid)

def build_af3_monomer_command(
    target_name: str,
    input_fasta_file: str,
    cfg: AF3Config,
) -> str:
    target_dir = f"{cfg.remote_base}/{target_name}"
    af3_dir = f"{target_dir}/af3"

    wrapper_script = f"""printf '%s\\n' \
'#!/bin/bash' \
'' \
'NETID="{cfg.netid}"' \
'' \
'INPUT_DIR="af_input"' \
'OUTPUT_DIR="af_output"' \
'' \
'mkdir -p "$OUTPUT_DIR"' \
'' \
'for f in "$INPUT_DIR"/*.json; do' \
'    if [ ! -f "$f" ]; then' \
'        echo "No JSON files found in $INPUT_DIR"' \
'        exit 1' \
'    fi' \
'' \
'    base=$(basename "$f" .json)' \
'' \
'    check=$(squeue -u "${{NETID}}" | wc -l)' \
'    while [ "$check" -ge 100 ]; do' \
'        sleep 300' \
'        check=$(squeue -u "${{NETID}}" | wc -l)' \
'    done' \
'' \
'    echo "Submitting AF3 job for: $base"' \
'    inner_jid=$(sbatch --parsable 02_af3_batch_input.sh "$base" "$INPUT_DIR" "$OUTPUT_DIR")' \
'    echo "INNER_AF3_JOB_ID:$inner_jid $base"' \
'done' \
> 02_af3_job.sh && chmod +x 02_af3_job.sh"""

    return " && ".join([
        f"mkdir -p {af3_dir}",
        f"cd {af3_dir}",
        f"cp {cfg.af3_tools_dir}/* .",

        "mkdir -p af_input af_output",

        "source ~/.bashrc",
        f"conda activate {cfg.conda_env}",

        # Patch JSON output directory.
        "sed -i 's|^out_dir=.*|out_dir=\"af_input\"|' 01_af3_json_monomer.sh",
        "echo AFTER_PATCH:",
        "grep -n '^out_dir=' 01_af3_json_monomer.sh",

        # Rename FASTA entries as design_1, design_2, ...
        "grep -q '^counter=1' 01_af3_json_monomer.sh || "
        "sed -i '/^sequence=\"\"/a counter=1' 01_af3_json_monomer.sh",

        "sed -i 's|pdb_name=\"${line#>}\"|pdb_name=\"design_${counter}\"\\n        counter=$((counter+1))|' "
        "01_af3_json_monomer.sh || true",

        # Fully rewrite the wrapper script instead of fragile sed.
        wrapper_script,

        # Patch AF3 batch script.
        f"sed -i 's|#SBATCH --partition=.*|#SBATCH --partition={cfg.partition}|' 02_af3_batch_input.sh || true",

        # Make sure old module path exists.
        "grep -q 'community-old/modulefiles' 02_af3_batch_input.sh || "
        "sed -i '/module use \\/projects\\/community\\/modulefiles/a module use /projects/community-old/modulefiles' 02_af3_batch_input.sh",

        # Make sure correct AF3 container is used.
        "sed -i '/--nv .*alphafold3.sif/c\\    --nv /projects/community/alphafold/vs3.0.0/pgarias/alphafold3.sif  \\\\' 02_af3_batch_input.sh",

        # Submit JSON generation first.
        f"json_jid=$(sbatch --parsable 01_af3_json_monomer.sh '{input_fasta_file}')",
        "echo JSON_JOB_ID:$json_jid",

        # Submit wrapper after JSON generation succeeds.
        "af3_jid=$(sbatch --parsable --dependency=afterok:$json_jid 02_af3_job.sh)",
        "echo AF3_JOB_ID:$af3_jid",

        "echo AF3_INPUT_DIR:$(pwd)/af_input",
        "echo AF3_OUTPUT_DIR:$(pwd)/af_output",
    ])

def download_af3_results(target_name: str, cfg: AF3Config) -> None:
    print(f"Downloading AF3 results for {target_name}...")

    remote_dir = f"{cfg.remote_base}/{target_name}/af3/af_output"
    local_dir = f"{cfg.local_base}/{target_name}/af3"

    os.makedirs(local_dir, exist_ok=True)

    cmd = [
        "scp",
        "-r",
        f"{cfg.netid}@{cfg.remote_host}:{remote_dir}",
        local_dir,
    ]

    subprocess.run(cmd, check=True)

def download_top_af3_model(target_name: str, design_name: str, cfg: AF3Config) -> None:
    print(f"Downloading top AF3 model for {target_name}, {design_name}...")
    print(f"Remote base: {cfg.remote_base}, Local base: {cfg.local_base}")
    remote_model_path = (
        f"{cfg.remote_base}/af3/af_output/af_output/"
        f"design_01_/model.cif"
    )

    local_dir = f"{cfg.local_base}/{target_name}/af3/top_models"
    os.makedirs(local_dir, exist_ok=True)

    local_model_path = f"{local_dir}/model.cif"

    cmd = [
        "scp",
        f"{cfg.netid}@{cfg.remote_host}:{remote_model_path}",
        local_model_path,
    ]

    subprocess.run(cmd, check=True)

def wait_for_user_af3_jobs(netid: str) -> None:
    while True:
        result = ssh_run("squeue -u $USER -h -n alphafold3 | wc -l", netid)
        count = int(result.stdout.strip())

        if count == 0:
            break

        print(f"Waiting for {count} AF3 job(s) to finish...")
        time.sleep(300)

def run_af3_monomer(
    target_name: str,
    input_fasta_file: str,
    download_type: str = "all",  # Options: "all", "top_model"
    cfg: AF3Config | None = None,
) -> dict:
    cfg = cfg or AF3Config()

    os.makedirs(f"{cfg.local_base}/{target_name}/af3", exist_ok=True)

    remote_cmd = build_af3_monomer_command(
        target_name=target_name,
        input_fasta_file=input_fasta_file,
        cfg=cfg,
    )

    try:
        response = ssh_run(remote_cmd, cfg.netid)
    except subprocess.CalledProcessError as e:
        print("STDOUT:\n", e.stdout)
        print("STDERR:\n", e.stderr)
        raise

    stdout = response.stdout

    json_job = re.search(r"JSON_JOB_ID:(\d+)", stdout)
    af3_job = re.search(r"AF3_JOB_ID:(\d+)", stdout)

    json_job_id = json_job.group(1) if json_job else None
    af3_job_id = af3_job.group(1) if af3_job else None

    print(f"JSON_JOB_ID: {json_job_id}, AF3_JOB_ID: {af3_job_id}")

    #if cfg.wait and af3_job_id:
    #    wait_for_slurm_job(af3_job_id, cfg.netid)
    #    wait_for_user_af3_jobs(cfg.netid)

    if download_type == "all":
        download_af3_results(target_name, cfg)
    elif download_type == "top_model":
        download_top_af3_model(target_name, design_name="design_01", cfg=cfg)

    return {
        "target_name": target_name,
        "json_job_id": json_job_id,
        "af3_job_id": af3_job_id,
        "stdout": stdout,
        "stderr": response.stderr,
        "af3_input_dir": f"{cfg.remote_base}/{target_name}/af3/af_input",
        "af3_output_dir": f"{cfg.remote_base}/{target_name}/af3/af_output",
        "local_output_dir": f"{cfg.local_base}/{target_name}/af3",
    }