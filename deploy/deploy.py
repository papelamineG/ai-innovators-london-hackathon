"""Deploy the active ADK Agent + Seed Pack to Vertex AI Agent Runtime (Reasoning Engine).

Enforces Python 3.12 cloudpickle parity, injects runtime environment variables,
bundles `app/` and `seed_packs/` via `extra_packages`, and writes `.deployed_agent.json`.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv
import vertexai
from vertexai import agent_engines
from vertexai.agent_engines import AdkApp

from app.agent import build_agent
from app.loader import REPO_ROOT, resolve_env_config
from app.preflight import (
    check_adc,
    format_error,
    parse_reasoning_engine_name,
    require_project,
)

STATE_FILE = REPO_ROOT / ".deployed_agent.json"

# Pinned requirements matching the Python 3.12 workstation environment to
# prevent cloudpickle deserialization skew inside Agent Runtime containers.
PINNED_REQUIREMENTS = [
    "google-adk==2.11.0",
    "google-cloud-aiplatform[agent_engines,adk]==1.165.1",
    "google-genai==2.31.0",
    "google-cloud-bigquery==3.46.2",
    "google-cloud-storage==3.17.1",
    "cloudpickle==3.1.2",
    "pydantic==2.14.1",
    "pyyaml==6.0.3",
    "python-dotenv==1.2.4",
    "mcp==1.30.0",
]


# Project roles the Agent Runtime service agent
# (service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com) needs so that
# deployed Seed Pack tools can read/write BigQuery, call Gemini and use Cloud Storage.
REASONING_ENGINE_RUNTIME_ROLES = (
    "roles/bigquery.dataEditor",
    "roles/bigquery.jobUser",
    "roles/aiplatform.user",
    "roles/storage.objectAdmin",
)

_CRM_PROJECTS_URL = "https://cloudresourcemanager.googleapis.com/v1/projects"
_IAM_HTTP_TIMEOUT_SECONDS = 10
_IAM_ETAG_CONFLICT_RETRIES = 2


def _authorized_session():
    """Return an AuthorizedSession built from Application Default Credentials."""
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return AuthorizedSession(creds)


def _reasoning_engine_service_agent(project_number: str) -> str:
    return f"serviceAccount:service-{project_number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"


def _add_runtime_bindings(policy: dict, member: str) -> bool:
    """Additively bind `member` to every runtime role in `policy` (in place).

    Only unconditional bindings (no "condition") count; conditional bindings, other
    members, `etag` and `version` are preserved. Returns True if the policy changed.
    """
    bindings = policy.setdefault("bindings", [])
    has_conditions = any("condition" in binding for binding in bindings)
    changed = False
    for role in REASONING_ENGINE_RUNTIME_ROLES:
        unconditional = [b for b in bindings if b.get("role") == role and "condition" not in b]
        if any(member in b.get("members", []) for b in unconditional):
            continue
        if unconditional:
            unconditional[0].setdefault("members", []).append(member)
        else:
            bindings.append({"role": role, "members": [member]})
        changed = True
    current_version = int(policy.get("version") or 1)
    policy["version"] = max(current_version, 3) if has_conditions else current_version
    return changed


def ensure_reasoning_engine_iam(project_id: str, resource_name: str) -> bool:
    """Grant the Agent Runtime service agent the BigQuery/Vertex/Storage roles it needs.

    Deployed agents run as service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com
    instead of the developer's ADC user, so BigQuery tools that work in `make dev` get
    403 Access Denied after `make deploy` unless these roles are bound. The update is
    additive (read-modify-write with etag) and raises on HTTP errors such as 403.
    """
    project_part = parse_reasoning_engine_name(resource_name)["project"]
    session = _authorized_session()
    if project_part.isdigit():
        project_number = project_part
    else:
        response = session.get(
            f"{_CRM_PROJECTS_URL}/{project_id}", timeout=_IAM_HTTP_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        project_number = str(response.json()["projectNumber"])
    member = _reasoning_engine_service_agent(project_number)

    for attempt in range(_IAM_ETAG_CONFLICT_RETRIES + 1):
        response = session.post(
            f"{_CRM_PROJECTS_URL}/{project_id}:getIamPolicy",
            json={"options": {"requestedPolicyVersion": 3}},
            timeout=_IAM_HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        policy = response.json()
        if not _add_runtime_bindings(policy, member):
            print(f"[deploy] Verified BigQuery/Vertex/Storage IAM roles on {member}.")
            return True
        response = session.post(
            f"{_CRM_PROJECTS_URL}/{project_id}:setIamPolicy",
            json={"policy": policy},
            timeout=_IAM_HTTP_TIMEOUT_SECONDS,
        )
        if response.status_code == 409 and attempt < _IAM_ETAG_CONFLICT_RETRIES:
            print("[deploy] IAM policy changed concurrently (etag conflict); retrying...")
            continue
        response.raise_for_status()
        print(f"[deploy] Granted BigQuery/Vertex/Storage IAM roles to {member}.")
        return True
    return False  # pragma: no cover - the loop always returns or raises


def _ensure_runtime_iam_best_effort(project_id: str, resource_name: str) -> bool:
    """Run ensure_reasoning_engine_iam but never raise: print the manual fix instead."""
    try:
        return ensure_reasoning_engine_iam(project_id, resource_name)
    except Exception as exc:  # IAM is best-effort; never abort make deploy
        try:
            project_part = parse_reasoning_engine_name(resource_name)["project"]
        except ValueError:
            project_part = ""
        number = project_part if project_part.isdigit() else "<PROJECT_NUMBER>"
        member = _reasoning_engine_service_agent(number)
        commands = "\n".join(
            f"  gcloud projects add-iam-policy-binding {project_id} --member={member} --role={role}"
            for role in ("roles/bigquery.dataEditor", "roles/bigquery.jobUser")
        )
        lookup = (
            ""
            if number != "<PROJECT_NUMBER>"
            else f"\n(Find <PROJECT_NUMBER> with `gcloud projects describe {project_id} "
            "--format='value(projectNumber)'`.)"
        )
        print(
            f"[deploy] NOTE: could not verify/grant runtime IAM roles for the deployed agent "
            f"({type(exc).__name__}: {exc}). The deployment itself succeeded. If the agent gets "
            "403 Access Denied on BigQuery, ask a Project IAM Admin to run:\n"
            f"{commands}{lookup}"
        )
        return False


def _assert_python_312() -> None:
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError(
            f"Vertex AI Agent Runtime requires Python 3.12 for cloudpickle parity "
            f"(running Python {sys.version_info.major}.{sys.version_info.minor}). "
            "Use `.venv/bin/python` created with `uv venv --python 3.12`."
        )


def load_saved_resource(
    state_file: Path,
    *,
    project_id: str,
    runtime_location: str,
) -> str | None:
    """Return the resource name saved by a previous `make deploy`, or None.

    Raises RuntimeError (instead of silently creating a duplicate agent) when the
    state file is unreadable or points at a different project/region.
    """
    if not state_file.exists():
        return None
    try:
        saved = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"{state_file.name} is unreadable ({exc}). Fix or delete it by hand; if it named a "
            "live agent, remove that agent with `make delete RESOURCE=<resource name>`."
        ) from exc
    resource = str(saved.get("resource_name") or "").strip()
    if not resource:
        return None
    parts = parse_reasoning_engine_name(resource)
    saved_project = str(saved.get("project_id") or "")
    if parts["location"] != runtime_location or (saved_project and saved_project != project_id):
        raise RuntimeError(
            f"{state_file.name} points at {resource} (project '{saved_project}'), but .env now "
            f"targets project '{project_id}' in '{runtime_location}'. Run `make delete` first to "
            "remove the old agent, or `make deploy ARGS=--create-new` to keep it and deploy a new one."
        )
    return resource


def _is_not_found(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return "notfound" in text or "404" in text or "not_found" in text


def _assert_staging_bucket(project_id: str, staging_bucket: str) -> None:
    """Fail fast with the create command when the staging bucket is missing."""
    from google.cloud import storage

    bucket_name = staging_bucket.replace("gs://", "").strip("/").split("/")[0]
    if not storage.Client(project=project_id).bucket(bucket_name).exists():
        raise RuntimeError(
            f"Staging bucket gs://{bucket_name} does not exist. Create it once with:\n"
            f"  gcloud storage buckets create gs://{bucket_name} --location=europe-west1 "
            f"--project={project_id}"
        )


def deploy_agent(
    *,
    pack_id: str | None = None,
    create_new: bool = False,
    state_file: Path | None = None,
    check_bucket: bool = True,
) -> str:
    """Package and deploy (or update) the ADK agent on Vertex AI Agent Runtime."""
    load_dotenv(REPO_ROOT / ".env")
    _assert_python_312()
    state_path = state_file or STATE_FILE
    # extra_packages are relative paths: always package from the repo root.
    os.chdir(REPO_ROOT)

    project_id = require_project()

    runtime_location = resolve_env_config("AGENT_RUNTIME_LOCATION", default="europe-west1")
    gemini_location = resolve_env_config("GOOGLE_CLOUD_LOCATION", default="global")
    model_name = resolve_env_config("GEMINI_MODEL")
    staging_bucket = resolve_env_config(
        "STAGING_BUCKET",
        default=f"gs://{project_id}-hackathon-staging",
    )
    if not staging_bucket.startswith("gs://"):
        staging_bucket = f"gs://{staging_bucket}"
    if not model_name:
        raise RuntimeError("GEMINI_MODEL is not set. Set GEMINI_MODEL in .env (see .env.example).")

    existing_resource: str | None = None
    if not create_new:
        existing_resource = load_saved_resource(
            state_path, project_id=project_id, runtime_location=runtime_location
        )
    elif state_path.exists():
        print(
            f"[deploy] WARNING: --create-new keeps the agent recorded in {state_path.name}; it is "
            "NOT deleted and will no longer be tracked. Delete it with `make delete RESOURCE=...`."
        )

    if check_bucket:
        check_adc(project_id)
        _assert_staging_bucket(project_id, staging_bucket)

    # Disable local stdio MCP subprocesses before constructing the pickled agent
    os.environ["ENABLE_MCP_STDIO"] = "0"
    os.environ["AGENT_RUNTIME_DEPLOY"] = "1"

    agent, pack = build_agent(pack_id=pack_id, enable_mcp_stdio=False)
    app = AdkApp(agent=agent, enable_tracing=True)

    # Note: Agent Runtime forbids passing GOOGLE_CLOUD_PROJECT or GOOGLE_CLOUD_LOCATION
    # in env_vars (they are reserved by the container runtime). We pass AGENT_ENGINE_PROJECT
    # and GEMINI_LOCATION instead.
    env_vars = {
        "GOOGLE_GENAI_USE_VERTEXAI": "1",
        "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
        "AGENT_ENGINE_PROJECT": project_id,
        "GEMINI_MODEL": model_name,
        "GEMINI_LOCATION": gemini_location,
        "SEED_PACK": pack.pack_id if pack.pack_id != "hello_fallback" else "",
        "SEED_BQ_DATASET": pack.bigquery_dataset or "",
        "ENABLE_MCP_STDIO": "0",
        "AGENT_RUNTIME_DEPLOY": "1",
    }

    display_name = f"hackathon-{pack.pack_id}".replace("_", "-")
    description = f"AI Innovators Expedition Starter ({pack.title})"

    print(f"[deploy] Initializing Vertex AI in project='{project_id}', location='{runtime_location}', staging_bucket='{staging_bucket}'...")
    vertexai.init(
        project=project_id,
        location=runtime_location,
        staging_bucket=staging_bucket,
    )

    extra_packages = ["app", "seed_packs"]
    deploy_kwargs = {
        "requirements": PINNED_REQUIREMENTS,
        "display_name": display_name,
        "description": description,
        "extra_packages": extra_packages,
        "env_vars": env_vars,
    }

    resource_name: str | None = None
    if existing_resource:
        print(f"[deploy] Updating existing Agent Runtime instance: {existing_resource} ...")
        try:
            remote_app = agent_engines.update(
                resource_name=existing_resource, agent_engine=app, **deploy_kwargs
            )
            resource_name = remote_app.resource_name
        except Exception as exc:
            if "'code': 13" in str(exc):
                # Known Agent Runtime quirk: code 13 can be reported for an update that applied.
                # The probe proves the engine exists, NOT that it runs the new build;
                # `make test-remote` is the real check.
                print("[deploy] Received code 13 during update; probing existing resource...")
                resource_name = agent_engines.get(existing_resource).resource_name
            elif _is_not_found(exc):
                print(
                    f"[deploy] Saved agent {existing_resource} no longer exists; creating a new one."
                )
            else:
                # Never fall through to create(): that would orphan the saved agent.
                raise RuntimeError(
                    f"Updating {existing_resource} failed: {exc}\n"
                    "Nothing was created. Fix the error and re-run `make deploy`, or run "
                    "`make delete` and then `make deploy`."
                ) from exc

    if resource_name is None:
        print(f"[deploy] Creating Agent Runtime instance '{display_name}' (this takes ~3-6 minutes)...")
        remote_app = agent_engines.create(agent_engine=app, **deploy_kwargs)
        resource_name = remote_app.resource_name

    if check_bucket:
        # Best-effort: the deployed agent runs as the Agent Runtime service agent, not your
        # ADC user. Never raises (offline unit tests pass check_bucket=False and skip it).
        _ensure_runtime_iam_best_effort(project_id, resource_name)

    metadata = {
        "resource_name": resource_name,
        "project_id": project_id,
        "runtime_location": runtime_location,
        "pack_id": pack.pack_id,
        "gemini_model": model_name,
        "deployed_at": datetime.now(timezone.utc).isoformat(),
    }
    state_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"DEPLOYED_RESOURCE_NAME: {resource_name}")
    return resource_name


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy ADK agent to Vertex AI Agent Runtime.")
    parser.add_argument("--pack", default=None, help="Override SEED_PACK id.")
    parser.add_argument("--create-new", action="store_true", help="Force creating a new ReasoningEngine.")
    args = parser.parse_args()
    try:
        deploy_agent(pack_id=args.pack, create_new=args.create_new)
    except Exception as exc:
        print(format_error(exc), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
