from langchain_core.tools import tool

from stabilization_tools import run_step_1, run_step2_conservation_pipeline, run_step3_mpnn_pipeline, run_step4_af3_pipeline, run_step5_stability_analysis, run_full_pipeline

@tool
def run_stabilization_pipeline(sequence_file: str, target_name: str, output_dir: str, email: str) -> dict:
    """Run the full stabilization pipeline."""
    return run_full_pipeline(target_name, sequence_file, output_dir, email)