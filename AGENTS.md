# AGENTS.md — entry point for any agent working in this repository

Read, in this order: `CLAUDE.md` (project rules), `docs/agent_harness.md`
(the spec -> gate -> execute -> verify loop), `docs/README.md` (map of
the documentation), `docs/decisions.md` (settled choices; do not
reopen without new evidence), `docs/open_items.md` (known gaps).

Rules that apply to every agent, whatever tool runs it:
- Work from a spec in `docs/specs/`. If you were not given one, write
  one first and get it accepted before changing code.
- Gates (`tests/test_gate_*.py`), fixtures (`tests/fixtures/`), and
  specs are read-only for executors. Passing a gate by editing it is
  failure.
- Do not edit `src/jiram_catalog/cli.py`, `pyproject.toml`, or
  `uv.lock` inside a delegated task; expose subcommands through
  `add_subparser(subparsers)` / `run(args)` in your module.
- Do not hand-edit the committed front-end build
  (`src/jiram_catalog/webapp/dist/`); change `frontend/src/` and
  regenerate it with `npm run build`, then commit the rebuilt bundle.
- Never write under the published ground-truth data directory or
  under another user's scratch space. Product writes go under the
  mirror root (`JIRAM_MIRROR`), in the directory the spec names.
- Run the offline suite with `JIRAM_SKIP_GATES=1 uv run pytest -q`;
  gates need the mirror and run on the cluster.
- End every report with the judgment calls you made and any
  ambiguity you resolved, with the options you saw.
