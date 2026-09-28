"""Model providers behind one interface, so changing vendor/route is an adapter change only.

ClaudeCLIProvider runs `claude -p` on Amy's logged-in account (ResonanceTech Team plan,
decision 2026-09-28). Cost is the `total_cost_usd` the CLI reports. That is the notional
API-price cost the run budget is measured against.

Cost tuning, all measured on real 12-ad batches (2026-09-28):
- Claude Code's own context (MCP tools, skills, settings, CLAUDE.md) is stripped:
  ~27.7k -> ~1.3k tokens of overhead per call.
- Plain JSON in one turn, not `--json-schema`. The schema route takes 2 turns and bills the
  whole input twice: $0.0107 -> $0.0043 per ad. The schema goes in the system prompt and
  validate.py enforces it (invalid -> one repair -> analysis_failed).
- DISABLE_PROMPT_CACHING: every batch is different, so 1h cache writes (2x input price) were
  never read back.
- CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: drops a helper call that re-read the whole input
  for ~13 output tokens (~25% of the bill).

MockProvider returns canned results for tests (spec: "same fixture can run with a mock").
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field

from . import db

PRICES = {  # USD per 1M tokens (input, output) — pricing table cached 2026-06-24
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
}
PRICING_VERSION = "anthropic-2026-06-24"
CALL_OVERHEAD_TOKENS = 1400
OUT_TOKENS_PER_AD = 520  # measured: 5.7-6.4k output tokens per 12-ad batch


@dataclass
class CallResult:
    ok: bool
    output: dict | None
    model_id: str
    cost_usd: float
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    error: str | None = None
    raw: dict = field(default_factory=dict)


def estimate_cost(model: str, prompt_chars: int, n_ads: int) -> float:
    pin, pout = PRICES[model]
    tokens_in = prompt_chars / 3.3 + CALL_OVERHEAD_TOKENS
    tokens_out = OUT_TOKENS_PER_AD * n_ads + 150
    return (tokens_in * pin + tokens_out * pout) / 1_000_000


class ClaudeCLIProvider:
    name = "claude-cli"

    def __init__(self, timeout_s: int = 600):
        self.timeout_s = timeout_s
        self.cwd = db.DATA_DIR / "tmp" / "claude-cwd"  # empty dir: no project CLAUDE.md to pick up
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.mcp = self.cwd / "empty-mcp.json"
        self.mcp.write_text('{"mcpServers": {}}')

    def call(self, model: str, system: str, user: str, schema: dict) -> CallResult:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("CLAUDE_CODE_", "CLAUDECODE")) and k != "ANTHROPIC_BASE_URL"}
        env.update(DISABLE_PROMPT_CACHING="1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
                   CLAUDE_CODE_MAX_OUTPUT_TOKENS="16000")  # 15 ads ≈ 9k output tokens
        system = (system + "\n\nOutput format: reply with ONE JSON object only (no prose, no code "
                  "fences) that matches this JSON Schema:\n" + json.dumps(schema, separators=(",", ":")))
        effort = []
        if model == "claude-haiku-4-5":
            env["MAX_THINKING_TOKENS"] = "0"  # classification doesn't need extended reasoning
        else:
            # Pilot 2026-09-28: Sonnet at default effort spent ~43k output tokens on 2 calls.
            effort = ["--effort", "low"]
        cmd = ["claude", "-p", "--model", model, *effort, "--tools", "", "--no-session-persistence",
               "--output-format", "json", "--strict-mcp-config", "--mcp-config", str(self.mcp),
               "--disable-slash-commands", "--setting-sources", "",
               "--exclude-dynamic-system-prompt-sections",
               "--system-prompt", system]
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, input=user, capture_output=True, text=True,
                                  timeout=self.timeout_s, cwd=self.cwd, env=env, check=False)
        except subprocess.TimeoutExpired:
            return CallResult(False, None, model, 0.0, error="timeout",
                              latency_ms=int((time.time() - t0) * 1000))
        latency = int((time.time() - t0) * 1000)
        try:
            d = json.loads(proc.stdout)
        except ValueError:
            return CallResult(False, None, model, 0.0, error=f"non-json output: {proc.stderr[:200]}",
                              latency_ms=latency)
        usage = d.get("usage") or {}
        tin = (usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0) + \
            (usage.get("cache_creation_input_tokens") or 0)
        # Claude Code may add small helper calls on another model (e.g. Haiku), so modelUsage can
        # hold several models. Attribute the call to the one we asked for; keep the rest as detail.
        mu = d.get("modelUsage") or {}
        model_id = next((k for k in mu if k.startswith(model)), model)
        cost = float(d.get("total_cost_usd") or 0)
        detail = {"modelUsage": mu, "num_turns": d.get("num_turns")}
        out = None if d.get("is_error") else parse_json(d.get("result") or "")
        if out is None:
            return CallResult(False, None, model_id, cost, tin, usage.get("output_tokens") or 0,
                              latency, error=("unparseable output: " + (d.get("result") or ""))[:300],
                              raw=detail)
        return CallResult(True, out, model_id, cost, tin, usage.get("output_tokens") or 0, latency,
                          raw=detail)


def parse_json(text: str) -> dict | None:
    """The model is told to return bare JSON; tolerate code fences or a stray preamble. Never
    regex labels out of prose: if it isn't one JSON object, it's a failure."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        obj = json.loads(t[start:end + 1])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


class MockProvider:
    """Deterministic stand-in. `responder(model, payloads) -> list[result dicts]`."""

    name = "mock"

    def __init__(self, responder, cost_per_call: float = 0.01):
        self.responder = responder
        self.cost_per_call = cost_per_call
        self.calls: list[tuple[str, list[str]]] = []

    def call(self, model: str, system: str, user: str, schema: dict) -> CallResult:
        payloads = json.loads(user.split("<data>\n", 1)[1].rsplit("\n</data>", 1)[0])
        self.calls.append((model, [p["ad_id"] for p in payloads]))
        out = self.responder(model, payloads)
        if out is None:
            return CallResult(False, None, model, self.cost_per_call, error="mock failure")
        return CallResult(True, {"results": out}, model, self.cost_per_call, 1000, 500, 1)

