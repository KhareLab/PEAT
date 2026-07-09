from bio_tools import foldseek_search, alphafold_fetch
from af3_tools import run_af3_monomer, AF3Config
from slurm_tools import local_to_remote_path, ssh_run, wait_for_slurm_job, get_slurm_job_id, get_current_job_ids
from mpnn_tools import run_proteinmpnn, ProteinMPNNConfig, build_patch_mpnn_script_cmd, build_submit_mpnn_cmd, ensure_remote_mpnn_dirs, download_proteinmpnn_results, upload_pdb
from homolog_tools import create_conservation_directoires, submit_hhblits_job, run_foldseek_pdb100
from sequence_tools import run_ebi_blast
from structure_tools import clean_pdb, check_missing_loops, pymol_align, get_aligned_residues, get_residual_mappings, download_pdb
import subprocess
import json
import os
import shutil
import requests
import sys
from pathlib import Path
from data_fetch import  get_pdb_data, annotate_uniprot



ARAMEL_DIR= "/home/cd1061"
DATA_DIR= "data"
UPLOAD_DIR= os.path.join(DATA_DIR, "uploaded_files")



def run_step_1(sequence_file: str, output_dir: str, email: str, target_name: str):
    result ={
        "input_sequence_file": sequence_file,
        "uniprot_hits":[],
        "chosen_uniprot_id": None,
        "pdb_hits": [],
        "chosen_pdb_id": None,
        "pdb_metadata": [],
        "structure_path": None,
        "structure_source": None,
        "foldseek_hits": [],
        "annotations": [],
        "paper-DOI": [],
        "notes": []
    }
    print("Running Step 1: Homolog Search and Annotation...")
    print(f"Sequence file: {sequence_file}, Output dir: {output_dir}, Email: {email}")
    uniprot_hits= run_ebi_blast(sequence_file, email)
    result["uniprot_hits"]= uniprot_hits

    if uniprot_hits:
        result["chosen_uniprot_id"]= uniprot_hits[0]
        print("Chosen uniprot id: ", result["chosen_uniprot_id"])
        result["annotations"]=annotate_uniprot(result["chosen_uniprot_id"], output_dir)
        pdb_entries = result["annotations"]["pdb_entries"]
        if pdb_entries:
            best_pdb = min(pdb_entries, key=lambda x: x["resolution"] if x["resolution"] is not None else float("inf"))
            result["chosen_pdb_id"] = best_pdb["pdb_id"]
            print("Chosen pdb structre", best_pdb["pdb_id"])
            result["structure_source"] = "pdb"
            pdb_data= get_pdb_data(result["chosen_pdb_id"])
            result["pdb_metadata"]=pdb_data
            result['paper-DOI']=pdb_data["rcsb_primary_citation"]["pdbx_database_id_DOI"]
            print("Paper DOIS: ",result['paper-DOI'])
            download_pdb(result["chosen_pdb_id"], output_dir)
            clean_pdb( pdb_path=os.path.join(output_dir,f"{result['chosen_pdb_id']}.pdb"), output_path=os.path.join(output_dir,f"clean_{result['chosen_pdb_id']}.pdb"), remove_ligands=True)
            result["structure_path"]= os.path.join(output_dir,f"clean_{result['chosen_pdb_id']}.pdb")
            return result

            
        else:
            print("No PDB structure exists")



    try:
        afdb= alphafold_fetch(result["chosen_uniprot_id"])

        af_pdb_path = afdb["pdb_path"]
        result["structure_path"] = af_pdb_path
        result["structure_source"] = "alphafold_db"
    except Exception:
        result["notes"].append("No AlphaFoldDB model found; run ColabFold.")
        cfg= AF3Config()
        cfg.remote_base= f"/home/cd1061/{result['chosen_uniprot_id']}"
        remote_pdb_path = local_to_remote_path(sequence_file, f"{cfg.remote_base}/{result['chosen_uniprot_id']}/af3", cfg.netid)

        run_af3_monomer(target_name=target_name, input_fasta_file=remote_pdb_path, download_type="top_model", cfg=cfg)
        result["structure_source"] = "colabfold"
        result["structure_path"] = f"{cfg.local_base}/{result['chosen_uniprot_id']}/af3/top_models/model.cif"
        
    foldseek_hits = run_foldseek_pdb100(result["structure_path"],".")
    result["foldseek_hits"]= foldseek_hits
    #print(foldseek_hits)
    if foldseek_hits:
        for hit in foldseek_hits:
            curr=get_pdb_data(hit)
            citation = curr.get("rcsb_primary_citation", {})
            doi = citation.get("pdbx_database_id_DOI")
            #print("look at :" ,hit)
            if doi:
                print("Doi",doi)
                result["chosen_pdb_id"] = hit
                result['paper-DOI']=curr["rcsb_primary_citation"]["pdbx_database_id_DOI"]
                download_pdb(hit, output_dir)
                break
        result["notes"].append(
            "Use top Foldseek PDB hit to find associated paper/catalytic residues."
        )

    pdb_hit_path = os.path.join(output_dir, f"{result['chosen_pdb_id']}.pdb")

    print("AF model:", result["structure_path"], os.path.exists(result["structure_path"]))
    print("PDB hit:", pdb_hit_path, os.path.exists(pdb_hit_path))

    pymol_align(
        pdb_hit_path,
        result["structure_path"],
        os.path.join(output_dir, "aligned.pse")
    )

    return result



def run_step2_conservation_pipeline(target_name: str, pdb_file: str):
    print("Running Step 2: Conservation Analysis...")
    create_conservation_directoires(ARAMEL_DIR, target_name)
    result=submit_hhblits_job(ARAMEL_DIR, target_name, pdb_file)

    return result



def run_step3_mpnn_pipeline(
    target_name: str,
    pdb_file: str,
    loc: str = ARAMEL_DIR,
    num_runs: int = 16,
    constraint_level: int = 50,
    soluble: bool = False,
    wait: bool = True,
    download: bool = False,
):
    """
    Step 3: Run ProteinMPNN/SolubleMPNN on Amarel.

    Expected remote layout:
        /home/cd1061/{target_name}/mpnn/
        /home/cd1061/{target_name}/mpnn/pdb/
        /home/cd1061/{target_name}/conservation/output/cpos_50.jsonl
        or
        /home/cd1061/{target_name}/conservation/output/{target_name}_cpos_50.jsonl

    Produces:
        /home/cd1061/{target_name}/mpnn/cpos_50/
        or
        /home/cd1061/{target_name}/mpnn/cpos_50_sol/
    """

    print("Running Step 3: ProteinMPNN Design...")

    remote_target_dir = f"{loc}/{target_name}"
    remote_mpnn_path = f"{remote_target_dir}/mpnn"

    output_name = f"cpos_{constraint_level}_sol" if soluble else f"cpos_{constraint_level}"
    local_constraint_name = f"{target_name}_cpos_{constraint_level}.jsonl"

    cfg = ProteinMPNNConfig(
        netid="cd1061",
        remote_host="amarel.rutgers.edu",
        conda_env="/projects/f_sdk94_1/conda/envs/aifold",
        partition="gpu-redhat",
        num_runs=num_runs,
        constraints_file=local_constraint_name,
        output_name=output_name,
        soluble=soluble,
        remote_location=loc,
    )

    # 1. Make remote mpnn/pdb directory
    ensure_remote_mpnn_dirs(remote_target_dir, cfg)

    # 2. Upload the PDB into /mpnn/pdb/
    pdb_name = upload_pdb(pdb_file, remote_target_dir, cfg)

    # 3. Copy the conservation constraint JSONL into the mpnn directory
    # This handles both possible naming styles.
    constraint_candidates = [
        f"{remote_target_dir}/conservation/output/{target_name}_cpos_{constraint_level}.jsonl",
        f"{remote_target_dir}/conservation/output/cpos_{constraint_level}.jsonl",
        f"{remote_target_dir}/conservation/{target_name}_cpos_{constraint_level}.jsonl",
        f"{remote_target_dir}/conservation/cpos_{constraint_level}.jsonl",
        f"{remote_mpnn_path}/{target_name}_cpos_{constraint_level}.jsonl",
        f"{remote_mpnn_path}/cpos_{constraint_level}.jsonl",
    ]

    candidate_str = " ".join(constraint_candidates)

    copy_constraint_cmd = f"""
cd {remote_target_dir}
for f in {candidate_str}; do
    if [ -f "$f" ]; then
        cp "$f" {remote_mpnn_path}/{local_constraint_name}
        echo "CONSTRAINT_FILE:$f"
        exit 0
    fi
done

echo "ERROR: Could not find cpos_{constraint_level}.jsonl constraint file."
echo "Checked:"
for f in {candidate_str}; do
    echo "$f"
done
exit 2
""".strip()

    # 4. Patch run_mpnn.sh
    patch_cmd = build_patch_mpnn_script_cmd(remote_target_dir, cfg)

    # 5. Submit job
    submit_cmd = build_submit_mpnn_cmd(remote_target_dir, cfg)

    remote_cmd = " && ".join([
        copy_constraint_cmd,
        patch_cmd,
        submit_cmd,
    ])

    print("Submitting ProteinMPNN job...")
    response = ssh_run(remote_cmd, cfg.netid)

    job_id = get_slurm_job_id(response.stdout)
    print(f"ProteinMPNN job id: {job_id}")
    print(f"Remote output directory: {remote_mpnn_path}/{output_name}")
    if wait:
        wait_for_slurm_job(job_id, cfg.netid)

    if download:
        download_proteinmpnn_results(loc, target_name, cfg)

    return {
        "job_id": job_id,
        "pdb_name": pdb_name,
        "remote_target_dir": remote_target_dir,
        "remote_mpnn_dir": remote_mpnn_path,
        "remote_output_dir": f"{remote_mpnn_path}/{output_name}",
        "constraints_file": local_constraint_name,
        "output_name": output_name,
        "soluble": soluble,
        "num_runs": num_runs,
    }



def run_step4_af3_pipeline(target_name: str, pdb_file: str, mpnn_output_dir: str):
    print("Running Step 4: AlphaFold3 Structure Prediction...")

    remote_mpnn_fasta = mpnn_output_dir or f"/home/cd1061/{target_name}/mpnn/cpos_50/seqs/{target_name}.fa"

    cfg = AF3Config()
    cfg.remote_base = f"/home/cd1061/{target_name}"

    result = run_af3_monomer(
        target_name=target_name,
        input_fasta_file=remote_mpnn_fasta,
        download_type="top_model",
        cfg=cfg,
    )

    return result

def run_step5_stability_analysis(target_name: str, pdb_file: str, mpnn_output_dir: str, af3_output_dir: str):
    result = {"notes": "Step 5 stability analysis not yet implemented."}
    return result


def run_full_pipeline(target_name: str, sequence_file: str, output_dir: str, email: str):
    print("Running full stabilization pipeline...")
    step1_result= run_step_1(sequence_file, output_dir, email, target_name)
    pdb_file= step1_result["structure_path"]
    print("PDB file for step 2: ", pdb_file, os.path.exists(pdb_file) if pdb_file else False)
    step2_result= run_step2_conservation_pipeline(target_name, pdb_file)
    amarel_fold= os.path.join(ARAMEL_DIR, target_name)
    print("Amarel fold path for step 3: ", amarel_fold, os.path.exists(amarel_fold))
    step3_result= run_step3_mpnn_pipeline(target_name, pdb_file)

    mpnn_output_dir = f"/home/cd1061/{target_name}/mpnn/cpos_50/seqs/{target_name}.fa"
    step4_result = run_step4_af3_pipeline(target_name, pdb_file, mpnn_output_dir)
    af3_output_dir= f"{target_name}_af3_output"
    #step5_result= run_step5_stability_analysis(target_name, pdb_file, mpnn_output_dir, af3_output_dir)

    return {
        "step1": step1_result,
        "step2": step2_result,
        "step3": step3_result,
        "step4": step4_result,
        #"step5": step5_result
    }


