"""Preflight checks and actionable error hints for Google Cloud newcomers.

Used by `make eval`, `make deploy`, `make test-remote`, `make delete` and the
Seed Pack `load.py` scripts so that the most common setup mistakes (no ADC,
missing quota project, API not enabled, missing bucket/dataset, wrong Gemini
location) fail with one clear "do this next" message instead of a traceback.
"""

from __future__ import annotations

import re

from app.loader import is_placeholder, resolve_env_config

REQUIRED_APIS = (
    "aiplatform.googleapis.com",
    "bigquery.googleapis.com",
    "storage.googleapis.com",
    "cloudresourcemanager.googleapis.com",
)

_RESOURCE_RE = re.compile(
    r"^projects/(?P<project>[^/]+)/locations/(?P<location>[^/]+)/reasoningEngines/(?P<id>[^/]+)$"
)


def require_project() -> str:
    """Return GOOGLE_CLOUD_PROJECT or raise a RuntimeError explaining how to set it."""
    project = resolve_env_config("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError(
            "GOOGLE_CLOUD_PROJECT is not set. Run `cp .env.example .env` and set "
            "GOOGLE_CLOUD_PROJECT=<your project id> in .env."
        )
    return project


def check_adc(project: str) -> None:
    """Raise a RuntimeError with the fix if Application Default Credentials are missing."""
    try:
        import google.auth
        from google.auth.exceptions import DefaultCredentialsError
    except ImportError as exc:  # pragma: no cover - dependency missing means setup was skipped
        raise RuntimeError("google-auth is not installed. Run `make setup`.") from exc
    try:
        google.auth.default()
    except DefaultCredentialsError as exc:
        raise RuntimeError(
            "No Application Default Credentials found. Run:\n"
            "  gcloud auth application-default login\n"
            f"  gcloud auth application-default set-quota-project {project}"
        ) from exc


def parse_reasoning_engine_name(resource_name: str) -> dict[str, str]:
    """Split `projects/P/locations/L/reasoningEngines/ID` into its parts (or raise ValueError)."""
    match = _RESOURCE_RE.match((resource_name or "").strip())
    if not match:
        raise ValueError(
            f"'{resource_name}' is not a full Agent Runtime resource name. Expected "
            "projects/<project>/locations/<region>/reasoningEngines/<id>."
        )
    return match.groupdict()


def explain_gcp_error(exc: BaseException, project: str | None = None) -> str:
    """Return a one-paragraph actionable hint for common Google Cloud errors ('' if unknown)."""
    text = f"{type(exc).__name__}: {exc}"
    lowered = text.lower()
    proj = project or resolve_env_config("GOOGLE_CLOUD_PROJECT") or "<your-project-id>"

    if "defaultcredentialserror" in lowered or "default credentials" in lowered:
        return (
            "No Application Default Credentials. Run `gcloud auth application-default login` "
            f"and `gcloud auth application-default set-quota-project {proj}`."
        )
    if "reauthentication" in lowered or "invalid_grant" in lowered:
        return "Your gcloud credentials expired. Run `gcloud auth application-default login` again."
    if "service_disabled" in lowered or "has not been used in project" in lowered or (
        "api" in lowered and "is disabled" in lowered
    ):
        return (
            "A required Google Cloud API is not enabled. Run:\n"
            f"  gcloud services enable {' '.join(REQUIRED_APIS)} --project={proj}\n"
            "then wait ~1 minute and retry."
        )
    if "quota project" in lowered or "user_project_denied" in lowered or "x-goog-user-project" in lowered:
        return f"ADC has no/incorrect quota project. Run `gcloud auth application-default set-quota-project {proj}`."
    if "not found: dataset" in lowered:
        return "The BigQuery dataset does not exist yet. Run `make seed PACK=<your pack>` first."
    if "bucket does not exist" in lowered or ("notfound: 404" in lowered and "/b/" in lowered):
        return (
            "The staging bucket does not exist. Create it once with:\n"
            f"  gcloud storage buckets create gs://{proj}-hackathon-staging --location=europe-west1 --project={proj}"
        )
    if ("publisher model" in lowered or "models/" in lowered) and ("404" in lowered or "not_found" in lowered or "not found" in lowered):
        return (
            "Gemini model not found at this location. Check GEMINI_MODEL in .env and keep "
            "GOOGLE_CLOUD_LOCATION=global (Gemini 3.8 Flash is served on the global endpoint)."
        )
    if "build failed" in lowered:
        return (
            "Agent Runtime could not build the container. Read the build log with:\n"
            "  gcloud logging read 'resource.type=\"aiplatform.googleapis.com/ReasoningEngine\"' "
            f"--project={proj} --freshness=30m --limit=50 --format='value(textPayload)'\n"
            "A timeout pulling the base image is transient: just re-run `make deploy`. A pip error "
            "means a package in PINNED_REQUIREMENTS (deploy/deploy.py) is wrong."
        )
    if "missing key inputs" in lowered or "api_key" in lowered:
        return (
            "The Gemini client is not in Vertex AI mode. Set GOOGLE_GENAI_USE_VERTEXAI=1 and "
            "GOOGLE_CLOUD_PROJECT in .env."
        )
    if "permission_denied" in lowered or "403" in lowered or "access denied" in lowered:
        hint = (
            f"Permission denied in project '{proj}'. Check you are using the right project "
            "(`gcloud config get-value project`), that your account has Vertex AI User, BigQuery "
            "User and Storage Admin roles, and that the quota project is set "
            f"(`gcloud auth application-default set-quota-project {proj}`)."
        )
        if any(
            key in lowered
            for key in ("bigquery", "jobs.create", "dataset", "access denied: table", "gcp-sa-aiplatform-re")
        ):
            member = "serviceAccount:service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
            hint += (
                " If this is a deployed Agent Runtime agent (works in `make dev`, fails after "
                "`make deploy`), it runs as service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re."
                "iam.gserviceaccount.com, not your ADC user: re-run `make deploy` with Project IAM "
                "Admin, or grant roles/bigquery.dataEditor and roles/bigquery.jobUser:\n"
                f"  gcloud projects add-iam-policy-binding {proj} --member={member} "
                "--role=roles/bigquery.dataEditor\n"
                f"  gcloud projects add-iam-policy-binding {proj} --member={member} "
                "--role=roles/bigquery.jobUser\n"
                f"(Find <PROJECT_NUMBER> with `gcloud projects describe {proj} "
                "--format='value(projectNumber)'`.)"
            )
        return hint
    if "resource_exhausted" in lowered or "429" in lowered:
        return "Gemini quota exhausted (429). Wait 30-60 seconds and retry; avoid parallel eval runs."
    return ""


def format_error(exc: BaseException, project: str | None = None) -> str:
    """Return `ERROR: <exc>` plus a `HINT:` line when a known fix exists."""
    hint = explain_gcp_error(exc, project)
    message = f"ERROR: {type(exc).__name__}: {exc}"
    return f"{message}\nHINT: {hint}" if hint else message
