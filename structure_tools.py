from Bio.PDB import PDBParser, PDBIO, Select, Superimposer
import os
import pymol
from pymol import cmd
import requests




def build_3dmol_html(pdb_id: str, pdb_path: str | None = None) -> str:
    """
    Returns HTML string for embedding a 3Dmol.js viewer.

    If pdb_path is provided, the PDB file is read and embedded inline
    (used for AlphaFold structures not available on RCSB).
    Otherwise the structure is fetched from RCSB by pdb_id.
    """
    if pdb_path:
        with open(pdb_path) as fh:
            pdb_data = fh.read().replace("`", "\\`")  # escape backticks for JS template literal
        return f"""
<script src="https://3Dmol.org/build/3Dmol-min.js"></script>
<div id="viewer3d" style="width:100%; height:500px;"></div>
<script>
  const elt = document.getElementById('viewer3d');
  const viewer = $3Dmol.createViewer(elt, {{ backgroundColor: 'white' }});
  const pdbData = `{pdb_data}`;
  viewer.addModel(pdbData, 'pdb');
  viewer.setStyle({{}}, {{ cartoon: {{ color: 'spectrum' }} }});
  viewer.zoomTo();
  viewer.render();
</script>
"""  # noqa: E501

    return f"""
<script src="https://3Dmol.org/build/3Dmol-min.js"></script>
<div id="viewer3d" style="width:100%; height:500px;"></div>
<script>
  const elt = document.getElementById('viewer3d');
  const viewer = $3Dmol.createViewer(elt, {{ backgroundColor: 'white' }});
  $3Dmol.download('pdb:{pdb_id.upper()}', viewer, {{}}, function() {{
    viewer.setStyle({{}}, {{ cartoon: {{ color: 'spectrum' }} }});
    viewer.zoomTo();
    viewer.render();
  }});
</script>
"""  # noqa: E501



def download_pdb(pdb_id: str, output_dir: str) -> str:
    pdb_id = pdb_id.upper()

    os.makedirs(output_dir, exist_ok=True)

    url = f"https://files.rcsb.org/download/{pdb_id}.pdb"

    r = requests.get(url, timeout=30)
    r.raise_for_status()

    pdb_path = os.path.join(output_dir, f"{pdb_id}.pdb")

    with open(pdb_path, "wb") as f:
        f.write(r.content)

    return pdb_path




class CleanPDBSelect(Select):
    def __init__(self, keep_chains=None, remove_ligands=True):
        self.keep_chains = set(keep_chains) if keep_chains else None
        self.remove_ligands = remove_ligands

    def accept_chain(self, chain):
        if self.keep_chains is None:
            return True
        return chain.id in self.keep_chains

    def accept_residue(self, residue):
        hetflag = residue.id[0]

        if hetflag == "W":
            return False

        if self.remove_ligands and hetflag != " ":
            return False

        return True

    def accept_atom(self, atom):
        if atom.is_disordered():
            return atom.get_altloc() in ("A", " ")

        return True

def clean_pdb(pdb_path: str, output_path: str,keep_chains: list[str] | None = None,remove_ligands: bool = True,):
    parser = PDBParser(QUIET=True)
    structure = parser.get_structure("structure", pdb_path)
    print(sum(1 for r in structure.get_residues() if r.id[0] == "W"))
    io = PDBIO()
    io.set_structure(structure)
    io.save(
        output_path,
        CleanPDBSelect(
            keep_chains=keep_chains,
            remove_ligands=remove_ligands,
        ),
    )

    return output_path


def check_missing_loops(pdb_path, chain_id="A"):
    parser = PDBParser(QUIET=True)
    structure = parser.get_structure("structure", pdb_path)

    chain = structure[0][chain_id]

    residues = [
        r.id[1]
        for r in chain
        if r.id[0] == " "  # protein residues only
    ]

    missing_gaps = []

    for prev, curr in zip(residues, residues[1:]):
        if curr != prev + 1:
            missing_gaps.append((prev, curr))

    if missing_gaps:
        print("Possible missing loops/gaps:")
        for prev, curr in missing_gaps:
            print(f"Gap between residues {prev} and {curr}")
    else:
        print("No residue-number gaps detected.")

    return missing_gaps


def pymol_align(
    fixed_pdb,
    moving_pdb,
    output_pdb="aligned.pdb",
    session_file="alignment.pse"
):
    pymol.finish_launching(["pymol", "-cq"])
    cmd.reinitialize()

    cmd.load(fixed_pdb, "template")
    cmd.load(moving_pdb, "target")

    cmd.color("cyan", "template")
    cmd.color("orange", "target")

    result = cmd.align("target", "template", object="aln")

    pairs = get_aligned_residues("aln")

    cmd.save(output_pdb, "target")
    cmd.save(session_file)

    return {
        "rmsd": result[0],
        "aligned_atoms": result[1],
        "raw_result": result,
        "aligned_residues": pairs,
    }

def get_aligned_residues(alignment_object="aln"):
    aligned_pairs = []
    seen = set()

    raw = cmd.get_raw_alignment(alignment_object)

    for pair in raw:
        model1, index1 = pair[0]  # target
        model2, index2 = pair[1]  # template

        a1 = cmd.get_model(f"{model1} and index {index1}").atom[0]
        a2 = cmd.get_model(f"{model2} and index {index2}").atom[0]

        # keep only CA-CA residue mappings
        if a1.name != "CA" or a2.name != "CA":
            continue

        key = (a2.chain, a2.resi, a1.chain, a1.resi)
        if key in seen:
            continue
        seen.add(key)

        aligned_pairs.append({
            "template_chain": a2.chain,
            "template_resi": int(a2.resi),
            "template_resn": a2.resn,
            "target_chain": a1.chain,
            "target_resi": int(a1.resi),
            "target_resn": a1.resn,
        })

    return aligned_pairs


def get_residual_mappings( catalytic_residues: list[int], align_result: dict, template_chain: str = "A") -> list[dict | None]:
    mapping = {
        (p["template_chain"], p["template_resi"]): p
        for p in align_result["aligned_residues"]
    }
    mappings = []

    for resi in catalytic_residues:
        match = mapping.get((template_chain, resi))

        if match is None:
            print(f"Template {template_chain}{resi} -> no aligned target residue found")
        else:
            print(
                f"Template {template_chain}{resi} "
                f"({match['template_resn']}) -> "
                f"Target {match['target_chain']}{match['target_resi']} "
                f"({match['target_resn']})"
            )

        mappings.append(match)

    return mappings





