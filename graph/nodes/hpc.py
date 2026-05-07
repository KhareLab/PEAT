from graph.state import PEATState
from graph.tools.hpc import run_hpc_command, submit_minimization, check_job_status, download_job_results


def hpc_command(state: PEATState) -> dict:
    output = run_hpc_command.invoke({"command": state["hpc_command"]})
    return {"response_text": f"**HPC Output:**\n```bash\n{output}\n```"}


def hpc_minimize(state: PEATState) -> dict:
    pdb_id = state["pdb_id"]
    try:
        result = submit_minimization.invoke({"pdb_id": pdb_id})
        job_id = result["job_id"]
        updated_jobs = dict(state.get("hpc_jobs") or {})
        updated_jobs[job_id] = {
            "pdb_id":     result["pdb_id"],
            "remote_dir": result["remote_dir"],
        }
        response = (
            f"**Energy minimization submitted for {pdb_id}.**\n\n"
            f"- **Job ID:** `{job_id}`\n"
            f"- **Remote dir:** `{result['remote_dir']}`\n"
            f"- **Steps:** 500 × steepest descent, vacuum, OPLS-AA force field\n\n"
            f"Check progress with: `check job {job_id}`\n"
            f"Download results with: `download results {job_id}`"
        )
        return {"response_text": response, "hpc_jobs": updated_jobs}
    except Exception as e:
        return {"response_text": f"Minimization submission failed: {e}"}


def hpc_check(state: PEATState) -> dict:
    job_id = state["job_id"]
    try:
        status = check_job_status.invoke({"job_id": job_id})
        return {"response_text": f"**Job {job_id} status:**\n```\n{status}\n```"}
    except Exception as e:
        return {"response_text": f"Job status check failed: {e}"}


def hpc_download(state: PEATState) -> dict:
    job_id   = state["job_id"]
    hpc_jobs = state.get("hpc_jobs") or {}
    job_info = hpc_jobs.get(job_id)
    if not job_info:
        return {
            "response_text": (
                f"No record of job {job_id} in this session. "
                "Submit a minimization first, or check the job ID."
            )
        }
    try:
        dl        = download_job_results.invoke({"job_id": job_id, "pdb_id": job_info["pdb_id"]})
        file_list = "\n".join(f"- `{f}`" for f in dl["files"])
        return {
            "response_text": (
                f"**Results downloaded for job {job_id}.**\n\n"
                f"Saved to: `{dl['local_dir']}`\n\n"
                f"{file_list if dl['files'] else '_No output files found._'}"
            )
        }
    except Exception as e:
        return {"response_text": f"Download failed: {e}"}
