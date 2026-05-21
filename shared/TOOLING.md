# Tooling Guide: LLM Calls Without API Credits

Both research directions make LLM calls programmatically. This system has two options available.

---

## Option A: Claude Haiku via `claude -p`

`claude -p` runs Claude Code in non-interactive (print) mode — sends a prompt, returns the response to stdout, exits. No Anthropic API credits required; uses your Claude Code subscription.

### Basic usage
```bash
claude -p "Your prompt here"
```

### Specify model (use Haiku for high-throughput experiments)
```bash
claude --model claude-haiku-4-5-20251001 -p "Your prompt here"
```

### Pipe a prompt from a file
```bash
claude -p < prompt.txt
```

### Capture output to a file
```bash
claude --model claude-haiku-4-5-20251001 -p "Your prompt here" > response.txt
```

### In a Python subprocess (for agentic loops)
```python
import subprocess

def call_claude_haiku(prompt: str) -> str:
    result = subprocess.run(
        ["claude", "--model", "claude-haiku-4-5-20251001", "-p", prompt],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude call failed: {result.stderr}")
    return result.stdout.strip()
```

### Notes
- Haiku is the fastest and cheapest model — ideal for running hundreds of iterations
- For higher reasoning quality at key steps (e.g., final reflection), swap to `claude-sonnet-4-6`
- Rate limiting: if you hit limits, add `time.sleep(2)` between calls
- Max prompt size: keep under ~150k tokens; timetabling XML instances are typically 10–50k tokens

---

## Option B: OpenAI GPT-5.x via `codex exec`

Codex CLI (v0.130.0) is installed on this system. Default model is **`gpt-5.5`** with `reasoning_effort = "high"` (configured in `~/.codex/config.toml`). Use `-m` to override the model.

### Basic usage (uses gpt-5.5 by default)
```bash
codex exec "Your prompt here"
```

### With explicit model override
```bash
codex exec -m "gpt-5.5" "Your prompt here"
codex exec -m "gpt-5" "Your prompt here"
```

### Write last agent message to a file (preferred over stdout redirect)
```bash
codex exec -o response.txt "Your prompt here"
```

### Skip git repo check (required when running outside a git repo)
```bash
codex exec --skip-git-repo-check "Your prompt here"
```

### Skip approval prompts (required for non-interactive use in scripts)
```bash
codex exec --dangerously-bypass-approvals-and-sandbox "Your prompt here"
```

### Full non-interactive invocation for scripted use
```bash
codex exec \
  --dangerously-bypass-approvals-and-sandbox \
  --skip-git-repo-check \
  -m "gpt-5.5" \
  -o response.txt \
  "Your prompt here"
```

### Pipe a prompt from a file
```bash
cat prompt.txt | codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check
```

### In a Python subprocess
```python
import subprocess, tempfile, os

def call_codex(prompt: str, model: str = "gpt-5.5") -> str:
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
        os.unlink(out_path)
```

### Notes
- Default model `gpt-5.5` has `reasoning_effort = "high"` — very capable but slower
- For high-throughput loops, consider `-m "gpt-5"` (faster, lower reasoning overhead)
- `--dangerously-bypass-approvals-and-sandbox` is required for non-interactive scripted calls
- `--skip-git-repo-check` is required when the working directory is not a git repo
- Use `-o <file>` rather than stdout redirect — codex may print UI chrome to stdout

---

## Benchmarking Both Models

Run every experiment with both Claude Haiku and GPT-5.5. Report results side-by-side. This gives the paper a model-agnostic findings section — stronger for reviewers.

```python
MODELS = {
    "claude-haiku": lambda p: call_claude_haiku(p),
    "gpt-5.5": lambda p: call_codex(p, "gpt-5.5"),
}

for model_name, call_fn in MODELS.items():
    response = call_fn(prompt)
    # log model_name, response, metrics
```

---

## Logging Convention

Every LLM call should be logged for reproducibility:

```python
import json, time

def logged_call(model_name: str, call_fn, prompt: str, log_file: str) -> str:
    start = time.time()
    response = call_fn(prompt)
    entry = {
        "model": model_name,
        "timestamp": time.time(),
        "latency_s": time.time() - start,
        "prompt_chars": len(prompt),
        "response_chars": len(response),
        "prompt": prompt,
        "response": response,
    }
    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return response
```

Store logs as JSONL files: one entry per LLM call. This allows full replay and analysis.
