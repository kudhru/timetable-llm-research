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

## Option B: OpenAI Model via `codex exec`

Codex CLI (v0.130.0) is installed on this system. Use it to make calls to OpenAI models.

### Basic usage
```bash
codex exec "Your prompt here"
```

### With model specification
```bash
codex exec --model gpt-4o-mini "Your prompt here"
```

### Quiet mode (response only, no UI chrome)
```bash
codex -q "Your prompt here"
```

### Capture output
```bash
codex exec "Your prompt here" > response.txt
```

### In a Python subprocess
```python
import subprocess

def call_codex(prompt: str, model: str = "gpt-4o-mini") -> str:
    result = subprocess.run(
        ["codex", "exec", "--model", model, prompt],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(f"Codex call failed: {result.stderr}")
    return result.stdout.strip()
```

### Notes
- Verify exact flag syntax for your codex version: `codex --help`
- `gpt-4o-mini` is the cost-efficient choice for high-throughput runs
- `gpt-4o` for higher reasoning quality at evaluation steps

---

## Benchmarking Both Models

Run every experiment with both Claude Haiku and one OpenAI model. Report results side-by-side. This gives the paper a model-agnostic findings section — stronger for reviewers.

```python
MODELS = {
    "claude-haiku": lambda p: call_claude_haiku(p),
    "gpt-4o-mini": lambda p: call_codex(p, "gpt-4o-mini"),
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
