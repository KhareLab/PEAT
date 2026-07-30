import os
import time
import requests
import subprocess
from pathlib import Path
from dataclasses import dataclass

from dataclasses import dataclass
from pathlib import Path
import subprocess
from slurm_tools import ssh_run, get_slurm_job_id, wait_for_slurm_job, local_to_remote_path


@dataclass
class ProteinMPNNConfig:
    netid: str = "cd1061"
    remote_host: str = "amarel.rutgers.edu"
    conda_env: str = "/projects/f_sdk94_1/conda/envs/aifold"
    partition: str = "gpu-redhat"
    num_runs: int = 16
    constraints_file: str = "cpos_50.jsonl"
    output_name: str = "cpos_50"
    soluble: bool = False
    remote_location: str = "/home/cd1061/Alpha"
    


def mpnn_model_name(soluble: bool) -> str:
    return "v_48_010" if soluble else "v_48_020"


def remote_mpnn_dir(loc: str) -> str:
    return f"{loc}/mpnn"


def ensure_remote_mpnn_dirs(loc: str, cfg: ProteinMPNNConfig) -> None:
    subprocess.run(
        ["ssh", f"{cfg.netid}@{cfg.remote_host}", f"mkdir -p {remote_mpnn_dir(loc)}/pdb"],
        check=True,
    )


def upload_pdb(input_file: str, loc: str, cfg: ProteinMPNNConfig) -> str:
    local_pdb = Path(input_file)
    pdb_name = local_pdb.name

    subprocess.run(
        [
            "scp",
            str(local_pdb),
            f"{cfg.netid}@{cfg.remote_host}:{remote_mpnn_dir(loc)}/pdb/{pdb_name}",
        ],
        check=True,
    )

    return pdb_name


def build_patch_mpnn_script_cmd(loc: str, cfg: ProteinMPNNConfig) -> str:
    model_name = mpnn_model_name(cfg.soluble)

    cmds = [
        f"cd {remote_mpnn_dir(loc)}",
        "cp /projects/f_sdk94_1/Tools/StabilizationProtocol/run_mpnn.sh .",
        "source ~/.bashrc",
        f"conda activate {cfg.conda_env}",
        f"sed -i 's/--num_seq_per_target [0-9]*/--num_seq_per_target {cfg.num_runs}/' run_mpnn.sh",
        f"sed -i 's/--model_name v_48_[0-9]*/--model_name {model_name}/' run_mpnn.sh",
        f"sed -i 's/^#SBATCH --partition=.*/#SBATCH --partition={cfg.partition}/' run_mpnn.sh",
    ]

    if cfg.soluble:
        cmds.append(
            "grep -q -- '--use_soluble_model' run_mpnn.sh || "
            "sed -i '/--omit_AAs/a\\        --use_soluble_model \\\\' run_mpnn.sh"
        )
    else:
        cmds.append("sed -i '/--use_soluble_model/d' run_mpnn.sh")

    return " && ".join(cmds)


def build_submit_mpnn_cmd(loc: str, cfg: ProteinMPNNConfig) -> str:
    return (
        f"cd {remote_mpnn_dir(loc)} && "
        f"sbatch run_mpnn.sh pdb {cfg.output_name} {cfg.constraints_file}"
    )


def submit_proteinmpnn(loc: str, cfg: ProteinMPNNConfig) -> str:
    patch_cmd = build_patch_mpnn_script_cmd(loc, cfg)
    submit_cmd = build_submit_mpnn_cmd(loc, cfg)

    response = ssh_run(f"{patch_cmd} && {submit_cmd}")
    job_id = get_slurm_job_id(response.stdout)

    return job_id


def run_proteinmpnn(
    input_file: str,
    pdb_id: str,
    loc: str,
    cfg: ProteinMPNNConfig = ProteinMPNNConfig(),
    wait: bool = True,
) -> str:
    ensure_remote_mpnn_dirs(f"{loc}/{pdb_id}", cfg)

    job_id = submit_proteinmpnn(f"{loc}/{pdb_id}", cfg)

    if wait:
        wait_for_slurm_job(job_id, cfg.netid)

    return job_id

def download_proteinmpnn_results(loc: str, pdb_id: str, cfg: ProteinMPNNConfig) -> None:
    remote_results_dir = f"{remote_mpnn_dir(f'{loc}/{pdb_id}')}/cpos_50"
    local_results_dir = Path(f"data/downloaded_results/{pdb_id}")
    local_results_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            "scp",
            "-r",
            f"{cfg.netid}@{cfg.remote_host}:{remote_results_dir}/*",
            str(local_results_dir),
        ],
        check=True,
    )



def run_proteinmpnn_test(
    input_file: str,
    loc: str,
    num_runs: int = 16,
    constraints_file: str = "A0A9P7YUI4_cpos_50.jsonl",
    catalytic_residues: list[str] = [],
    output_name: str = "cpos_50",
    soluble: bool = False):
    local_pdb = Path(input_file)
    pdb_name = local_pdb.name

    model_name = "v_48_010" if soluble else "v_48_020"


    remote_cmd = " && ".join([
        f"cd {loc}/mpnn",
        "mkdir -p pdb",
        
        "cp / .",
        "source ~/.bashrc",
        "conda activate /projects/f_sdk94_1/conda/envs/aifold",
        f"sed -i 's/--num_seq_per_target [0-9]*/--num_seq_per_target {num_runs}/' run_mpnn.sh",

        f"sed -i 's/--model_name v_48_[0-9]*/--model_name {model_name}/' run_mpnn.sh",
        "sed -i 's/^#SBATCH --partition=.*/#SBATCH --partition=gpu-redhat/' run_mpnn.sh"
    ])

    subprocess.run(
        ["ssh", "cd1061@amarel.rutgers.edu", f"cd {loc}/mpnn && mkdir -p pdb"],
        check=True
    )

    subprocess.run(
        ["scp", str(local_pdb), f"cd1061@amarel.rutgers.edu:{loc}/mpnn/pdb/{pdb_name}"],
        check=True,
    )

    if soluble:
        remote_cmd += " && grep -q -- '--use_soluble_model' run_mpnn.sh || sed -i '/--omit_AAs/a\\        --use_soluble_model \\\\' run_mpnn.sh"
    else:
        remote_cmd += " && sed -i '/--use_soluble_model/d' run_mpnn.sh"

    remote_cmd += f" && sbatch run_mpnn.sh pdb {output_name} {constraints_file}"

    print(remote_cmd)

    response= ssh_run(remote_cmd)

    wait_for_slurm_job(get_slurm_job_id(response.stdout), "cd1061")

    download_proteinmpnn_results(loc, pdb_name, ProteinMPNNConfig())

