import subprocess
import os
import shutil
from pathlib import Path
from slurm_tools import ssh_run, get_slurm_job_id, wait_for_slurm_job
import json
import requests
import shlex
import time

STABILIZATION_DIR = "/projects/f_sdk94_1/Tools/StabilizationProtocol"




def create_conservation_directoires(loc: str, target: str):
    remote_cmd = " && ".join([
    f"cd {loc}",
    f"mkdir -p {target}",
    f"cd {target}",
    "mkdir -p conservation",
    "mkdir -p mpnn",
    "mkdir -p af3",
    f"cd {STABILIZATION_DIR}",
    f"cp run_hhblits_search.sh hhblits_search.py PDB_Tools_V3.py \
   /cache/home/cd1061/{target}/conservation/",
   f"cp run_mpnn.sh /cache/home/cd1061/{target}/mpnn/",
   "pwd"
    ])
    print(remote_cmd)
    
    response= ssh_run(remote_cmd)
    print(response.stdout)
from pathlib import Path
import shlex



def cif_to_pdb_amarel(file_location: str) -> str:
    cif_path = Path(file_location)
    pdb_path = cif_path.with_suffix(".pdb")

    directory = shlex.quote(str(cif_path.parent))
    cif_name = cif_path.name
    pdb_name = pdb_path.name

    python_executable = "/projects/f_sdk94_1/conda/envs/aifold/bin/python"

    print(f"Converting CIF to PDB: {cif_path} -> {pdb_path}")
    print(f"Directory: {cif_path.parent}")

    cmd = (
        f"cd {directory} && "
        f"{python_executable} - <<'PY'\n"
        "from Bio.PDB import MMCIFParser, PDBIO\n"
        "\n"
        f"input_cif = {cif_name!r}\n"
        f"output_pdb = {pdb_name!r}\n"
        "\n"
        "parser = MMCIFParser(QUIET=True)\n"
        "structure = parser.get_structure('model', input_cif)\n"
        "\n"
        "io = PDBIO()\n"
        "io.set_structure(structure)\n"
        "io.save(output_pdb)\n"
        "\n"
        "print(f'Converted {input_cif} -> {output_pdb}')\n"
        "PY\n"
        f"test -s {shlex.quote(pdb_name)}"
    )

    response = ssh_run(cmd)

    print("STDOUT:")
    print(response.stdout)

    if response.stderr:
        print("STDERR:")
        print(response.stderr)

    return str(pdb_path)

def submit_hhblits_job(loc:str,target_name: str, pdb_filename: str, catalytic_residues: list[str] = []):
    print(f"formated catalytic residues: {','.join(map(str, catalytic_residues))}")
    local_pdb = Path(pdb_filename)
    filename_without_ext = local_pdb.stem
    remote_conservation = f"{loc}/{target_name}/conservation"
    remote_pdb_name = local_pdb.name
    subprocess.run(
        [
            "scp",
            str(local_pdb),
            f"cd1061@amarel.rutgers.edu:{remote_conservation}/"
        ],
        check=True
    )

    pdb_filename = cif_to_pdb_amarel(remote_conservation + "/" + remote_pdb_name)

    print("File name without extension: ", filename_without_ext)
    remote_cmd = " && ".join([
        f"cd {loc}/{target_name}/conservation",
        f"sed -i 's/CATALYTIC_RESIDUES=\"[^\"]*\"/CATALYTIC_RESIDUES=\"{','.join(map(str, catalytic_residues))}\"/' run_hhblits_search.sh",
        "source ~/.bashrc",
        "conda activate /projects/f_sdk94_1/conda/envs/aifold",
        "which hhblits",
        f"sbatch run_hhblits_search.sh {filename_without_ext}.pdb"
    ])
    response= ssh_run(remote_cmd)
    wait_for_slurm_job(get_slurm_job_id(response.stdout), "cd1061")
    print(response.stdout)






def run_foldseek_pdb100(pdb_path: str, output_dir: str) -> list[dict]:
    print("Running Foldseek search...")
    _FOLDSEEK_URL = "https://search.foldseek.com/api"

    with open(os.path.join(output_dir,pdb_path), "rb") as fh:
        resp = requests.post(
            f"{_FOLDSEEK_URL}/ticket",
            data={"database[]": ["pdb100"], "mode": "3diaa"},
            files={"q": (os.path.basename(pdb_path), fh, "chemical/x-pdb")},
            timeout=30,
        )
    resp.raise_for_status()
    ticket_id = resp.json()["id"]

    for _ in range(60):
        time.sleep(5)
        poll = requests.get(f"{_FOLDSEEK_URL}/ticket/{ticket_id}", timeout=15)
        poll.raise_for_status()
        status_data = poll.json()

        if status_data.get("status") == "COMPLETE":
            break
        if status_data.get("status") == "ERROR":
            raise RuntimeError(f"Foldseek job failed: {status_data}")
    else:
        raise TimeoutError("Foldseek job timed out after 5 minutes")

    result_resp = requests.get(f"{_FOLDSEEK_URL}/result/{ticket_id}/0", timeout=30)
    result_resp.raise_for_status()
    data = result_resp.json()
    with open(os.path.join(output_dir,"foldseek.json"), "w") as f:
        json.dump(data, f, indent=2)


    hits = []
    for db_result in data.get("results", []):
        for query_alignments in db_result.get("alignments", []):
            if not isinstance(query_alignments, list):
                query_alignments = [query_alignments]
            for hit in query_alignments:
                hits.append({
                    "pdb_id": hit.get("target", ""),
                    "evalue": hit.get("eval"),
                    "prob": hit.get("prob"),
                    "sequence_identity": (hit["seqId"] / 100)
                    if hit.get("seqId") is not None else None,
                    "description": hit.get("taxName") or hit.get("description", ""),
                })
    print("Number of hits: ", len(hits))
    hits.sort(key=lambda h: h["prob"] or 0.0, reverse=True)
    hits_id=[]
    for hit in hits:
        hits_id.append(hit["pdb_id"].split("-")[0])
    return hits_id[:50]


