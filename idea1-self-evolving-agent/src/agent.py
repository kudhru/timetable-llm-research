"""
LLM calling utilities and prompt building for the exam scheduling agent.
"""
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional


# ── LLM calling ────────────────────────────────────────────────────────────────

def call_codex(prompt: str, model: str = "gpt-5.5") -> str:
    """
    Call codex exec with the given prompt using a temp file for output.
    Uses --dangerously-bypass-approvals-and-sandbox and --skip-git-repo-check.
    """
    with tempfile.NamedTemporaryFile(mode='r', suffix='.txt', delete=False) as f:
        out_path = f.name
    try:
        result = subprocess.run(
            [
                "codex", "exec",
                "--dangerously-bypass-approvals-and-sandbox",
                "--skip-git-repo-check",
                "-m", model,
                "-o", out_path,
                prompt,
            ],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            raise RuntimeError(f"Codex call failed: {result.stderr}")
        with open(out_path) as f:
            return f.read().strip()
    finally:
        if os.path.exists(out_path):
            os.unlink(out_path)


def call_llm(prompt: str, model: str = "claude-haiku", timeout: int = 180) -> str:
    """
    Call an LLM with the given prompt and return the response string.

    Supported models:
      - "claude-haiku": Claude Haiku via `claude -p`
      - "claude-sonnet": Claude Sonnet via `claude -p`
      - "gpt-5.5": GPT-5.5 via `codex exec` (with bypass flags)
      - "gpt-4o-mini": GPT-4o-mini via `codex exec`
      - "gpt-4o": GPT-4o via `codex exec`

    Raises RuntimeError if the subprocess fails.
    """
    if model in ("claude-haiku", "claude-haiku-4-5"):
        result = subprocess.run(
            ["claude", "--model", "claude-haiku-4-5-20251001", "-p", prompt],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"LLM call failed (model={model}, returncode={result.returncode}):\n"
                f"STDERR: {result.stderr[:500]}"
            )
        return result.stdout.strip()
    elif model in ("claude-sonnet", "claude-sonnet-4-6"):
        result = subprocess.run(
            ["claude", "--model", "claude-sonnet-4-6", "-p", prompt],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"LLM call failed (model={model}, returncode={result.returncode}):\n"
                f"STDERR: {result.stderr[:500]}"
            )
        return result.stdout.strip()
    elif model == "gpt-5.5":
        # Route to call_codex with the new flags
        return call_codex(prompt, "gpt-5.5")
    elif model.startswith("gpt") or model == "codex-default":
        # codex exec reads prompt from stdin; does not support gpt-4o-mini with ChatGPT accounts
        # Use default model when model="codex-default", otherwise pass -m flag
        cmd = ["codex", "exec"]
        if model != "codex-default" and not model.startswith("gpt-4o-mini"):
            cmd += ["-m", model]
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"LLM call failed (model={model}, returncode={result.returncode}):\n"
                f"STDERR: {result.stderr[:500]}"
            )
        return result.stdout.strip()
    else:
        raise ValueError(f"Unknown model: {model!r}")


def logged_call(
    prompt: str,
    model: str,
    log_file: str,
    extra_meta: Optional[dict] = None,
    timeout: int = 180,
) -> str:
    """
    Call the LLM and log the full call (prompt, response, latency) to a JSONL file.
    Returns the response string, or raises on error.
    """
    start = time.time()
    error = None
    response = ""
    try:
        response = call_llm(prompt, model=model, timeout=timeout)
    except Exception as e:
        error = str(e)
        raise
    finally:
        latency = time.time() - start
        entry = {
            "model": model,
            "timestamp": time.time(),
            "latency_s": round(latency, 2),
            "prompt_chars": len(prompt),
            "response_chars": len(response),
            "prompt": prompt,
            "response": response,
        }
        if extra_meta:
            entry.update(extra_meta)
        if error:
            entry["error"] = error
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
    return response


# ── JSON parsing ────────────────────────────────────────────────────────────────

def parse_solution(response: str) -> dict:
    """
    Extract a JSON solution dict from an LLM response.

    The LLM is expected to output something like:
      {"assignments": [{"exam_id": "E1", "period_id": "1", "room_ids": ["R1"]}]}

    Returns {} if parsing fails.
    """
    if not response:
        return {}

    # Try direct parse first
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass

    # Try to find JSON block (handles markdown code fences and surrounding text)
    # Look for outermost braces
    match = re.search(r'\{.*\}', response, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Try to find JSON in a code block
    code_block = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
    if code_block:
        try:
            return json.loads(code_block.group(1))
        except json.JSONDecodeError:
            pass

    return {}


def solution_from_parsed(parsed: dict) -> dict:
    """
    Convert parsed JSON dict to Solution format: {exam_id: (period_id, [room_ids])}.
    Returns {} if the parsed dict is missing the 'assignments' key.
    """
    if not parsed or "assignments" not in parsed:
        return {}
    solution = {}
    for a in parsed["assignments"]:
        exam_id = str(a.get("exam_id", ""))
        period_id = str(a.get("period_id", ""))
        room_ids = [str(r) for r in a.get("room_ids", [])]
        if exam_id and period_id:
            solution[exam_id] = (period_id, room_ids)
    return solution


# ── Prompt building ────────────────────────────────────────────────────────────

def build_proposal_prompt(instance, template_path: str = None, strategy_context: str = "") -> str:
    """
    Build a proposal prompt for the given instance.

    If template_path is provided, uses that template and fills in placeholders.
    Otherwise builds the prompt programmatically.
    """
    if template_path:
        with open(template_path) as f:
            template = f.read()
    else:
        # Build a default template
        template = _DEFAULT_PROPOSAL_TEMPLATE

    # Build exam list
    exam_lines = []
    for exam in instance.exams:
        avail_p = ", ".join(exam.available_periods[:10])
        if len(exam.available_periods) > 10:
            avail_p += f", ... ({len(exam.available_periods)} total)"
        avail_r = ", ".join(exam.available_rooms[:10])
        if len(exam.available_rooms) > 10:
            avail_r += f", ... ({len(exam.available_rooms)} total)"
        exam_lines.append(
            f'  {{"exam_id": "{exam.id}", "students": {exam.student_count}, '
            f'"available_periods": [{avail_p}], '
            f'"available_rooms": [{avail_r}]}}'
        )
    exam_list_str = "[\n" + ",\n".join(exam_lines) + "\n]"

    # Period info
    period_lines = []
    for p in instance.periods:
        period_lines.append(
            f'  {{"id": "{p.id}", "day": "{p.day}", "time": "{p.time}", "penalty": {p.penalty}}}'
        )
    period_list_str = "[\n" + ",\n".join(period_lines) + "\n]"

    # Room info
    room_lines = []
    for r in instance.rooms:
        room_lines.append(
            f'  {{"id": "{r.id}", "capacity": {r.size}, "alt_capacity": {r.alt}}}'
        )
    room_list_str = "[\n" + ",\n".join(room_lines) + "\n]"

    # Hard constraints in NL
    hard_constraints_nl = []
    for c in instance.constraints:
        if c.hard:
            hard_constraints_nl.append(
                f"  - {c.type.upper()} (id={c.id}): exams {c.exam_ids}"
            )
    hard_constraints_str = "\n".join(hard_constraints_nl) if hard_constraints_nl else "  (none)"

    # Soft constraints
    soft_constraints_nl = []
    for c in instance.constraints:
        if not c.hard:
            soft_constraints_nl.append(
                f"  - {c.type.upper()} (id={c.id}, weight={c.weight}): exams {c.exam_ids}"
            )
    soft_constraints_str = "\n".join(soft_constraints_nl) if soft_constraints_nl else "  (none)"

    # Period dates summary
    period_dates = list({p.day for p in instance.periods})

    # Capacities summary
    room_capacities = sorted([r.size for r in instance.rooms])

    # Strategy context block
    strategy_block = ""
    if strategy_context:
        strategy_block = (
            "\n## Accumulated Strategy Learnings\n"
            "Based on previous attempts, here are key learnings to apply:\n"
            f"{strategy_context}\n"
        )

    prompt = template.format(
        instance_name=instance.name,
        num_exams=len(instance.exams),
        num_periods=len(instance.periods),
        period_dates=", ".join(period_dates[:6]),
        num_rooms=len(instance.rooms),
        room_capacities=f"{min(room_capacities)}–{max(room_capacities)}",
        num_students=len(instance.students),
        hard_constraints_nl=hard_constraints_str,
        soft_constraints_nl=soft_constraints_str,
        exam_list=exam_list_str,
        period_list=period_list_str,
        room_list=room_list_str,
        strategy_context=strategy_block,
    )
    return prompt


_DEFAULT_PROPOSAL_TEMPLATE = """\
You are a university exam scheduling assistant. Your task is to assign each exam to a period and one or more rooms.

## Instance: {instance_name}
- Exams: {num_exams} exams to schedule
- Periods: {num_periods} available periods (days: {period_dates})
- Rooms: {num_rooms} rooms (capacities: {room_capacities})
- Students: {num_students} enrolled students

## Periods
{period_list}

## Rooms
{room_list}

## Hard Constraints (MUST satisfy all of these)
1. STUDENT CONFLICT: No two exams that share at least one student can be in the same period.
2. ROOM CAPACITY: The total capacity of assigned rooms must be >= the number of students in the exam.
3. PERIOD AVAILABILITY: Each exam must be assigned to one of its listed available periods.
4. ROOM AVAILABILITY: Each exam must be assigned to rooms from its listed available rooms only.
5. Distribution constraints:
{hard_constraints_nl}

## Soft Preferences (minimize penalty score — lower is better)
- Prefer periods with penalty=0 over periods with penalty>0
- Soft distribution constraints:
{soft_constraints_nl}
{strategy_context}
## Exams to Schedule
For each exam, you are given: exam_id, student count, available periods (use one of these), available rooms (use one or more of these).
{exam_list}

## Output Format
Return ONLY a JSON object in this exact format (no other text):
{{
  "assignments": [
    {{"exam_id": "E1", "period_id": "1", "room_ids": ["R1"]}},
    {{"exam_id": "E2", "period_id": "2", "room_ids": ["R2", "R3"]}},
    ...
  ]
}}

IMPORTANT:
- Every exam must have exactly one assignment.
- Use only available periods and rooms for each exam.
- Avoid placing exams that share students in the same period.
- Assign enough rooms so total capacity >= student count.
- Output ONLY the JSON, nothing else.
"""


def build_formal_feedback_prompt(
    instance, solution: dict, violations: list, penalty: float, best_penalty: float,
    template_path: str = None,
) -> str:
    """Build a formal reflection prompt with exact violation details."""
    if template_path:
        with open(template_path) as f:
            template = f.read()
    else:
        template = _DEFAULT_FORMAL_FEEDBACK_TEMPLATE

    # Format violations
    if violations:
        from collections import Counter
        vtype_counts = Counter(v["type"] for v in violations)
        most_common_type = vtype_counts.most_common(1)[0][0]
        most_common_count = vtype_counts.most_common(1)[0][1]

        # Find hot exams (most frequently involved in violations)
        from collections import Counter as C2
        exam_mention_counts = C2()
        for v in violations:
            for eid in v.get("exam_ids", []):
                exam_mention_counts[eid] += 1
        hot_exams = [eid for eid, _ in exam_mention_counts.most_common(5)]

        # Find most congested period
        period_mention_counts = C2()
        for v in violations:
            if "period_id" in v:
                period_mention_counts[v["period_id"]] += 1
        if period_mention_counts:
            busiest_period = period_mention_counts.most_common(1)[0][0]
            period_obj = instance.period_map.get(busiest_period)
            busiest_period_str = (
                f"{busiest_period} ({period_obj.day} {period_obj.time})"
                if period_obj else busiest_period
            )
        else:
            busiest_period_str = "N/A"

        violation_list = "\n".join(
            f"  {i+1}. [{v['type']}] {v['description']}"
            for i, v in enumerate(violations[:30])
        )
        if len(violations) > 30:
            violation_list += f"\n  ... and {len(violations) - 30} more violations"
    else:
        most_common_type = "none"
        most_common_count = 0
        hot_exams = []
        busiest_period_str = "N/A"
        violation_list = "  (no violations)"

    # Format the current solution assignments
    assignment_lines = []
    for exam_id, (period_id, room_ids) in sorted(solution.items()):
        assignment_lines.append(
            f'    {{"exam_id": "{exam_id}", "period_id": "{period_id}", "room_ids": {room_ids}}}'
        )
    current_solution_str = "[\n" + ",\n".join(assignment_lines) + "\n  ]"

    prompt = template.format(
        num_violations=len(violations),
        violation_list=violation_list,
        penalty=f"{penalty:.1f}" if penalty != float('inf') else "N/A (infeasible)",
        best_penalty=f"{best_penalty:.1f}" if best_penalty != float('inf') else "N/A",
        most_common_violation_type=most_common_type,
        count=most_common_count,
        hot_exams=", ".join(hot_exams) if hot_exams else "N/A",
        busiest_period=busiest_period_str,
        current_solution=current_solution_str,
        instance_name=instance.name,
    )
    return prompt


_DEFAULT_FORMAL_FEEDBACK_TEMPLATE = """\
You proposed a schedule for instance {instance_name}. The formal constraint verifier found these violations:

## Hard Constraint Violations ({num_violations} total)
{violation_list}

## Metrics
- Current Penalty Score: {penalty}
- Previous Best Penalty: {best_penalty}
- Most violated constraint type: {most_common_violation_type} ({count} occurrences)
- Exams most involved in violations: {hot_exams}
- Most congested period: {busiest_period}

## Your Current Assignment (for reference)
{current_solution}

## Task
1. Diagnose WHY these violations occurred in your assignment.
2. State a concise revised strategy to fix them.
3. Propose a fully corrected assignment for ALL exams.

IMPORTANT: Your new assignment must satisfy ALL hard constraints:
- No two exams sharing students in the same period
- Room capacity >= student count
- Only use each exam's available periods and rooms

Output ONLY a JSON object in this exact format:
{{
  "diagnosis": "Brief explanation of what went wrong",
  "strategy_update": "Specific rules you will follow in the new assignment",
  "assignments": [
    {{"exam_id": "E1", "period_id": "1", "room_ids": ["R1"]}},
    ...
  ]
}}
"""


def build_nl_feedback_prompt(
    violations: list, penalty: float,
    template_path: str = None,
) -> str:
    """Build a vague natural language reflection prompt."""
    if template_path:
        with open(template_path) as f:
            template = f.read()
    else:
        template = _DEFAULT_NL_FEEDBACK_TEMPLATE

    has_violations = len(violations) > 0
    prompt = template.format(
        has_violations="Yes" if has_violations else "No",
        penalty=f"{penalty:.1f}" if penalty != float('inf') else "N/A",
        num_violations=len(violations),
    )
    return prompt


_DEFAULT_NL_FEEDBACK_TEMPLATE = """\
You proposed a schedule, but it didn't work well.

Some problems were observed:
- Has hard constraint violations: {has_violations} ({num_violations} issues found)
- Penalty score: {penalty} (lower is better)
- Some students may have overlapping exam conflicts
- Some rooms may not be suitable for the assigned exams
- The schedule could be better organized to spread exams across available periods

Please reflect on what might have gone wrong and propose a better, more balanced schedule.

Output ONLY a JSON object in this exact format:
{{
  "diagnosis": "What you think went wrong",
  "strategy_update": "How you will improve in the next attempt",
  "assignments": [
    {{"exam_id": "E1", "period_id": "1", "room_ids": ["R1"]}},
    ...
  ]
}}
"""


# ── Quick test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Testing call_llm with a simple hello world ...")
    try:
        response = call_llm("Say exactly: hello world", model="claude-haiku")
        print(f"Response: {response[:200]}")
        print("PASS: claude-haiku call succeeded")
    except Exception as e:
        print(f"FAIL: {e}")
