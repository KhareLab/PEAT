import re
import os

from graph.state import PEATState

# ── Compiled regex patterns ───────────────────────────────────────────────────

_MINIMIZE_RE = re.compile(
    r'^\s*'
    r'(?:run\s+)?'
    r'(?:energy\s+)?'
    r'minimi[sz]e?\s+'
    r'(?:on\s+)?(?:pdb\s+)?'
    r'([0-9][A-Za-z0-9]{3})'
    r'\s*$'
    r'|^\s*run\s+energy\s+minimization\s+(?:on\s+)?(?:pdb\s+)?([0-9][A-Za-z0-9]{3})\s*$',
    re.IGNORECASE,
)

_CHECK_JOB_RE = re.compile(
    r'^\s*(?:check|job|query)\s+(?:job\s+|status\s+)?(\d+)\s*$',
    re.IGNORECASE,
)

_DOWNLOAD_RE = re.compile(
    r'^\s*(?:download|get|fetch)\s+results?\s+(\d+)\s*$',
    re.IGNORECASE,
)

_ALPHAFOLD_RE = re.compile(
    r'^\s*'
    r'(?:(?:fetch|get|show|load)\s+)?'
    r'(?:alphafold|af2|af)\s+'
    r'(?:for\s+)?'
    r'([A-Za-z0-9]+)'
    r'\s*$',
    re.IGNORECASE,
)

_FOLDSEEK_RE = re.compile(
    r'(?:^|.*\b)foldseek\b.*?(?:on|for|against|search)?\s+(?:pdb\s+)?([0-9][A-Za-z0-9]{3})\b'
    r'|(?:find\s+similar(?:\s+structures?)?(?:\s+to)?'
    r'|similar\s+structures?(?:\s+to)?'
    r'|structure\s+search(?:\s+for)?)\s+(?:pdb\s+)?([0-9][A-Za-z0-9]{3})\b',
    re.IGNORECASE,
)

_ANALYZE_RE = re.compile(
    r'^\s*'
    r'(?:(?:analyze|analyse|fetch|show|load|look\s*up|get|examine|study|open|run)\s+)?'
    r'(?:pdb\s+)?'
    r'([0-9][A-Za-z0-9]{3})'
    r'\s*$',
    re.IGNORECASE,
)

_HPC_PREFIXES = {
    "gmx", "python", "python3", "bash", "sh",
    "squeue", "sacct", "sbatch", "scancel",
    "echo", "ls", "cat", "head", "tail",
}

_AA_CHARS     = set("ACDEFGHIKLMNPQRSTVWY")
_AA_THRESHOLD = 0.80
_MIN_SEQ_LEN  = 20


# ── Individual intent parsers (encapsulated, individually testable) ───────────

def is_hpc_command(text: str) -> bool:
    parts = text.strip().split()
    return bool(parts) and parts[0].lower() in _HPC_PREFIXES


def parse_minimize_request(text: str) -> str | None:
    m = _MINIMIZE_RE.match(text)
    if not m:
        return None
    return (m.group(1) or m.group(2)).upper()


def parse_check_job(text: str) -> str | None:
    m = _CHECK_JOB_RE.match(text)
    return m.group(1) if m else None


def parse_download_results(text: str) -> str | None:
    m = _DOWNLOAD_RE.match(text)
    return m.group(1) if m else None


def parse_alphafold_request(text: str) -> str | None:
    m = _ALPHAFOLD_RE.match(text)
    return m.group(1).upper() if m else None

def parse_alphafold3_request(text: str) -> str | None:
    m = re.search(
        r'^\s*'
        r'(?:(?:fetch|get|show|load)\s+)?'
        r'(?:alphafold3|af3)\s+'
        r'(?:for\s+)?'
        r'([A-Za-z0-9]+)'
        r'\s*$',
        text,
        re.IGNORECASE,
    )
    return m.group(1).upper() if m else None


def parse_foldseek_request(text: str) -> str | None:
    m = _FOLDSEEK_RE.search(text)
    if not m:
        return None
    return (m.group(1) or m.group(2)).upper()

def parse_proteinmpnn_request(text: str) -> str | None:
    """Parse a request for ProteinMPNN analysis from the input text."""
    # This is a placeholder regex; adjust as needed for actual request patterns
    m = re.search(r'proteinmpnn\s+([0-9][A-Za-z0-9]{3})', text, re.IGNORECASE)
    return m.group(1).upper() if m else None


def parse_analyze_request(text: str) -> str | None:
    m = _ANALYZE_RE.match(text)
    return m.group(1).upper() if m else None


def parse_sequence_input(text: str) -> str | None:
    """
    Return cleaned AA sequence if the message is a FASTA block or raw AA string.

    Rules (strict to avoid misrouting plain English):
    - FASTA: requires a line starting with '>'; header lines stripped and sequence validated.
    - Raw: no spaces or tabs; >= 80% valid amino acid characters; >= 20 characters.
    """
    stripped = text.strip()
    lines    = stripped.splitlines()

    if any(l.startswith(">") for l in lines):
        seq = "".join(l.strip() for l in lines if not l.startswith(">")).upper()
        if len(seq) < _MIN_SEQ_LEN:
            return None
        if sum(1 for c in seq if c in _AA_CHARS) / len(seq) >= _AA_THRESHOLD:
            return seq
        return None

    if " " in stripped or "\t" in stripped:
        return None
    seq = stripped.upper()
    if len(seq) < _MIN_SEQ_LEN:
        return None
    if sum(1 for c in seq if c in _AA_CHARS) / len(seq) >= _AA_THRESHOLD:
        return seq
    return None

def parse_stabilization_request(
    text: str,
) -> tuple[str, list[int]] | None:
    """
    Parse stabilization requests such as:

        stabilization PsMan8a
        stabilization PsMan8a 12, 34, 43, 456
        stabilization for PsMan8a 12 34 43 456
        run stabilization on PsMan8a active sites 12, 34, 43
    """
    m = re.fullmatch(
        r'\s*'
        r'(?:(?:run|start|perform)\s+)?'
        r'stabili[sz]ation\s+'
        r'(?:(?:for|on)\s+)?'
        r'(?P<target>[A-Za-z0-9_.-]+)'
        r'(?:\s+(?:active\s+sites?\s*)?'
        r'(?P<sites>\d+(?:\s*,?\s*\d+)*))?'
        r'\s*',
        text,
        re.IGNORECASE,
    )

    if not m:
        return None

    target_name = m.group("target")
    sites_text = m.group("sites")

    if sites_text:
        active_sites = [
            int(site)
            for site in re.findall(r"\d+", sites_text)
        ]
    else:
        active_sites = []

    return target_name, active_sites

# ── Router node ───────────────────────────────────────────────────────────────

def router(state: PEATState) -> dict:
    """Determine intent and extract routing parameters from the raw prompt."""
    prompt           = state["raw_prompt"]
    analyzed_pdb_ids = state.get("analyzed_pdb_ids") or []
    print("Router: Determining intent for prompt:")

    if is_hpc_command(prompt):
        print("Detected HPC command.")
        return {"intent": "hpc", "hpc_command": prompt.strip()}

    minimize_id = parse_minimize_request(prompt)
    if minimize_id:
        print("Detected energy minimization request.")
        return {"intent": "minimize", "pdb_id": minimize_id}

    check_id = parse_check_job(prompt)
    if check_id:
        print("Detected job check request.")
        return {"intent": "check_job", "job_id": check_id}

    download_id = parse_download_results(prompt)
    if download_id:
        print("Detected download results request.")
        return {"intent": "download", "job_id": download_id}

    af_id = parse_alphafold_request(prompt)
    if af_id:
        print("Detected AlphaFold request.")
        return {"intent": "alphafold", "uniprot_id": af_id}
    
    af3_id = parse_alphafold3_request(prompt)
    if af3_id:
        print("Detected AlphaFold3 request.")
        return {"intent": "alphafold3", "uniprot_id": af3_id}

    fs_id = parse_foldseek_request(prompt)
    if fs_id:
        print("Detected Foldseek request.")
        return {"intent": "foldseek", "pdb_id": fs_id}

    stabilization_request = parse_stabilization_request(prompt)
    if stabilization_request:
        target_name, active_sites = stabilization_request

        print("Detected stabilization request.")
        print(f"Stabilization target: {target_name}")
        print(f"Active sites: {active_sites}")

        return {
            "intent": "stabilization",
            "target_name": target_name,
            "m_csa_sites": active_sites,
        }

    mpnn_id = parse_proteinmpnn_request(prompt)
    if mpnn_id:
        file_path = "data/uploaded_files"  # Adjust this path as needed
        if not os.path.exists(file_path):
            return {"response_text": f"ProteinMPNN request detected, but the required file path '{file_path}' does not exist."}
        print("Detected ProteinMPNN request.")
        return {"intent": "protein_mpnn", "pdb_id": mpnn_id}

    pdb_id = parse_analyze_request(prompt)
    if pdb_id:
        print("Detected PDB analysis request.")
        # Already analyzed — let the LLM answer from conversation context
        if pdb_id in analyzed_pdb_ids:
            return {"intent": "llm_qa", "pdb_id": pdb_id}
        return {"intent": "analyze", "pdb_id": pdb_id}

    seq = parse_sequence_input(prompt)
    if seq:
        print("Detected raw sequence input.")
        return {"intent": "sequence", "sequence": seq}

    return {"intent": "llm_qa"}


def route_by_intent(state: PEATState) -> str:
    """Conditional edge: read intent from state and return the target node name."""
    return state["intent"]
