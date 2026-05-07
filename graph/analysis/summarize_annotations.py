from graph.state import PEATState
from graph.chains import annotation_chain


def _build_annotation_texts(up_features: dict, entry: dict) -> list[str]:
    """Build a flat list of annotation strings from UniProt comments and PDB metadata."""
    all_texts = []

    for comment in up_features.get("comments", []):
        if comment.get("texts"):
            for text in comment["texts"]:
                all_texts.append(text.get("value", ""))
        elif comment.get("commentType") == "CATALYTIC ACTIVITY":
            reaction = comment.get("reaction", {}).get("name", "")
            ec_num   = comment.get("reaction", {}).get("ecNumber", "")
            if reaction:
                all_texts.append(f"Catalytic Activity: {reaction} (EC {ec_num})")
        elif comment.get("commentType") == "SUBCELLULAR LOCATION":
            for loc in comment.get("subcellularLocations", []):
                val = loc.get("location", {}).get("value", "")
                if val:
                    all_texts.append(f"Subcellular Location: {val}")
        elif comment.get("commentType") == "INTERACTION":
            for interaction in comment.get("interactions", []):
                g1    = interaction.get("interactantOne", {}).get("geneName", "")
                g2    = interaction.get("interactantTwo", {}).get("geneName", "")
                count = interaction.get("numberOfExperiments", 0)
                all_texts.append(f"Interaction: {g1} ↔ {g2} ({count} experiments)")

    # Fall back to PDB metadata + UniProt domain features for sparse TrEMBL entries
    if not all_texts:
        struct_title    = entry.get("struct", {}).get("title", "")
        struct_keywords = entry.get("struct_keywords", {}).get("text", "")
        citation        = entry.get("rcsb_primary_citation", {})
        paper_title     = citation.get("title", "")
        if struct_title:
            all_texts.append(f"PDB structure title: {struct_title}")
        if struct_keywords:
            all_texts.append(f"PDB keywords: {struct_keywords}")
        if paper_title:
            all_texts.append(f"Associated paper: {paper_title}")
        for feat in up_features.get("features", []):
            feat_type = feat.get("type", "")
            feat_desc = feat.get("description", "")
            loc       = feat.get("location", {})
            start     = loc.get("start", {}).get("value", "")
            end       = loc.get("end", {}).get("value", "")
            if feat_type and (feat_desc or feat_type != "Chain"):
                all_texts.append(f"{feat_type}: {feat_desc} (residues {start}–{end})")

    return all_texts


def summarize_annotations(state: PEATState) -> dict:
    up_features = state.get("uniprot_features") or {}
    pdb_entry   = state.get("pdb_entry") or {}

    all_texts = _build_annotation_texts(up_features, pdb_entry)
    if not all_texts:
        return {"gpt_summary": {}}

    try:
        gpt_summary = annotation_chain.invoke({"annotations": "\n".join(all_texts)})
        if not isinstance(gpt_summary, dict):
            gpt_summary = {"Structure": [], "Function": [str(gpt_summary)], "Sequence": []}
    except Exception:
        gpt_summary = {}

    return {"gpt_summary": gpt_summary}
