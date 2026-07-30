from __future__ import annotations

import requests
import re
import json
from typing import Any


import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

import fitz  # PyMuPDF
from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

import requests


import json
from pathlib import Path
from typing import Any

import requests


# ---------------------------------------------------------------------------
# URLs and cache
# ---------------------------------------------------------------------------

RESIDUES_URL = (
    "https://www.ebi.ac.uk/thornton-srv/"
    "m-csa/api/residues/?format=json"
)

HOMOLOGUES_URL = (
    "https://www.ebi.ac.uk/thornton-srv/"
    "m-csa/api/homologues_residues.json"
)

CACHE_PATH = Path("data/mcsa_homologues_residues.json")


# ---------------------------------------------------------------------------
# Download/cache the large homologue file
# ---------------------------------------------------------------------------

def load_homologues_data() -> Any:
    """
    Download the M-CSA homologue dataset once and cache it locally.

    Warning:
        The downloaded JSON file is larger than 200 MB.
    """
    if not CACHE_PATH.exists():
        CACHE_PATH.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        print("Downloading M-CSA homologue data...")
        print("This file is larger than 200 MB and may take a while.")

        with requests.get(
            HOMOLOGUES_URL,
            timeout=600,
            stream=True,
        ) as response:
            response.raise_for_status()

            with CACHE_PATH.open("wb") as file:
                for chunk in response.iter_content(
                    chunk_size=1024 * 1024,
                ):
                    if chunk:
                        file.write(chunk)

        print(f"Saved homologue data to: {CACHE_PATH}")

    print("Loading cached M-CSA homologue data...")

    with CACHE_PATH.open(
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


# ---------------------------------------------------------------------------
# General recursive helpers
# ---------------------------------------------------------------------------

def object_contains_exact_string(
    obj: Any,
    target: str,
) -> bool:
    """
    Return True when an exact string occurs anywhere in a nested object.
    """
    normalized_target = target.strip().lower()

    if isinstance(obj, dict):
        return any(
            object_contains_exact_string(
                value,
                normalized_target,
            )
            for value in obj.values()
        )

    if isinstance(obj, list):
        return any(
            object_contains_exact_string(
                item,
                normalized_target,
            )
            for item in obj
        )

    if isinstance(obj, str):
        return obj.strip().lower() == normalized_target

    return False


def find_mcsa_ids(obj: Any) -> set[int]:
    """
    Recursively collect M-CSA IDs from a nested object.

    Handles key variations such as:
        mcsa_id
        mcsa_ids
        m-csa_id
    """
    found: set[int] = set()

    if isinstance(obj, dict):
        for key, value in obj.items():
            normalized_key = (
                str(key)
                .lower()
                .replace("-", "")
                .replace("_", "")
            )

            if normalized_key in {"mcsaid", "mcsaids"}:
                if isinstance(value, list):
                    for item in value:
                        try:
                            found.add(int(item))
                        except (TypeError, ValueError):
                            pass
                else:
                    try:
                        found.add(int(value))
                    except (TypeError, ValueError):
                        pass

            found.update(find_mcsa_ids(value))

    elif isinstance(obj, list):
        for item in obj:
            found.update(find_mcsa_ids(item))

    return found


# ---------------------------------------------------------------------------
# PDB → M-CSA IDs
# ---------------------------------------------------------------------------

def get_mcsa_ids_from_pdb(
    pdb_id: str,
    debug: bool = False,
) -> list[int]:
    """
    Find all M-CSA entries associated with a reference or homologous PDB.
    """
    pdb_id = pdb_id.strip().lower()
    data = load_homologues_data()

    # Handle either a raw list or a dictionary containing a list.
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = (
            data.get("results")
            or data.get("data")
            or data.get("homologues")
            or []
        )

        # If no known wrapper key exists, search the full dictionary.
        if not rows:
            rows = [data]
    else:
        raise TypeError(
            "Unexpected homologue-data type: "
            f"{type(data).__name__}"
        )

    mcsa_ids: set[int] = set()
    matching_rows = 0

    for row in rows:
        if not object_contains_exact_string(row, pdb_id):
            continue

        matching_rows += 1
        row_ids = find_mcsa_ids(row)
        mcsa_ids.update(row_ids)

        if debug:
            print("\nMatching homologue row:")
            print(
                json.dumps(
                    row,
                    indent=2,
                )[:5000]
            )
            print("M-CSA IDs found in row:", row_ids)

    if debug:
        print(
            f"\nRows containing {pdb_id.upper()}: "
            f"{matching_rows}"
        )

    return sorted(mcsa_ids)


# ---------------------------------------------------------------------------
# M-CSA ID → curated reference residues
# ---------------------------------------------------------------------------

def get_residues_from_mcsa_id(
    mcsa_id: int,
) -> list[tuple[int, str, str | None, str | None]]:
    """
    Return curated reference residues for an M-CSA entry.

    Each result is:
        (
            residue_number,
            amino_acid,
            chain,
            reference_pdb,
        )
    """
    response = requests.get(
        RESIDUES_URL,
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()

    if isinstance(data, dict):
        rows = data.get("results", [])
    elif isinstance(data, list):
        rows = data
    else:
        raise TypeError(
            "Unexpected residue-data type: "
            f"{type(data).__name__}"
        )

    residue_records: set[
        tuple[int, str, str | None, str | None]
    ] = set()

    for row in rows:
        row_mcsa_id = row.get("mcsa_id")

        try:
            row_mcsa_id = int(row_mcsa_id)
        except (TypeError, ValueError):
            continue

        if row_mcsa_id != mcsa_id:
            continue

        for chain in row.get("residue_chains", []):
            # Keep only curated reference-chain annotations.
            if chain.get("is_reference") is False:
                continue

            residue_number = chain.get("auth_resid")
            amino_acid = chain.get("code")

            if residue_number is None or amino_acid is None:
                continue

            chain_name = chain.get("chain_name")
            reference_pdb = chain.get("pdb_id")

            residue_records.add(
                (
                    int(residue_number),
                    str(amino_acid).title(),
                    (
                        str(chain_name)
                        if chain_name is not None
                        else None
                    ),
                    (
                        str(reference_pdb).upper()
                        if reference_pdb is not None
                        else None
                    ),
                )
            )

    return sorted(
        residue_records,
        key=lambda record: (
            record[3] or "",
            record[2] or "",
            record[0],
        ),
    )


# ---------------------------------------------------------------------------
# Full PDB → M-CSA → reference residues pipeline
# ---------------------------------------------------------------------------

def get_m_csa_active_sites(
    pdb_id: str,
    debug: bool = False,
) -> tuple[list[int], list[str]]:
    """
    Map any M-CSA-associated PDB to its M-CSA entry and retrieve the
    manually curated catalytic residues from that entry's reference PDB.

    Important:
        Returned numbering belongs to the reference PDB, not necessarily
        the input homologous PDB.
    """
    pdb_id = pdb_id.strip().upper()

    mcsa_ids = get_mcsa_ids_from_pdb(
        pdb_id,
        debug=debug,
    )

    if not mcsa_ids:
        print(
            f"No M-CSA entry found for PDB {pdb_id}."
        )
        return [], []

    print(
        f"PDB {pdb_id} maps to M-CSA "
        f"entries: {mcsa_ids}"
    )

    all_records: set[
        tuple[int, str, str | None, str | None]
    ] = set()

    for mcsa_id in mcsa_ids:
        records = get_residues_from_mcsa_id(
            mcsa_id
        )

        if not records:
            print(
                f"No curated residues found for "
                f"M-CSA entry {mcsa_id}."
            )
            continue

        reference_pdbs = sorted(
            {
                reference_pdb
                for _, _, _, reference_pdb in records
                if reference_pdb is not None
            }
        )

        print(
            f"M-CSA entry {mcsa_id} reference "
            f"PDB(s): {reference_pdbs}"
        )

        all_records.update(records)

    sorted_records = sorted(
        all_records,
        key=lambda record: (
            record[3] or "",
            record[2] or "",
            record[0],
        ),
    )

    catalytic_locations = sorted(
        {
            residue_number
            for (
                residue_number,
                _,
                _,
                _,
            ) in sorted_records
        }
    )

    amino_acid_locations = []

    for (
        residue_number,
        amino_acid,
        chain,
        reference_pdb,
    ) in sorted_records:
        label = f"{amino_acid}{residue_number}"

        if chain:
            label += chain

        amino_acid_locations.append(label)

        if debug:
            print(
                f"{reference_pdb}: {label}"
            )

    # Remove duplicate labels while keeping sorted order.
    amino_acid_locations = sorted(
        set(amino_acid_locations),
        key=lambda label: (
            int(
                "".join(
                    character
                    for character in label
                    if character.isdigit()
                )
                or 0
            ),
            label,
        ),
    )

    return (
        catalytic_locations,
        amino_acid_locations,
    )



MCSA_ENTRIES_URL = "https://www.ebi.ac.uk/thornton-srv/m-csa/api/entries/?format=json"







def normalize_doi(doi: str) -> str:
    doi = doi.strip().lower()
    doi = doi.replace("https://doi.org/", "")
    doi = doi.replace("http://doi.org/", "")
    doi = doi.replace("doi:", "")
    return doi.strip()


def contains_doi(obj: Any, doi: str) -> bool:
    """
    Recursively checks if a DOI appears anywhere inside a nested dict/list/string.
    """
    doi = normalize_doi(doi)

    if isinstance(obj, dict):
        return any(contains_doi(value, doi) for value in obj.values())

    if isinstance(obj, list):
        return any(contains_doi(item, doi) for item in obj)

    if isinstance(obj, str):
        return doi in normalize_doi(obj)

    return False


def get_mcsa_catalytic_residues_from_doi(doi: str) -> tuple[list[int], list[str]]:
    """
    Takes a DOI and returns catalytic residues from matching M-CSA entries.

    Returns:
        catalytic_locations:
            Example: [7, 8, 70, 147, 178, 180]

        amino_acid_locations:
            Example: ["Asp7", "Ser8", "Cys70", "Glu147", "Cys178", "His180"]
    """
    doi = normalize_doi(doi)

    url = MCSA_ENTRIES_URL
    residue_pairs = []

    while url:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()

        entries = data.get("results", [])
        url = data.get("next")

        for entry in entries:
            if not contains_doi(entry, doi):
                continue

            for residue in entry.get("residues", []):
                for chain in residue.get("residue_chains", []):
                    aa = chain.get("code")
                    loc = chain.get("auth_resid")

                    if aa is not None and loc is not None:
                        residue_pairs.append((loc, aa))

    residue_pairs = sorted(set(residue_pairs), key=lambda x: x[0])

    catalytic_locations = [loc for loc, aa in residue_pairs]
    amino_acid_locations = [f"{aa}{loc}" for loc, aa in residue_pairs]

    return catalytic_locations, amino_acid_locations



load_dotenv()


# ---------------------------------------------------------------------------
# Structured output schemas
# ---------------------------------------------------------------------------

class CatalyticResidue(BaseModel):
    residue_name: str = Field(
        description=(
            "Three-letter amino-acid code such as GLU, ASP, HIS, "
            "or unknown"
        )
    )
    residue_number: int = Field(
        description="Residue number exactly as reported in the paper"
    )
    chain: str | None = Field(
        description="Chain identifier if stated, otherwise null"
    )
    protein: str | None = Field(
        description=(
            "Protein, construct, homolog, or structure to which this "
            "numbering applies"
        )
    )
    role: str | None = Field(
        description=(
            "Catalytic role, such as nucleophile, acid, base, "
            "charge stabilization, or metal-binding residue"
        )
    )
    evidence: str = Field(
        description=(
            "A concise excerpt or paraphrase supporting the assignment"
        )
    )
    page: int | None = Field(
        description="PDF page containing the evidence, if known"
    )
    confidence: Literal["high", "medium", "low"]
    explicitly_catalytic: bool = Field(
        description=(
            "True only when the paper directly identifies the residue "
            "as catalytic or functionally essential"
        )
    )


class CatalyticResidueResult(BaseModel):
    residues: list[CatalyticResidue]
    notes: list[str] = Field(
        default_factory=list,
        description=(
            "Numbering ambiguities, missing information, or reasons "
            "no residues were found"
        ),
    )


# ---------------------------------------------------------------------------
# Model setup
# ---------------------------------------------------------------------------

def create_model():
    """
    Create a provider-independent LangChain chat model.

    Examples of provider values:
        openai
        anthropic
        google_genai
        ollama
    """
    provider = os.getenv("LLM_PROVIDER")
    model_name = os.getenv("LLM_MODEL")

    if not provider:
        raise RuntimeError(
            "LLM_PROVIDER is missing from the environment."
        )

    if not model_name:
        raise RuntimeError(
            "LLM_MODEL is missing from the environment."
        )

    return init_chat_model(
        model=model_name,
        model_provider=provider,
        timeout=180,
        max_retries=2,
    )


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def extract_pdf_pages(
    pdf_path: str | Path,
) -> list[Document]:
    """
    Extract each PDF page as a LangChain Document.

    PDF page numbers are stored as one-indexed metadata.
    """
    path = Path(pdf_path)

    if not path.is_file():
        raise FileNotFoundError(f"PDF does not exist: {path}")

    pages: list[Document] = []

    with fitz.open(path) as pdf:
        for page_index, page in enumerate(pdf):
            text = page.get_text("text").strip()

            if not text:
                continue

            pages.append(
                Document(
                    page_content=text,
                    metadata={
                        "source": str(path),
                        "page": page_index + 1,
                    },
                )
            )

    return pages


# ---------------------------------------------------------------------------
# Relevant-passage selection
# ---------------------------------------------------------------------------

# These expressions search for catalytic evidence.
#
# Generic residue patterns such as His225 or A123G are intentionally absent.
# Those patterns created hundreds of overlapping windows in your old code.
CATALYTIC_PATTERN = re.compile(
    "|".join(
        [
            r"\bcataly\w*",
            r"\bactive[\s-]?site\b",
            r"\bsite[\s-]?directed mutagen\w*",
            r"\bmutagen\w*",
            r"\bmechanis\w*",
            r"\bnucleophile\b",
            r"\bgeneral[\s-]?acid\b",
            r"\bgeneral[\s-]?base\b",
            r"\bresidual activity\b",
            r"\benzymatic activity\b",
            r"\bessential for catalysis\b",
            r"\bfunctionally essential\b",
            r"\bprotonat\w*",
            r"\bdeprotonat\w*",
            r"\bcharge neutralization\b",
            r"\bstabilization of .{0,40}intermediate\b",
        ]
    ),
    re.IGNORECASE,
)


def merge_ranges(
    ranges: list[tuple[int, int]],
    merge_gap: int = 200,
) -> list[tuple[int, int]]:
    """
    Merge ranges that overlap or are very close together.

    The returned ranges are sorted and non-overlapping.
    """
    if not ranges:
        return []

    ranges = sorted(ranges)
    merged: list[list[int]] = []

    for start, end in ranges:
        if not merged:
            merged.append([start, end])
            continue

        previous_start, previous_end = merged[-1]

        if start <= previous_end + merge_gap:
            merged[-1][1] = max(previous_end, end)
        else:
            merged.append([start, end])

    return [(start, end) for start, end in merged]


def find_relevant_passages(
    pages: list[Document],
    context_chars: int = 900,
    merge_gap: int = 200,
) -> list[Document]:
    """
    Find catalysis-related text and merge overlapping windows.

    Each source character from a page can occur in at most one returned
    passage. Therefore, the same source text is never repeatedly included
    because of nearby regex matches.
    """
    passages: list[Document] = []

    for page_document in pages:
        text = page_document.page_content
        candidate_ranges: list[tuple[int, int]] = []

        for match in CATALYTIC_PATTERN.finditer(text):
            start = max(0, match.start() - context_chars)
            end = min(len(text), match.end() + context_chars)
            candidate_ranges.append((start, end))

        merged_ranges = merge_ranges(
            candidate_ranges,
            merge_gap=merge_gap,
        )

        for passage_index, (start, end) in enumerate(
            merged_ranges,
            start=1,
        ):
            passages.append(
                Document(
                    page_content=text[start:end],
                    metadata={
                        **page_document.metadata,
                        "passage": passage_index,
                        "start_char": start,
                        "end_char": end,
                    },
                )
            )

    return passages


def validate_no_overlaps(
    pages: list[Document],
    passages: list[Document],
) -> None:
    """
    Verify that selected ranges from the same page do not overlap.

    This catches a regression before an expensive API call occurs.
    """
    ranges_by_page: dict[int, list[tuple[int, int]]] = defaultdict(list)

    for passage in passages:
        page = int(passage.metadata["page"])
        start = int(passage.metadata["start_char"])
        end = int(passage.metadata["end_char"])

        ranges_by_page[page].append((start, end))

    for page, ranges in ranges_by_page.items():
        ranges.sort()

        for previous, current in zip(ranges, ranges[1:]):
            previous_start, previous_end = previous
            current_start, current_end = current

            if current_start < previous_end:
                raise RuntimeError(
                    "Overlapping passages detected on "
                    f"page {page}: "
                    f"{previous_start}-{previous_end} and "
                    f"{current_start}-{current_end}"
                )

    full_text_chars = sum(
        len(page.page_content)
        for page in pages
    )

    selected_text_chars = sum(
        len(passage.page_content)
        for passage in passages
    )

    # Because ranges cannot overlap, selected source text should never be
    # larger than the original PDF text.
    if selected_text_chars > full_text_chars:
        raise RuntimeError(
            "Selected text is larger than the original PDF. "
            "Passage duplication may have occurred."
        )


def format_passages(
    passages: list[Document],
) -> str:
    """
    Add page labels to selected passages for the LLM.
    """
    formatted: list[str] = []

    for passage in passages:
        page = passage.metadata["page"]
        passage_number = passage.metadata["passage"]
        start = passage.metadata["start_char"]
        end = passage.metadata["end_char"]

        formatted.append(
            f"--- PDF PAGE {page}, PASSAGE {passage_number}, "
            f"CHARS {start}-{end} ---\n"
            f"{passage.page_content}"
        )

    return "\n\n".join(formatted)


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """
You extract experimentally supported catalytic residues from scientific
papers.

Return a residue only when the supplied text directly describes the residue
as catalytic, mechanistically involved, or experimentally essential for
catalysis.

Do not infer that a residue is catalytic merely because it is:

- conserved,
- close to a ligand,
- in an active-site pocket,
- involved only in substrate recognition,
- mentioned in a mutation,
- or aligned to a catalytic residue in another protein.

Preserve the paper's original residue numbering.

Never transfer or convert residue numbering between proteins unless the
supplied text explicitly provides that mapping.

Distinguish the target protein from homologs, templates, substrates,
antibodies, mutants, and comparison structures.

When evidence is uncertain, use low confidence or omit the residue.
"""


def extract_catalytic_residues_with_llm(
    model,
    paper_text: str,
    target_protein: str | None = None,
) -> tuple[CatalyticResidueResult, dict[str, Any] | None]:
    """
    Perform one LangChain model call with Pydantic structured output.

    Returns:
        Parsed CatalyticResidueResult
        Token-usage metadata, when supplied by the provider
    """
    if target_protein:
        target_instruction = (
            f"The target protein or structure is {target_protein}. "
            "Only return residues belonging to this target unless another "
            "protein is mentioned in the notes to clarify an ambiguity."
        )
    else:
        target_instruction = (
            "Identify which protein or structure each residue belongs to."
        )

    user_prompt = (
        f"{target_instruction}\n\n"
        "Extract all supported catalytic residues from these "
        "page-labelled passages:\n\n"
        f"{paper_text}"
    )

    structured_model = model.with_structured_output(
        CatalyticResidueResult,
        include_raw=True,
    )

    response = structured_model.invoke(
        [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ]
    )

    parsing_error = response.get("parsing_error")

    if parsing_error is not None:
        raise RuntimeError(
            f"Structured-output parsing failed: {parsing_error}"
        )

    parsed = response.get("parsed")

    if parsed is None:
        raise RuntimeError(
            "The model did not return a parsed result."
        )

    if not isinstance(parsed, CatalyticResidueResult):
        parsed = CatalyticResidueResult.model_validate(parsed)

    raw_message = response.get("raw")
    usage = getattr(raw_message, "usage_metadata", None)

    return parsed, usage


# ---------------------------------------------------------------------------
# Result processing
# ---------------------------------------------------------------------------

def deduplicate_residues(
    result: CatalyticResidueResult,
) -> CatalyticResidueResult:
    """
    Remove duplicate residue assignments while retaining the result with
    the strongest confidence.
    """
    confidence_rank = {
        "low": 0,
        "medium": 1,
        "high": 2,
    }

    best: dict[
        tuple[str | None, str | None, int, str],
        CatalyticResidue,
    ] = {}

    for residue in result.residues:
        normalized_protein = (
            residue.protein.strip().lower()
            if residue.protein
            else None
        )

        normalized_chain = (
            residue.chain.strip().upper()
            if residue.chain
            else None
        )

        key = (
            normalized_protein,
            normalized_chain,
            residue.residue_number,
            residue.residue_name.strip().upper(),
        )

        previous = best.get(key)

        if previous is None:
            best[key] = residue
            continue

        current_rank = confidence_rank[residue.confidence]
        previous_rank = confidence_rank[previous.confidence]

        if current_rank > previous_rank:
            best[key] = residue

    residues = sorted(
        best.values(),
        key=lambda residue: (
            residue.protein or "",
            residue.chain or "",
            residue.residue_number,
        ),
    )

    return CatalyticResidueResult(
        residues=residues,
        notes=result.notes,
    )


def residue_number_list(
    result: CatalyticResidueResult,
    minimum_confidence: Literal[
        "low",
        "medium",
        "high",
    ] = "medium",
) -> list[int]:
    ranks = {
        "low": 0,
        "medium": 1,
        "high": 2,
    }

    minimum_rank = ranks[minimum_confidence]

    return sorted(
        {
            residue.residue_number
            for residue in result.residues
            if residue.explicitly_catalytic
            and ranks[residue.confidence] >= minimum_rank
        }
    )


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def llm_process_pdf(
    pdf_path: str | Path,
    target_protein: str | None = None,
) -> tuple[CatalyticResidueResult, dict[str, Any] | None]:
    pages = extract_pdf_pages(pdf_path)

    if not pages:
        return (
            CatalyticResidueResult(
                residues=[],
                notes=["No extractable PDF text was found."],
            ),
            None,
        )

    passages = find_relevant_passages(pages)
    validate_no_overlaps(pages, passages)

    if not passages:
        return (
            CatalyticResidueResult(
                residues=[],
                notes=[
                    "No passages related to catalysis or active sites "
                    "were found."
                ],
            ),
            None,
        )

    full_chars = sum(
        len(page.page_content)
        for page in pages
    )
    selected_chars = sum(
        len(passage.page_content)
        for passage in passages
    )

    print(f"PDF pages: {len(pages)}")
    print(f"Full PDF characters: {full_chars:,}")
    print(f"Selected passages: {len(passages)}")
    print(f"Selected characters: {selected_chars:,}")
    print(
        "Selected/full ratio: "
        f"{selected_chars / full_chars:.1%}"
    )

    # Hard safety limit before spending money.
    max_selected_chars = int(
        os.getenv("MAX_SELECTED_CHARS", "120000")
    )

    if selected_chars > max_selected_chars:
        raise RuntimeError(
            f"Refusing to send {selected_chars:,} selected characters. "
            f"The configured maximum is {max_selected_chars:,}. "
            "Inspect passage selection before continuing."
        )

    paper_text = format_passages(passages)
    model = create_model()

    result, usage = extract_catalytic_residues_with_llm(
        model=model,
        paper_text=paper_text,
        target_protein=target_protein,
    )

    return deduplicate_residues(result), usage















def get_catalytic_residues_from_all(pdb_id: str, doi: str=None, downloaded_paper: bool=False) -> list[str]:
    residual_locations = []
    if pdb_id is not None:
        catalytic_locations, amino_acid_locations = get_m_csa_active_sites(pdb_id)
        residual_locations.extend(amino_acid_locations)
        residual_locations = list(set(residual_locations))
        print(f"Residual locations after processing PDB ID {pdb_id}: {amino_acid_locations}")
        print(f"Catalytic locations after processing PDB ID {pdb_id}: {catalytic_locations}")

    if doi is not None:
        catalytic_locations, amino_acid_locations = get_mcsa_catalytic_residues_from_doi(doi)
        residual_locations.extend(amino_acid_locations)
        residual_locations = list(set(residual_locations))
        print(f"Residual locations after processing DOI {doi}: {amino_acid_locations}")
        print(f"Catalytic locations after processing DOI {doi}: {catalytic_locations}")
    
    if downloaded_paper:
        dir="data/uploaded_files"
        for file_name in os.listdir(dir):
            if file_name.endswith(".pdf"):
                file_path = os.path.join(dir, file_name)
                result, usage = llm_process_pdf(file_path)
                print(f"Result from LLM processing PDF: {result}")
                llm_residues = [
                f"{residue.residue_name.title()}{residue.residue_number}"
                for residue in result.residues
                if residue.explicitly_catalytic
                and residue.confidence in {"medium", "high"}
            ]

                residual_locations.extend(llm_residues)

    return list(set(get_residual_pos(residual_locations)))
#print(get_catalytic_residues_from_all(pdb_id="1HMW", doi="10.1021/bi0024254", downloaded_paper=True))

def get_residual_pos(residuals: list[str]) -> list[str]:
    return [residue[3:6] for residue in residuals]
    

#print(get_catalytic_residues_from_all(pdb_id="1HMW", doi="10.1021/bi0024254", downloaded_paper=True))

        
