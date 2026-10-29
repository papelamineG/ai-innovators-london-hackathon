# AI Innovators Expedition — London Hackathon Starter Reference

> **Note:** For the complete event guide, rules, scoring rubric, and Challenge Card index, see the root [`README.md`](../README.md), [`docs/PREWORK.md`](./PREWORK.md), and [`challenges/README.md`](../challenges/README.md).
>
> *This is not an officially supported Google product. All datasets, companies, personas, and mock action APIs in this repository are 100% synthetic and fictional.*

Welcome to the common **Starter** repository for the **AI Innovators Expedition** (Google London, 29–30 October 2026). Every **Table** starts from this repository and activates its tailored **Seed Pack** (`t01`..`t10`) so you can go from clone to a **deployed, evaluated Google ADK agent on Vertex AI Agent Runtime** inside your first hour of **Build Time**.

---

## 15-Minute Quickstart

### 1. Prerequisites
- **Python 3.12** (required for `cloudpickle` parity with Vertex AI Agent Runtime containers)
- **[`uv`](https://docs.astral.sh/uv/)** package manager (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **[Google Cloud SDK (`gcloud`)](https://cloud.google.com/sdk/docs/install)** authenticated against your Table's **Scored Deployment** project (**Google-provided Sandbox Project** or **Own-Org Participant** project):
  ```bash
  gcloud auth login
  gcloud auth application-default login
  gcloud config set project YOUR_GCP_PROJECT_ID
  gcloud auth application-default set-quota-project YOUR_GCP_PROJECT_ID
  ```
- Enable the APIs the Starter uses (once per project, takes ~1 minute):
  ```bash
  gcloud services enable aiplatform.googleapis.com bigquery.googleapis.com \
    storage.googleapis.com cloudresourcemanager.googleapis.com --project=YOUR_GCP_PROJECT_ID
  ```
- **macOS or Linux** with GNU Make and bash. **Windows:** use WSL2 (Ubuntu) and run everything inside it.

### 2. Install Python 3.12 Environment (`uv`)
```bash
make setup
```

### 3. Configure `.env`
Copy `.env.example` to `.env` and set your project ID, staging bucket, and Table **Seed Pack** (e.g. `t08` for Table 8, or `t00_example` for the reference coffee roastery pack):
```bash
cp .env.example .env
```
Edit `.env` (plain `KEY=value` lines, no quotes):
```dotenv
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_GENAI_USE_VERTEXAI=1
GOOGLE_CLOUD_LOCATION=global
GEMINI_MODEL=gemini-3.8-flash
AGENT_RUNTIME_LOCATION=europe-west1
STAGING_BUCKET=
SEED_PACK=t08
BQ_LOCATION=EU
```
An empty `STAGING_BUCKET` means `gs://<GOOGLE_CLOUD_PROJECT>-hackathon-staging`. Create that bucket once if it does not exist yet:
```bash
gcloud storage buckets create gs://$(grep '^GOOGLE_CLOUD_PROJECT=' .env | cut -d= -f2)-hackathon-staging \
  --location=europe-west1
```

### 4. Seed Your Table's BigQuery Dataset & Storage Assets
```bash
make seed PACK=t08
```
*(Or run `make seed PACK=t00_example` to test with the built-in specialty roastery reference pack.)*

### 5. Run Local Unit Tests & Launch the ADK Web UI
```bash
make test
make dev
```
Open **`http://127.0.0.1:8000`** in your browser, select the `app` agent from the top-left dropdown, and inspect tool calls and traces in the **Trace** tab.

### 6. Run the Evaluation Pipeline (Must-Have)
Each Seed Pack ships with 5 golden evaluation scenarios in `seed_packs/<pack>/eval/golden.test.json`:
```bash
make eval
```
This validates your `.test.json` files against the ADK `EvalSet` schema, runs each conversation turn against your agent, scores both tool-call trajectory (`tool_trajectory_avg_score`, by tool name) and reference response grounding (`response_match_score`, token overlap), and prints a pass/fail summary table ending in `EVAL_SUMMARY: x/y cases passed`. Thresholds live in `eval/test_config.json`. Use `make eval-validate` to check the JSON files without calling Gemini.

### 7. Deploy to Vertex AI Agent Runtime & Verify Remotely (Must-Have)
```bash
make deploy
make test-remote
```
- `make deploy` packages `app/` and `seed_packs/` using Python 3.12, deploys (or updates in-place) your agent on **Vertex AI Agent Runtime** in `AGENT_RUNTIME_LOCATION` (`europe-west1`), and saves the resource name to `.deployed_agent.json`.
- `make test-remote` creates a live session on your deployed `ReasoningEngine`, streams the first question from your pack's golden evals (or `make test-remote PROMPT="..."`), and prints the remote tool calls and response.
- Re-running `make deploy` **updates the same agent in place**. If an update fails, nothing new is created; fix the error and retry. `make deploy ARGS=--create-new` deliberately creates a second agent.
- To delete the remote deployment when finished (only the agent saved in `.deployed_agent.json`, or the one you name):
  ```bash
  make delete                       # or: make delete RESOURCE=projects/.../reasoningEngines/123
  ```

---

## Team Git Workflow (1 Shared Private Repo per Table)

Every Participant has their own pre-configured Google Cloud sandbox account (`@gcplab.me` with Agent Platform Pay-As-You-Go) or company Google Cloud account, so all 5 Table members can use **Antigravity 2.0** in parallel. Each **Table** collaborates in **one shared private GitHub repository** created by your Table's **Repo Captain**:

1. **Repo Captain (Minute 0)**:
   - Create a **private** GitHub repository for your Table and push this Starter repo (`git remote add origin ... && git push -u origin main`).
   - Invite all 4 teammates as collaborators.
   - Everyone clones the repo, runs `make setup`, and creates their local `.env` pointing at the Table's chosen **Scored Deployment** project.
2. **Parallel Collaboration in Antigravity**:
   - All 5 Table members can work in parallel across agent orchestration, BigQuery/MCP tools, evaluation cases in `seed_packs/<pack>/eval/golden.test.json`, optional security guardrails, and the 2-slide **Pitch** + live demo.
   - Before pushing changes to the shared repo, run:
     ```bash
     make test && git add -A && git commit -m "Update agent and evals" && git push
     ```
   - Teammates run `git pull` to stay in sync in Antigravity (which reads [`AGENTS.md`](../AGENTS.md) automatically).

### Unlocking the Agentic SDLC (`+1 pt`) & CI/CD Pipeline (`+1 pt`) Bonuses

#### 1. Agentic SDLC Practices (`+1 pt` — demonstrate at least 2 of 3 pillars)
1. **Pillar 1 — Custom Antigravity Context & Primitives**: Extend [`AGENTS.md`](../AGENTS.md) with your Table's domain invariants, or create custom rules, skills, or subagents (`.agents/rules/`, `.agents/skills/`) tailored to your architecture.
2. **Pillar 2 — Spec / Eval-First Development (Plan-Act-Verify & TDD)**: Write a brief feature spec/plan and add new golden evaluation scenarios in `seed_packs/<pack>/eval/golden.test.json` or unit tests in `tests/test_starter.py` *before* asking Antigravity to implement a new tool or prompt change, then verify with `make test` and `make eval`.
3. **Pillar 3 — Parallel Multi-Agent Git Workflow (`git worktree` & PRs)**: Instead of 5 builders pushing directly to `main`, use feature branches, isolated Git worktrees (`git worktree add ../wt-evals -b feat/evals`), and Pull Requests so multiple coding agents work concurrently without file collisions.

#### 2. Automated CI/CD Pipeline (`+1 pt` — green pipeline run running tests/evals + deploy)
Because `@gcplab.me` sandbox projects enforce org policies that block exporting Service Account JSON keys, use **GitHub Actions** for fast PR quality gates (`make test` + `make eval-validate`) and **Google Cloud Build** (which runs natively inside your GCP project using the Cloud Build service agent—no exported keys needed!) or Workload Identity Federation to run `make eval` + `make deploy` to Vertex AI Agent Runtime:

- **Example `.github/workflows/ci.yml` (PR & Push Quality Gates)**:
  ```yaml
  name: Agent CI Gates
  on:
    pull_request:
    push:
      branches: [main]
  jobs:
    test-and-validate-evals:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v4
        - uses: astral-sh/setup-uv@v4
          with:
            python-version: "3.12"
        - name: Install dependencies & run offline tests + eval schema check
          run: |
            make setup
            make test
            SEED_PACK=t08 make eval-validate
  ```
- **Example `cloudbuild.yaml` (Test + Eval + Deploy to Agent Runtime on GCP)**:
  ```yaml
  steps:
    - name: "ghcr.io/astral-sh/uv:python3.12-bookworm"
      entrypoint: "bash"
      env:
        - "GOOGLE_CLOUD_PROJECT=$PROJECT_ID"
        - "GOOGLE_GENAI_USE_VERTEXAI=1"
        - "GOOGLE_CLOUD_LOCATION=global"
        - "GEMINI_MODEL=gemini-3.8-flash"
        - "AGENT_RUNTIME_LOCATION=europe-west1"
        - "SEED_PACK=${_SEED_PACK}"
      args:
        - "-c"
        - |
          uv venv --python 3.12 .venv
          uv pip install --python .venv/bin/python -r pyproject.toml
          .venv/bin/pytest -q
          .venv/bin/python eval/run_eval.py --validate-only
          .venv/bin/python deploy/deploy.py
  substitutions:
    _SEED_PACK: "t08"
  ```
  *(Trigger via a Cloud Build GitHub trigger or `gcloud builds submit --config=cloudbuild.yaml --substitutions=_SEED_PACK=t08` and show the green build log + deployed `ReasoningEngine` to your Mentor or the Jury!)*

---

## Architecture & Seed Pack Plugin Contract

```text
ai-innovators-london-hackathon/
├── app/
│   ├── agent.py                  # Root ADK LlmAgent (reads GEMINI_MODEL from env)
│   ├── loader.py                 # Seed Pack plugin loader (YAML + BQ tools + MCP stdio/fallback)
│   └── preflight.py              # Clear "do this next" hints for common Google Cloud errors
├── challenges/
│   ├── README.md                 # Index of all 10 Table Challenge Cards
│   └── T01 .. T10-*.md           # Per-Table Challenge Cards (copies of seed_packs/tNN_*/CHALLENGE_CARD.md)
├── deploy/
│   ├── deploy.py                 # One-command deploy/update to Vertex AI Agent Runtime
│   ├── test_remote.py            # Live session + stream_query verification against Agent Runtime
│   └── delete.py                 # Clean up deployed ReasoningEngine resource
├── docs/
│   ├── PREWORK.md                # Participant Pre-Work checklist
│   └── STARTER.md                # Starter technical reference
├── eval/
│   ├── run_eval.py               # Golden evaluation runner & ADK EvalSet validator
│   ├── test_config.json          # Default tool trajectory & response match thresholds
│   └── hello_fallback.test.json  # Baseline evalset when SEED_PACK is unset
├── seed_packs/
│   ├── t00_example/              # Reference pack (London Specialty Roastery)
│   └── t01 .. t10/               # Per-Table Seed Packs (pack.yaml, tools.py, mcp_server.py, load.py, eval/, CHALLENGE_CARD.md)
├── tests/
│   └── test_starter.py           # Offline pytest suite
├── AGENTS.md                     # Rules & conventions for Antigravity
├── LICENSE                       # Apache License 2.0
├── Makefile                      # One-command workflow targets
├── pyproject.toml                # Pinned Python 3.12 dependencies
└── README.md                     # Participant guide & hackathon overview
```

> **Seed Pack contract:** `pack.yaml` (id, title, instruction, `bigquery_dataset`, optional `mcp_server`), `tools.py` exporting `TOOLS = [...]`, and, if `mcp_server` is set, `mcp_server.py` exporting `MCP_TOOLS = [...]` (the functions decorated with `@mcp.tool()`). The loader only uses these explicit lists.

> **Note on MCP Stdio vs. Vertex AI Agent Runtime:** Locally (`make dev`), if a Seed Pack defines `mcp_server` in `pack.yaml`, `app/loader.py` attaches its **Mock Action API** (`FastMCP`) via ADK's `McpToolset` over `stdio`. Because Vertex AI Agent Runtime containers are serverless and do not persist local workstation subprocess paths across `cloudpickle`, `deploy/deploy.py` sets `ENABLE_MCP_STDIO=0` and the loader automatically exposes the pack's mock action functions as native Python `FunctionTool` callables—giving you 100% tool parity locally and in the cloud.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `DefaultCredentialsError` or `403 PERMISSION_DENIED` | ADC credentials missing or quota project unset | Run `gcloud auth application-default login` and `gcloud auth application-default set-quota-project $GOOGLE_CLOUD_PROJECT`. |
| `SERVICE_DISABLED` / `API ... has not been used in project` | Required API not enabled | Run the `gcloud services enable ...` command from Prerequisites, wait a minute, retry. |
| `Staging bucket gs://... does not exist` | Bucket not created yet | Run the `gcloud storage buckets create ...` command from step 3. |
| `Not found: Dataset ...` in a tool result | Seed data not loaded | Run `make seed PACK=<your pack>`. |
| `.venv/bin/python not found` | Setup skipped | Run `make setup`. |
| `404 Not Found` calling `gemini-3.8-flash` | Regional endpoint used instead of `global` | Ensure `GOOGLE_CLOUD_LOCATION=global` in `.env`. `app/agent.py` passes this location to `Gemini(client_kwargs=...)` (and deploy forwards it as `GEMINI_LOCATION`), while `AGENT_RUNTIME_LOCATION=europe-west1` is only used for Agent Runtime. |
| Agent returns blank reply or `KeyError` on turn 1 | Unescaped `{variable}` in `instruction` | ADK treats `{foo}` in `instruction` as a session-state placeholder. Replace `{foo}` with `[foo]`. |
| Tool spinner hangs or stream dies mid-turn | Tool raised a Python exception | ADK tools must **never raise**; catch exceptions and return `json.dumps({"status": "ERROR", "error": str(exc)})`. Inspect container logs with `gcloud logging read 'resource.type="aiplatform.googleapis.com/ReasoningEngine"' --project=$GOOGLE_CLOUD_PROJECT --limit=20`. |
| Deserialization error in Agent Runtime | Deployed from Python 3.13 instead of 3.12 | Run via `make deploy` (which uses `.venv/bin/python` on Python 3.12 and checks `sys.version_info[:2] == (3, 12)`). |
| Deployed agent gets `403 Access Denied` reading/writing BigQuery (works locally in `make dev`) | Agent Runtime runs as `service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com` rather than your user ADC | Re-run `make deploy` (which auto-grants runtime roles when you have Project IAM Admin), or grant `roles/bigquery.dataEditor` and `roles/bigquery.jobUser` to `serviceAccount:service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com` with `gcloud projects add-iam-policy-binding`. |

---

## Bonus Ideas (1 Point Each When Working Live & Adding Value)

Once your Table has all 5 **Must-Haves** working (`make eval`, `make deploy`, `make test-remote`), each capability that works live in your demo and adds real value to your agent earns **1 whole point** (no maximum; each distinct capability counts once and the jury decides what counts; quality beats quantity):

- **Governance & Observability**: Agent Gateway, Agent Registry, OpenTelemetry / Agent Topology trace spans, or human-in-the-loop approval gates before sensitive actions.
- **Security & Guardrails**: Harden your agent's instructions, input validation, and safety guardrails against the adversarial test vector in your Seed Pack with [Model Armor Overview](https://cloud.google.com/security-command-center/docs/model-armor-overview) and [ADK Safety & Callbacks](https://adk.dev/callbacks/).
- **CI/CD Pipeline**: Wire an automated pipeline (GitHub Actions and/or Google Cloud Build) that runs automated test/eval gates (`make test` + `make eval-validate`/`make eval`) and deploys your agent to Vertex AI Agent Runtime (`make deploy`), verified by showing a green pipeline run during your demo or to a Mentor.
- **Agentic SDLC Practices**: Apply what you learned in the Agentic SDLC session by demonstrating at least **2 of the 3 core pillars** in your Table's repo and workflow: (1) Custom Antigravity rules/skills/subagents or a domain-tailored `AGENTS.md`, (2) Spec/Eval-first development (Plan-Act-Verify / TDD with new `.test.json` golden cases or `pytest` tests before implementation), and (3) Parallel Git collaboration (feature branches, `git worktree` isolation, and Pull Requests).
- **Gemini Enterprise App**: Surface your deployed Agent Runtime engine inside Gemini Enterprise — [Register and Manage an ADK Agent in Gemini Enterprise](https://cloud.google.com/gemini/enterprise/docs/register-and-manage-an-adk-agent).
- **MCP App**: Render an interactive MCP App (`ui://` rich UI component) or extend your Seed Pack's `mcp_server.py` — [ADK Model Context Protocol Tools](https://adk.dev/tools-custom/mcp-tools/).
- **Gemma Open Models**: Experiment with **Gemma** alongside Gemini for specialized sub-tasks — [Gemma Documentation on Google AI](https://ai.google.dev/gemma/docs) & [ADK Gemini/Gemma Models](https://adk.dev/agents/models/).
- **Tribe A2A Bonus (Agent-to-Agent Protocol)**: Connect your Table's agent with your sister Table's agent in the same **Tribe** using ADK's A2A integration — [ADK A2A Documentation](https://adk.dev/agents/custom-agents/) & [Deploy ADK with `--a2a`](https://adk.dev/api-reference/cli/).
- **BigQuery**: Join multiple tables or add parameterized SQL analytics in `seed_packs/<pack>/tools.py` — [BigQuery Python Client Documentation](https://cloud.google.com/python/docs/reference/bigquery/latest).
- **Cloud Run**: Deploy supporting microservices, A2A bridges, or custom MCP endpoints on Cloud Run.
- **Knowledge Graph**: Integrate Enterprise Knowledge Graph or entity resolution into your agent's grounding workflow.
- **Model Routing**: Route dynamically between Google models (e.g. `gemini-3.8-flash` and another Gemini tier or **Gemma**) based on task complexity or latency.
- **Multimodality (Voice, Image)**: Integrate live voice, audio, or image input/output (`GEMINI_IMAGE_MODEL=gemini-3.1-flash-image`).
- **Memory**: Demonstrate stateful cross-session or persistent long-term memory live in your agent workflow.
- **Open Door (Other Capability)**: Any other capability that clearly adds real value to your agent and works live in the demo.
