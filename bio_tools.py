# bio_tools.py
import os
import time
import requests
import statistics

import re
import shlex
from slurm_tools import ssh_run, get_slurm_job_id


def run_foldseek_amarel(remote_dir: str, pdb_filename: str) -> str:
    pdb_name = shlex.quote(pdb_filename)
    print(f"Running Foldseek for PDB file: {pdb_filename} in remote directory: {remote_dir}")

    cmd = " && ".join([
        f"cd {shlex.quote(remote_dir)}",
        "mkdir -p foldseek",
        "cd foldseek",
        "cp /projects/f_sdk94_1/Tools/FoldSeek/* .",
        #f"cp {pdb_name} .",
        # Replace the old -q value inside the existing Slurm script.
        f"sed -i 's|-q [^ ]*|-q {shlex.quote(pdb_filename)}|' run_foldseek_hits.py",
        # Submit the existing script and print its job ID.
        "sbatch --parsable run_foldseek_hits.py",
    ])

    result = ssh_run(cmd)
    job_id = get_slurm_job_id(result.stdout)
    return job_id

_FOLDSEEK_URL = "https://search.foldseek.com/api"
_CACHE_DIR    = "/tmp/peat_structures"


def foldseek_search(pdb_id: str) -> list[dict]:
    """
    Search for structurally similar proteins using the Foldseek REST API.

    Steps:
      1. Cache PDB file in /tmp/peat_structures/, downloading from RCSB if absent.
      2. Submit to Foldseek against pdb100 + afdb50 databases.
      3. Poll the ticket endpoint until results are ready.
      4. Return top 10 hits sorted by TM-score (descending) as a list of dicts
         with keys: pdb_id, evalue, tm_score, sequence_identity, description.
    """
    pdb_id = pdb_id.upper()
    os.makedirs(_CACHE_DIR, exist_ok=True)

    # ── 1. Cache / download PDB file ────────────────────────────────────────────
    pdb_path = os.path.join(_CACHE_DIR, f"{pdb_id}.pdb")
    if not os.path.exists(pdb_path):
        r = requests.get(
            f"https://files.rcsb.org/download/{pdb_id}.pdb",
            timeout=30,
        )
        r.raise_for_status()
        with open(pdb_path, "wb") as fh:
            fh.write(r.content)

    # ── 2. Submit ticket ─────────────────────────────────────────────────────────
    with open(pdb_path, "rb") as fh:
        resp = requests.post(
            f"{_FOLDSEEK_URL}/ticket",
            data={"database[]": ["pdb100", "afdb50"], "mode": "3diaa"},
            files={"q": (f"{pdb_id}.pdb", fh, "chemical/x-pdb")},
            timeout=30,
        )
    resp.raise_for_status()
    ticket_id = resp.json()["id"]

    # ── 3. Poll until complete (max 5 min, 5 s intervals) ───────────────────────
    for _ in range(60):
        time.sleep(5)
        poll = requests.get(f"{_FOLDSEEK_URL}/ticket/{ticket_id}", timeout=15)
        poll.raise_for_status()
        status_data = poll.json()
        status = status_data.get("status", "")
        if status == "COMPLETE":
            break
        if status == "ERROR":
            raise RuntimeError(f"Foldseek job failed for {pdb_id}: {status_data}")
    else:
        raise TimeoutError(f"Foldseek job timed out after 5 minutes for {pdb_id}")

    # ── 4. Fetch results from the result endpoint ────────────────────────────────
    result_resp = requests.get(f"{_FOLDSEEK_URL}/result/{ticket_id}/0", timeout=30)
    result_resp.raise_for_status()
    data = result_resp.json()

    # ── 5. Parse hits ────────────────────────────────────────────────────────────
    hits = []
    for db_result in data.get("results", []):
        # alignments is a list-of-lists: one inner list per query sequence
        for query_alignments in db_result.get("alignments", []):
            if not isinstance(query_alignments, list):
                query_alignments = [query_alignments]
            for hit in query_alignments:
                hits.append({
                    "pdb_id":            hit.get("target", ""),
                    "evalue":            hit.get("eval"),
                    "prob":              hit.get("prob"),   # probability of true homology, 0-1
                    "sequence_identity": (hit["seqId"] / 100) if hit.get("seqId") is not None else None,
                    "description":       hit.get("taxName") or hit.get("description", ""),
                })

    hits.sort(key=lambda h: h["prob"] or 0.0, reverse=True)
    return hits[:10]


_ALPHAFOLD_URL = "https://alphafold.ebi.ac.uk/api/prediction"


def alphafold_fetch(uniprot_id: str) -> dict:
    """
    Fetch the AlphaFold structure for a UniProt ID.

    Steps:
      1. Call https://alphafold.ebi.ac.uk/api/prediction/{uniprot_id}.
      2. Download the PDB file to /tmp/peat_structures/AF-{uniprot_id}.pdb
         (cached — skips download if already present).
      3. Parse per-residue pLDDT confidence scores from the B-factor column.
      4. Return a dict with keys:
           pdb_path    — local path to the downloaded PDB file
           plddt_mean  — mean pLDDT across all residues
           plddt_min   — minimum pLDDT
           plddt_max   — maximum pLDDT
           entry_id    — AlphaFold entry ID (e.g. AF-Q9WYE2-F1)
           gene        — gene name from AlphaFold metadata
           organism    — organism scientific name
           description — UniProt protein description
    """
    os.makedirs(_CACHE_DIR, exist_ok=True)

    # ── 1. Fetch metadata ────────────────────────────────────────────────────────
    resp = requests.get(f"{_ALPHAFOLD_URL}/{uniprot_id}", timeout=15)
    resp.raise_for_status()
    entries = resp.json()
    if not entries:
        raise ValueError(f"No AlphaFold entry found for UniProt ID: {uniprot_id}")
    entry = entries[0]

    pdb_url  = entry.get("pdbUrl", "")
    if not pdb_url:
        raise ValueError(f"No pdbUrl in AlphaFold response for {uniprot_id}")

    # ── 2. Cache / download PDB ──────────────────────────────────────────────────
    pdb_path = os.path.join(_CACHE_DIR, f"AF-{uniprot_id}.pdb")
    if not os.path.exists(pdb_path):
        dl = requests.get(pdb_url, timeout=30)
        dl.raise_for_status()
        with open(pdb_path, "wb") as fh:
            fh.write(dl.content)
        pdb_content = dl.text
    else:
        with open(pdb_path) as fh:
            pdb_content = fh.read()

    # ── 3. Parse pLDDT from B-factor column (cols 60-66 of ATOM records) ────────
    plddt_scores = []
    seen_residues = set()
    for line in pdb_content.splitlines():
        if line.startswith("ATOM"):
            # Use only CA atoms to get one score per residue
            atom_name  = line[12:16].strip()
            chain      = line[21]
            res_seq    = line[22:26].strip()
            if atom_name == "CA":
                try:
                    plddt_scores.append(float(line[60:66]))
                    seen_residues.add((chain, res_seq))
                except ValueError:
                    pass

    plddt_mean = round(statistics.mean(plddt_scores), 2) if plddt_scores else None
    plddt_min  = round(min(plddt_scores), 2)             if plddt_scores else None
    plddt_max  = round(max(plddt_scores), 2)             if plddt_scores else None

    return {
        "pdb_path":    pdb_path,
        "plddt_mean":  plddt_mean,
        "plddt_min":   plddt_min,
        "plddt_max":   plddt_max,
        "entry_id":    entry.get("entryId", ""),
        "gene":        entry.get("gene", ""),
        "organism":    entry.get("organismScientificName", ""),
        "description": entry.get("uniprotDescription", ""),
    }
