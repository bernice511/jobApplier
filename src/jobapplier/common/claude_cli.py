"""Calls the `claude` CLI headlessly (subprocess) instead of the Anthropic API, so this
tool piggybacks on the user's existing Claude Code subscription login instead of requiring
a separate Console API key.

IMPORTANT: this requires `claude /login` to have already been completed interactively in a
real terminal on this machine - there is no way to authenticate non-interactively, and a
nested/sandboxed shell (e.g. an agent's own tool-call subprocess) may not share that login
session even when the user's own terminal is logged in.

Deliberately NOT using `--bare`: per `claude --help`, bare mode reads auth strictly from
ANTHROPIC_API_KEY/apiKeyHelper and never from OAuth/keychain, which breaks subscription
login entirely (the whole point of going through the CLI instead of a Console API key).

Every call also passes:
- `--tools ""` - no tool use (these prompts are plain JSON-in, JSON-out already).
- `--safe-mode` - skips CLAUDE.md/hooks/plugins/MCP discovery. Unlike `--bare`, this does
  NOT restrict auth to API-key-only (confirmed via `claude --help`: "Auth, model selection,
  built-in tools... work normally"), so OAuth/subscription login still works.
- `--no-session-persistence` - skip writing a session transcript for these one-shot calls.

Deliberately NOT using `--effort low`: it looked like a speed win in isolation, but on a real
classify_batch call it silently DROPPED batch items from the response instead of just being
more conservative about matched-vs-suggested - 2 of 4 items vanished (not even classified as
"unmet"), at both default and custom system prompts. That's a correctness bug, not a
speed/quality tradeoff, so it's not used even though it measured faster.

Benchmarked on a trivial prompt: the old bare `claude -p --output-format json` invocation
took ~2.85s wall time, ~25k cached input tokens (the full default system prompt + tool
schemas, even though the prompt itself says "do not use any tools"), and 2 model calls (an
internal auto-mode classifier, then the real answer) at ~$0.008. With the flags above:
~1.9s wall, 177 input tokens, 1 model call, ~$0.0004.

`model` and `system_prompt` are optional per-call overrides (`--model` / `--system-prompt` -
the latter REPLACES the default system prompt rather than appending to it, unlike
`--append-system-prompt`). Left unset, a call uses whatever the CLI's default model/system
prompt are today, so resume_parser.py's structuring call and tailor.py/tailoring_service.py's
resume/cover-letter generation are unaffected unless a caller explicitly opts in.
"""
from __future__ import annotations

import json
import subprocess

CLAUDE_CLI_TIMEOUT_SECONDS = 180


class ClaudeCLIError(RuntimeError):
    pass


def _build_argv(model: str | None, system_prompt: str | None) -> list[str]:
    argv = [
        "claude", "-p", "--output-format", "json",
        "--tools", "",
        "--safe-mode",
        "--no-session-persistence",
    ]
    if model:
        argv += ["--model", model]
    if system_prompt:
        argv += ["--system-prompt", system_prompt]
    return argv


def call_claude(
    prompt: str,
    timeout_seconds: int = CLAUDE_CLI_TIMEOUT_SECONDS,
    model: str | None = None,
    system_prompt: str | None = None,
) -> str:
    """Sends `prompt` to `claude` via stdin in headless mode and returns the plain text
    response. Raises ClaudeCLIError on any failure (not logged in, non-zero exit, timeout,
    unparseable output, etc.)."""
    try:
        proc = subprocess.run(
            _build_argv(model, system_prompt),
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise ClaudeCLIError(
            "The `claude` CLI was not found on PATH. Install Claude Code and run "
            "`claude /login` in a terminal first."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ClaudeCLIError(f"claude CLI timed out after {timeout_seconds}s") from exc

    if not proc.stdout.strip():
        raise ClaudeCLIError(
            f"claude CLI produced no output (exit code {proc.returncode}). "
            f"stderr: {proc.stderr.strip()}"
        )

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeCLIError(f"claude CLI output wasn't valid JSON: {proc.stdout[:500]}") from exc

    if payload.get("is_error"):
        result_text = payload.get("result", "") or ""
        if "not logged in" in result_text.lower():
            raise ClaudeCLIError(
                "claude CLI is not logged in in this shell - run `claude /login` in your "
                "terminal first, then run this from that same terminal."
            )
        raise ClaudeCLIError(f"claude CLI returned an error: {result_text}")

    result = payload.get("result")
    if not result:
        raise ClaudeCLIError(f"claude CLI response had no 'result' field: {payload}")
    return result


def _find_json_objects(text: str) -> list[str]:
    """Returns all top-level {...} substrings in text, via balanced-brace scanning."""
    spans = []
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    spans.append(text[start:i + 1])
    return spans


def call_claude_json(
    prompt: str,
    timeout_seconds: int = CLAUDE_CLI_TIMEOUT_SECONDS,
    model: str | None = None,
    system_prompt: str | None = None,
) -> dict:
    """Like call_claude, but strips markdown code fences (if present) and parses the
    result as JSON. Raises ClaudeCLIError if the response isn't valid JSON.

    Occasionally Claude second-guesses itself mid-response and emits commentary plus more
    than one JSON object (e.g. "Wait, let me correct this - {...}"). To tolerate that
    without silently accepting garbage, if a plain parse of the whole response fails, this
    falls back to scanning for balanced {...} objects and trying the LAST one first (it's
    the self-corrected final answer), then earlier ones, before giving up."""
    raw = call_claude(
        prompt, timeout_seconds=timeout_seconds, model=model, system_prompt=system_prompt
    ).strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    for candidate in reversed(_find_json_objects(raw)):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise ClaudeCLIError(f"Claude's response wasn't valid JSON: {raw[:1000]}")
