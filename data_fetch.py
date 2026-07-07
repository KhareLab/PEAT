import requests
import fitz  # PyMuPDF
import re


def get_pdb_data(pdb_id: str) -> dict:
    """
    Fetches RCSB PDB entry JSON and adds polymer entity info for a given PDB ID.
    """
    pdb_id = pdb_id.upper()
    url = f"https://data.rcsb.org/rest/v1/core/entry/{pdb_id}"
    r = requests.get(url, timeout=10)
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


def get_unpaywall_data(doi: str, email: str, timeout: int = 10) -> dict | None:
    """
    Retrieves Unpaywall JSON for a given DOI. Never raises — returns None on
    any network error, non-200 status, or malformed response.
    """
    url = f"https://api.unpaywall.org/v2/{doi}?email={email}"
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()
    except (requests.RequestException, ValueError):
        return None


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


def get_m_csa_active_sites(pdb_id: str) -> list[dict]:
    """
    Queries the M-CSA API for catalytic active site annotations.
    """
    url = f"https://www.ebi.ac.uk/thornton-srv/m-csa/rest/structure/{pdb_id.upper()}"
    r = requests.get(url)
    if r.ok:
        return r.json().get("activeSites", [])
    return []


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
