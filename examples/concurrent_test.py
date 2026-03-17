#!/usr/bin/env python3
"""Concurrent agent test: 10 agents hitting vLLM + persistent swerex server."""

import concurrent.futures
import time
from pathlib import Path

from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.extra.swerex_remote import SwerexRemoteEnvironment
from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel

SWEREX_HOST = "http://127.0.0.1"
SWEREX_PORT = 8000
SWEREX_AUTH_TOKEN = "test123"

VLLM_BASE = "http://143.248.136.10:8066/v1"
MODEL_NAME = "openai/Qwen/Qwen2.5-32B-Instruct"

TASKS = [
    # Multi-step file manipulation tasks
    "Create a directory /tmp/task0_work, then create 5 Python files in it (a.py through e.py) each containing a function that returns its filename. Then write a main.py that imports all 5 and prints their return values. Run main.py and verify the output.",
    "Create a Python script /tmp/task1_fizzbuzz.py that implements FizzBuzz for 1-100. Run it, save output to /tmp/task1_output.txt, then count how many lines contain 'Fizz', 'Buzz', and 'FizzBuzz' separately. Report the counts.",
    "Find all .conf files under /etc, count them, then find the 3 largest ones by file size. Show the first 10 lines of each of those 3 files.",
    "Create a CSV file /tmp/task3_data.csv with 20 rows of random-looking data (name, age, score columns). Then write a Python script that reads it, calculates the average score, finds the oldest person, and prints a summary. Run the script.",
    "Write a Python script /tmp/task4_primes.py that finds all prime numbers up to 500 using the Sieve of Eratosthenes. Run it, save output to a file, then count total primes found and verify the last prime is 499.",
    # System investigation tasks
    "Investigate the Python installation: find where python3 is installed, check its version, list all installed pip packages, find the top 5 largest packages by installed size, and report findings.",
    "Explore the /etc directory structure: count total files, find all files modified in the last 7 days, identify the 5 largest files, and check permissions on /etc/shadow and /etc/passwd. Summarize findings.",
    "Create a shell script /tmp/task7_sysinfo.sh that collects: hostname, kernel version, CPU count, total memory, disk usage, and current user. Make it executable, run it, save output to /tmp/task7_report.txt, then display the report.",
    "Write a Python program /tmp/task8_wordcount.py that reads /etc/services, counts unique words, finds the 10 most common words, and calculates total lines/words/characters (like wc). Run and verify by comparing with actual wc output.",
    "Create a directory /tmp/task9_project with a proper Python package structure (setup.py, src/ with __init__.py and two modules). One module should have a function to calculate factorials, another to check palindromes. Write tests and run them with Python's unittest.",
]

SYSTEM_TEMPLATE = """\
You are a helpful assistant that can interact with a computer shell.

Your response must contain exactly ONE bash code block with ONE command.
Include a THOUGHT section before your command.

<format_example>
THOUGHT: I want to check the files.

```mswea_bash_command
ls -la
```
</format_example>

When you are done, submit with:
```mswea_bash_command
echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && echo "done"
```
"""

INSTANCE_TEMPLATE = "Please complete this task: {{task}}"

OBSERVATION_TEMPLATE = """\
{% if output.exception_info -%}
<exception>{{output.exception_info}}</exception>
{% endif -%}
<returncode>{{output.returncode}}</returncode>
<output>
{{ output.output[:3000] -}}
</output>"""

FORMAT_ERROR_TEMPLATE = """\
Format error: {{error}}
Please provide EXACTLY ONE action in triple backticks (found {{actions|length}}).

```mswea_bash_command
<your command>
```
"""


def run_agent(task_id: int, task: str, output_dir: Path) -> dict:
    """Run a single agent against the shared swerex server with cwd-based isolation."""
    model = LitellmTextbasedModel(
        model_name=MODEL_NAME,
        model_kwargs={
            "api_base": VLLM_BASE,
            "drop_params": True,
            "temperature": 0.0,
        },
        cost_tracking="ignore_errors",
        observation_template=OBSERVATION_TEMPLATE,
        format_error_template=FORMAT_ERROR_TEMPLATE,
    )

    env = SwerexRemoteEnvironment(
        host=SWEREX_HOST,
        port=SWEREX_PORT,
        auth_token=SWEREX_AUTH_TOKEN,
        cwd=f"/workspace/agent_{task_id}",
        timeout=30,
    )

    agent = DefaultAgent(
        model,
        env,
        system_template=SYSTEM_TEMPLATE,
        instance_template=INSTANCE_TEMPLATE,
        step_limit=15,
        cost_limit=0,
        output_path=output_dir / f"agent_{task_id}.traj.json",
    )

    start = time.perf_counter()
    try:
        result = agent.run(task=task)
        elapsed = time.perf_counter() - start
        status = result.get("exit_status", "unknown")
    except Exception as e:
        elapsed = time.perf_counter() - start
        status = f"error:{type(e).__name__}"
        result = {"exit_status": status, "exception": str(e)}

    print(f"[Agent {task_id:2d}] {status:20s} | {agent.n_calls} steps | {elapsed:.1f}s | {task[:50]}")
    return {"task_id": task_id, "status": status, "steps": agent.n_calls, "elapsed": elapsed}


def main():
    output_dir = Path("/tmp/mswea-tempo/test_results")
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Running {len(TASKS)} concurrent agents against {MODEL_NAME}")
    print(f"vLLM backend: {VLLM_BASE}")
    print(f"swerex server: {SWEREX_HOST}:{SWEREX_PORT}")
    print(f"Output: {output_dir}")
    print("-" * 80)

    start = time.perf_counter()
    results = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(run_agent, i, task, output_dir): i
            for i, task in enumerate(TASKS)
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                tid = futures[future]
                print(f"[Agent {tid:2d}] UNCAUGHT ERROR: {e}")
                results.append({"task_id": tid, "status": "uncaught_error", "steps": 0, "elapsed": 0})

    total = time.perf_counter() - start
    print("-" * 80)
    print(f"All done in {total:.1f}s")

    # Summary
    results.sort(key=lambda r: r["task_id"])
    total_steps = sum(r["steps"] for r in results)
    statuses = {}
    for r in results:
        s = r["status"]
        statuses[s] = statuses.get(s, 0) + 1
    print(f"Total steps: {total_steps}, Status distribution: {statuses}")


if __name__ == "__main__":
    main()
