import re

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


def parse_foldseek_request(text: str) -> str | None:
    m = _FOLDSEEK_RE.search(text)
    if not m:
        return None
    return (m.group(1) or m.group(2)).upper()


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


# ── Router node ───────────────────────────────────────────────────────────────

def router(state: PEATState) -> dict:
    """Determine intent and extract routing parameters from the raw prompt."""
    prompt           = state["raw_prompt"]
    analyzed_pdb_ids = state.get("analyzed_pdb_ids") or []

    if is_hpc_command(prompt):
        return {"intent": "hpc", "hpc_command": prompt.strip()}

    minimize_id = parse_minimize_request(prompt)
    if minimize_id:
        return {"intent": "minimize", "pdb_id": minimize_id}

    check_id = parse_check_job(prompt)
    if check_id:
        return {"intent": "check_job", "job_id": check_id}

    download_id = parse_download_results(prompt)
    if download_id:
        return {"intent": "download", "job_id": download_id}

    af_id = parse_alphafold_request(prompt)
    if af_id:
        return {"intent": "alphafold", "uniprot_id": af_id}

    fs_id = parse_foldseek_request(prompt)
    if fs_id:
        return {"intent": "foldseek", "pdb_id": fs_id}

    pdb_id = parse_analyze_request(prompt)
    if pdb_id:
        # Already analyzed — let the LLM answer from conversation context
        if pdb_id in analyzed_pdb_ids:
            return {"intent": "llm_qa", "pdb_id": pdb_id}
        return {"intent": "analyze", "pdb_id": pdb_id}

    seq = parse_sequence_input(prompt)
    if seq:
        return {"intent": "sequence", "sequence": seq}

    return {"intent": "llm_qa"}


def route_by_intent(state: PEATState) -> str:
    """Conditional edge: read intent from state and return the target node name."""
    return state["intent"]
