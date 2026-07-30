import requests
import fitz  # PyMuPDF
import re
import os
import json


def get_pdb_data(pdb_id: str) -> dict:
    """
    Fetches RCSB PDB entry JSON and adds polymer entity info for a given PDB ID.
    """
    pdb_id = pdb_id.upper()
    url = f"https://data.rcsb.org/rest/v1/core/entry/{pdb_id}"
    r = requests.get(url)
    r.raise_for_status()
    entry = r.json()

    return entry

def get_uniprot_ids_from_sifts(pdb_id: str) -> list[str]:
    url = f"https://www.ebi.ac.uk/pdbe/api/mappings/uniprot/{pdb_id.lower()}"
    r = requests.get(url)
    if not r.ok:
        return []

    data = r.json().get(pdb_id.lower(), {}).get("UniProt", {})
    return list(data.keys()) 


def get_unpaywall_data(doi: str, email: str) -> dict | None:
    """
    Retrieves Unpaywall JSON for a given DOI.
    """
    url = f"https://api.unpaywall.org/v2/{doi}?email={email}"
    r = requests.get(url)
    if r.status_code != 200:
        return None
    return r.json()


def fetch_pdf_text(pdf_url: str, max_chars: int = 10000) -> str:
    try:
        r = requests.get(pdf_url, timeout=15)
        r.raise_for_status()
        with open("temp.pdf", "wb") as f:
            f.write(r.content)
        doc = fitz.open("temp.pdf")
        text = "".join(page.get_text() for page in doc)
        return text[:max_chars]
    except Exception:
        return ""


def chunk_pdf_sections(pdf_path: str) -> list[str]:
    """
    Splits PDF text into sections based on naive heading detection.
    """
    doc = fitz.open(pdf_path)
    full_text = "".join(page.get_text() for page in doc)
    # Simple split on all-caps headings
    sections = re.split(r"\n([A-Z ]{4,})\n", full_text)
    return sections



def fetch_uniprot_features(uniprot_id: str) -> dict:
    """
    Retrieves annotations from UniProt including features, comments, gene, and protein description.
    """
    url = f"https://rest.uniprot.org/uniprotkb/{uniprot_id}.json"
    try:
        r = requests.get(url)
        r.raise_for_status()
        data = r.json()
        return {
            "features": data.get("features", []),
            "comments": data.get("comments", []),
            "proteinDescription": data.get("proteinDescription", {}),
            "genes": data.get("genes", [])
        }
    except requests.exceptions.RequestException:
        return {
            "features": [],
            "comments": [],
            "proteinDescription": {},
            "genes": []
        }


def annotate_uniprot(uniprot_id: str, output_dir: str) -> dict:
    url = f"https://rest.uniprot.org/uniprotkb/{uniprot_id}.json"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()

    # protein name
    protein_desc = data.get("proteinDescription", {})
    protein_name = (
        protein_desc.get("recommendedName", {})
        .get("fullName", {})
        .get("value")
    )

    # organism
    organism = data.get("organism", {}).get("scientificName")

    # sequence length
    seq_length = data.get("sequence", {}).get("length")

    # function comments
    functions = []
    for comment in data.get("comments", []):
        if comment.get("commentType") == "FUNCTION":
            for text_obj in comment.get("texts", []):
                functions.append(text_obj.get("value"))

    # domains / regions / motifs / sites
    features = []
    for feat in data.get("features", []):
        ftype = feat.get("type")
        if ftype in ["Domain", "Region", "Motif", "Compositional bias", "Active site", "Binding site"]:
            loc = feat.get("location", {})
            start = loc.get("start", {}).get("value")
            end = loc.get("end", {}).get("value")

            features.append({
                "type": ftype,
                "description": feat.get("description"),
                "start": start,
                "end": end,
            })

    # PDB cross references
    pdb_entries = []

    for ref in data.get("uniProtKBCrossReferences", []):
        if ref.get("database") == "PDB":

            props = {p["key"]: p["value"] for p in ref.get("properties", [])}

            res = props.get("Resolution")

            pdb_entries.append({
                "pdb_id": ref.get("id"),
                "method": props.get("Method"),
                "resolution": (
                    float(res.replace(" A", "").replace("Å", ""))
                    if res and res != "-"
                    else None
                ),
                "chains": props.get("Chains"),
            })
    annotations= {
        "uniprot_id": data.get("primaryAccession", uniprot_id),
        "entry_name": data.get("uniProtkbId"),
        "protein_name": protein_name,
        "organism": organism,
        "sequence_length": seq_length,
        "function": functions,
        "features": features,
        "pdb_entries": pdb_entries,
    }
    with open(os.path.join(output_dir,f"{uniprot_id}_annotation.json"),"w") as f:
        json.dump(annotations, f, indent=4)
    
    return annotations


def get_pdb_id_from_sequence(sequence: str) -> str | None:
    # Clean sequence (in case it's in FASTA format)
    if sequence.startswith(">"):
        sequence = "\n".join(line for line in sequence.splitlines() if not line.startswith(">"))
        sequence = sequence.replace("\n", "")

    url = "https://search.rcsb.org/rcsbsearch/v2/query"
    query = {
        "query": {
            "type": "terminal",
            "service": "sequence",
            "parameters": {
                "evalue_cutoff": 1e-5,
                "target": "pdb_protein_sequence",
                "value": sequence
            }
        },
        "request_options": {
            "scoring_strategy": "sequence",
            "return_all_hits": False
        },
        "return_type": "entry"
    }

    try:
        r = requests.post(url, json=query)
        r.raise_for_status()
        result = r.json()
        hits = result.get("result_set", [])
        if hits:
            return hits[0]["identifier"]  # Return top hit
    except Exception as e:
        print(f"Sequence → PDB search failed: {e}")
    return None

def get_m_csa_active_sites(pdb_id: str) -> list[dict]:
    """
    Queries the M-CSA API for catalytic active site annotations.
    """
    url = f"https://www.ebi.ac.uk/thornton-srv/m-csa/rest/structure/{pdb_id.upper()}"
    r = requests.get(url)
    if r.ok:
        return r.json().get("activeSites", [])
    return []
