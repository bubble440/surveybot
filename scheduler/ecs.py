# scheduler/ecs.py

import os
import boto3
from account_loader import load_account

RUN_ENV = os.getenv("RUN_ENV", "")

# IS_PROD : variables ECS existantes OU RUN_ENV=aws/gcp
IS_PROD = bool(
    os.getenv("AWS_EXECUTION_ENV")
    or os.getenv("ECS_CONTAINER_METADATA_URI")
    or os.getenv("ECS_CONTAINER_METADATA_URI_V4")
    or RUN_ENV in ("aws", "gcp")
)

# ── Variables AWS (inchangées) ──────────────────────────────────────────────
ECS_CLUSTER          = os.getenv("ECS_CLUSTER")
ECS_TASK_DEFINITION  = os.getenv("ECS_SURVEYBOT_TASK_DEF")
ECS_CONTAINER_NAME   = os.getenv("ECS_SURVEYBOT_CONTAINER", "surveybot")

# ── Variables GCP ───────────────────────────────────────────────────────────
GCP_PROJECT  = os.getenv("GCP_PROJECT")
GCP_REGION   = os.getenv("GCP_REGION", "europe-west1")
GCP_JOB_NAME = os.getenv("GCP_JOB_NAME")


# ============================================================================
# API publique
# ============================================================================

def is_task_running(account_id: str) -> bool:
    """Retourne True si une exécution est déjà active pour cet account_id."""

    if not IS_PROD:
        return False

    if RUN_ENV == "gcp":
        return _is_task_running_gcp(account_id)
    return _is_task_running_ecs(account_id)


def start_task(account_id: str):
    """
    Point d'entrée UNIQUE pour lancer un bot.
    - Local : dry-run (log uniquement)
    - Prod AWS : ecs.run_task()
    - Prod GCP : Cloud Run Jobs run_job()
    """
    print(f"[SCHEDULER] Demande lancement bot account_id={account_id}")

    if not IS_PROD:
        _start_task_local_dry_run(account_id)
    elif RUN_ENV == "gcp":
        _start_task_gcp(account_id)
    else:
        _start_task_ecs(account_id)


# ============================================================================
# Local dry-run
# ============================================================================

def _start_task_local_dry_run(account_id: str):
    print(
        f"[SCHEDULER][LOCAL][DRY-RUN] "
        f"would run task for account_id={account_id}"
    )


# ============================================================================
# AWS – chemins inchangés
# ============================================================================

def _is_task_running_ecs(account_id: str) -> bool:
    if not ECS_CLUSTER:
        raise RuntimeError("ECS_CLUSTER manquant")

    ecs = boto3.client("ecs")

    try:
        task_arns = []
        for status in ("RUNNING", "PENDING"):
            resp = ecs.list_tasks(
                cluster=ECS_CLUSTER,
                desiredStatus=status,
            )
            task_arns.extend(resp.get("taskArns", []))

        if not task_arns:
            return False

        desc = ecs.describe_tasks(
            cluster=ECS_CLUSTER,
            tasks=task_arns,
        )

        for task in desc.get("tasks", []):
            for container in task.get("containers", []):
                envs = container.get("environment", [])
                for env in envs:
                    if (
                        env.get("name") == "ACCOUNT_ID"
                        and env.get("value") == account_id
                    ):
                        print(
                            f"[SCHEDULER] Task déjà active pour {account_id} "
                            f"(status={task.get('lastStatus')})"
                        )
                        return True

        return False

    except Exception as e:
        # En cas d'erreur AWS, on joue safe : on considère la task comme active
        print(f"[SCHEDULER][WARN] is_task_running failed: {e}")
        return True


def _start_task_ecs(account_id: str):
    if not ECS_CLUSTER or not ECS_TASK_DEFINITION:
        raise RuntimeError(
            "ECS_CLUSTER ou ECS_SURVEYBOT_TASK_DEF manquant dans l'environnement"
        )

    ecs = boto3.client("ecs")

    print(
        f"[SCHEDULER][ECS] run_task cluster={ECS_CLUSTER} "
        f"taskDef={ECS_TASK_DEFINITION} account_id={account_id}"
    )

    account = load_account(account_id)

    container_overrides = {
        "name": ECS_CONTAINER_NAME,
        "environment": [
            {"name": "ACCOUNT_ID",   "value": account_id},
            {"name": "RUN_ENV",      "value": "aws"},

            {"name": "EMAIL",        "value": account["EMAIL"]},
            {"name": "PASSWORD",     "value": account["PASSWORD"]},

            {"name": "PROXY_URL",    "value": account["PROXY_URL"]},
            {"name": "PROXY_USER",   "value": account["PROXY_USER"]},
            {"name": "PROXY_PASS",   "value": account["PROXY_PASS"]},

            {"name": "GEO_LAT",      "value": str(account.get("GEO_LAT", ""))},
            {"name": "GEO_LON",      "value": str(account.get("GEO_LON", ""))},
            {"name": "SURVEY_LANG",  "value": account.get("SURVEY_LANG", "fr-FR")},
            {"name": "SURVEY_TZ",    "value": account.get("SURVEY_TZ", "Europe/Paris")},
        ]
    }

    response = ecs.run_task(
        cluster=ECS_CLUSTER,
        taskDefinition=ECS_TASK_DEFINITION,
        launchType="FARGATE",
        count=1,
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets":        os.getenv("ECS_SUBNETS", "").split(","),
                "securityGroups": os.getenv("ECS_SECURITY_GROUPS", "").split(","),
                "assignPublicIp": "ENABLED",
            }
        },
        overrides={"containerOverrides": [container_overrides]},
    )

    failures = response.get("failures")
    if failures:
        raise RuntimeError(f"ECS run_task failure: {failures}")

    tasks = response.get("tasks", [])
    if tasks:
        print(f"[SCHEDULER][ECS] task lancée: {tasks[0].get('taskArn')}")


# ============================================================================
# GCP – Cloud Run Jobs
# ============================================================================

def _gcp_job_parent() -> str:
    """Construit le nom de ressource du job Cloud Run."""
    if not GCP_PROJECT:
        raise RuntimeError("GCP_PROJECT manquant")
    if not GCP_JOB_NAME:
        raise RuntimeError("GCP_JOB_NAME manquant")
    return f"projects/{GCP_PROJECT}/locations/{GCP_REGION}/jobs/{GCP_JOB_NAME}"


def _is_task_running_gcp(account_id: str) -> bool:
    try:
        from google.cloud import run_v2
    except ImportError:
        raise RuntimeError(
            "google-cloud-run manquant — pip install google-cloud-run"
        )

    parent = _gcp_job_parent()
    client = run_v2.ExecutionsClient()

    try:
        for execution in client.list_executions(parent=parent):
            # completion_time est None tant que l'exécution n'est pas terminée
            if execution.completion_time:
                continue  # terminée → ignorer

            # Vérifier les overrides pour retrouver ACCOUNT_ID
            try:
                for co in execution.overrides.container_overrides:
                    for env_var in co.env:
                        if env_var.name == "ACCOUNT_ID" and env_var.value == account_id:
                            print(
                                f"[SCHEDULER] Execution GCP déjà active pour {account_id} "
                                f"(execution={execution.name})"
                            )
                            return True
            except AttributeError:
                continue

        return False

    except Exception as e:
        # En cas d'erreur GCP, on joue safe pour éviter les doublons
        print(f"[SCHEDULER][WARN] _is_task_running_gcp failed: {e}")
        return True


def _start_task_gcp(account_id: str):
    try:
        from google.cloud import run_v2
    except ImportError:
        raise RuntimeError(
            "google-cloud-run manquant — pip install google-cloud-run"
        )

    job_name = _gcp_job_parent()
    account  = load_account(account_id)

    env_overrides = [
        run_v2.EnvVar(name="ACCOUNT_ID",  value=account_id),
        run_v2.EnvVar(name="RUN_ENV",     value="gcp"),

        run_v2.EnvVar(name="EMAIL",       value=account["EMAIL"]),
        run_v2.EnvVar(name="PASSWORD",    value=account["PASSWORD"]),

        run_v2.EnvVar(name="PROXY_URL",   value=account["PROXY_URL"]),
        run_v2.EnvVar(name="PROXY_USER",  value=account["PROXY_USER"]),
        run_v2.EnvVar(name="PROXY_PASS",  value=account["PROXY_PASS"]),

        run_v2.EnvVar(name="GEO_LAT",     value=str(account.get("GEO_LAT", ""))),
        run_v2.EnvVar(name="GEO_LON",     value=str(account.get("GEO_LON", ""))),
        run_v2.EnvVar(name="SURVEY_LANG", value=account.get("SURVEY_LANG", "fr-FR")),
        run_v2.EnvVar(name="SURVEY_TZ",   value=account.get("SURVEY_TZ", "Europe/Paris")),
    ]

    overrides = run_v2.RunJobRequest.Overrides(
        container_overrides=[
            run_v2.RunJobRequest.Overrides.ContainerOverride(env=env_overrides)
        ]
    )

    print(
        f"[SCHEDULER][GCP] run_job job={job_name} account_id={account_id}"
    )

    client = run_v2.JobsClient()
    request = run_v2.RunJobRequest(name=job_name, overrides=overrides)

    # Déclenche l'exécution (LRO) sans bloquer
    operation = client.run_job(request=request)
    print(f"[SCHEDULER][GCP] exécution déclenchée: {operation.operation.name}")
