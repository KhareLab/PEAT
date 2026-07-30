import requests
import time

from Bio.Blast import NCBIWWW, NCBIXML
from Bio.Align import MultipleSeqAlignment
from collections import Counter



import time
import requests
import xml.etree.ElementTree as ET
import re

import time
import xml.etree.ElementTree as ET

import requests


def run_ebi_blast(sequence_file: str, email: str) -> list[str]:
    base = "https://www.ebi.ac.uk/Tools/services/rest/ncbiblast"

    with open(sequence_file, "r", encoding="utf-8") as f:
        fasta = f.read().strip()

    if not fasta:
        raise ValueError("The sequence file is empty.")

    lines = fasta.splitlines()

    if lines[0].startswith(">"):
        header = lines[0]
        sequence_lines = lines[1:]
    else:
        header = ">query"
        sequence_lines = lines

    sequence = "".join(
        line.strip()
        for line in sequence_lines
        if line.strip()
    )

    if not sequence:
        raise ValueError("No protein sequence was found in the file.")

    print(f"Submitting BLAST query for {header}")
    print(f"Sequence length: {len(sequence)}")

    # Submit BLAST job
    response = requests.post(
        f"{base}/run",
        data={
            "email": email,
            "program": "blastp",
            "database": "uniprotkb",
            "sequence": sequence,
            "stype": "protein",
            "alignments": 5,
        },
        timeout=60,
    )

    if not response.ok:
        raise RuntimeError(
            f"BLAST submission failed with HTTP "
            f"{response.status_code}: {response.text}"
        )

    job_id = response.text.strip()

    if not job_id:
        raise RuntimeError("EBI BLAST returned an empty job ID.")

    print(f"Job ID: {job_id}")

    # Poll until BLAST finishes
    while True:
        status_response = requests.get(
            f"{base}/status/{job_id}",
            timeout=30,
        )
        status_response.raise_for_status()

        status = status_response.text.strip().upper()
        print(f"Status: {status}")

        if status == "FINISHED":
            break

        if status in {"ERROR", "FAILURE", "NOT_FOUND"}:
            raise RuntimeError(
                f"BLAST job {job_id} failed with status: {status}"
            )

        time.sleep(5)

    # Retrieve XML result
    result_response = requests.get(
        f"{base}/result/{job_id}/xml",
        timeout=60,
    )
    result_response.raise_for_status()

    try:
        root = ET.fromstring(result_response.content)
    except ET.ParseError as exc:
        preview = result_response.text[:1000]

        raise RuntimeError(
            "Could not parse EBI BLAST XML response.\n"
            f"Response preview:\n{preview}"
        ) from exc

    hits: list[str] = []

    # EBI XML typically stores accessions in hit attributes
    for hit in root.iter():
        tag_name = hit.tag.split("}")[-1].lower()

        if tag_name != "hit":
            continue

        accession = (
            hit.get("ac")
            or hit.get("accession")
            or hit.get("id")
        )

        if not accession:
            continue

        accession = accession.strip()

        # Handle formats such as:
        # sp|P12345|ENTRY_NAME
        # tr|A0A123|ENTRY_NAME
        # SP:P12345
        if "|" in accession:
            parts = accession.split("|")

            if len(parts) >= 2:
                accession = parts[1]

        elif ":" in accession:
            accession = accession.split(":", 1)[1]

        if accession and accession not in hits:
            hits.append(accession)

    # Fallback: search all XML elements for accession-like attributes
    if not hits:
        for element in root.iter():
            accession = (
                element.get("ac")
                or element.get("accession")
            )

            if not accession:
                continue

            accession = accession.strip()

            if "|" in accession:
                parts = accession.split("|")

                if len(parts) >= 2:
                    accession = parts[1]

            elif ":" in accession:
                accession = accession.split(":", 1)[1]

            if accession and accession not in hits:
                hits.append(accession)

    print(f"{header} BLAST hits: {hits}")

    if not hits:
        xml_preview = result_response.text[:3000]

        raise RuntimeError(
            "BLAST finished successfully, but no UniProt accessions "
            "could be extracted from the XML result.\n"
            f"Job ID: {job_id}\n"
            f"XML preview:\n{xml_preview}"
        )

    return hits
def run_blast(sequence: str, program: str = "blastp", database: str = "nr"):
    """
    Runs NCBI BLAST for the input sequence and returns the parsed record.
    """
    result_handle = NCBIWWW.qblast(program, database, sequence)
    return NCBIXML.read(result_handle)


def conservation_scores(msa: MultipleSeqAlignment) -> list[float]:
    """
    Computes per-residue conservation scores from an MSA.
    """
    length = msa.get_alignment_length()
    scores = []
    for i in range(length):
        col = [rec.seq[i] for rec in msa]
        freq = Counter(col)
        top_count = freq.most_common(1)[0][1]
        scores.append(top_count / len(col))
    return scores