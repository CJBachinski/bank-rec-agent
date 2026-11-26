# Requirement map

This project was built to mirror what forward deployed roles at finance-AI companies ask for. The requirements below are paraphrased from two postings I tracked: an AI deployment strategist role at a company that builds agents for corporate finance teams, and a deployment associate role at an AI-native accounting platform. Each row points at where the repository shows the work.

| What the postings ask for | Where it is shown |
|---|---|
| Design, configure and tune AI agent workflows against a customer's data, systems and conventions | `recon/agent.py` (tool loop, structured decisions), `recon/pipeline.py` (clearing policy), `prompts/v2.md` (this company's conventions) |
| Maintain prompt libraries and tune model configurations | `prompts/` with three versions and [CHANGELOG.md](../prompts/CHANGELOG.md); model comparison in the README, section 4.5 |
| Build and test eval sets | `evals/cases.py` (trap set with controls), `evals/grade.py` (code-only grader), `evals/run.py` (resumable harness), `evals/fakes.py` (stand-in models that prove the harness) |
| Spot where an AI workflow falls short and refine prompts | [FAILURE_ANALYSIS.md](FAILURE_ANALYSIS.md) |
| Proficient in SQL; comfortable working with customer data | `sql/schema.sql` (tables, status views, tie-out view), SQL candidate queries in `recon/rules.py`, file validation in `recon/ingest.py` |
| Integrations across ERPs and banks | `recon/ingest.py`: two bank export formats and a general ledger report loaded into one model, each checked against itself |
| Understand accounting workflows: close, reconciliations | `recon/report.py` (adjusted bank to adjusted book), outstanding checks, deposits in transit, returned items, duplicated and transposed entries in `recon/synth.py` |
| Build go-live reconciliations tying bank to the general ledger | `scripts/rules_baseline.py`, `recon/report.py`, the reconciliation written to `out/` |
| Test configuration with AI-assisted checks | `recon/verify.py`: every model proposal is checked by code before it touches the books |
| Identify and prioritise use cases with finance teams | README section 2 (workflow map: which steps are mechanical, which are judgement, and which suit an agent) |
| Scope, build and launch deployments | README section 5 (three-phase rollout with exit criteria and controls) |
| Identify risks early and escalate | README section 7 and the failure analysis: what the evidence cannot show |
| Comfortable building with AI coding tools | Built with Claude Code; `tests/` holds the tests that kept it honest |
