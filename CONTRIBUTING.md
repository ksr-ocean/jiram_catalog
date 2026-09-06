# Contributing

Everyone, human or agent, follows the same loop (`docs/agent_harness.md`).

1. Open a spec in `docs/specs/<date>_<name>.md` describing goal, scope,
   decisions, and the validation that defines done. Small fixes may
   use a short spec in the pull-request description.
2. Write or extend a gate in `tests/test_gate_<name>.py` when the
   change has ground truth to compare against; offline unit tests
   otherwise.
3. Branch from `main`: `git switch -c <topic>`. Keep shared files
   (`cli.py`, `pyproject.toml`, `uv.lock`) in their own small commits.
4. Before pushing: `JIRAM_SKIP_GATES=1 uv run pytest -q` must pass
   everywhere; `uv run pytest -q` (gates included) must pass on the
   cluster with the mirror available.
5. Open a pull request whose description lists: spec, gate, files
   changed, judgment calls, open questions. A reviewer runs the scope
   check and the gates independently before merging.
6. Record the outcome in `docs/build_log_<date>.md` and any settled
   choice in `docs/decisions.md`.

Environment: `uv sync` creates the environment (Python 3.12). Set
`JIRAM_MIRROR` to your mirror root and `JIRAM_PAPER_DATA` to the
published PJ4 data directory; defaults point at the project group's
shared copies on the cluster.
