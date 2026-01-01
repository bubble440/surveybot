# scheduler/ecs.py

import os
import time
from typing import Optional

# Détection environnement
IS_PROD = bool(
    os.getenv("AWS_EXECUTION_ENV")
    or os.getenv("ECS_CONTAINER_METADATA_URI")
    or os.getenv("ECS_CONTAINER_METADATA_URI_V4")
    or os.getenv("RUN_ENV") == "aws"
)

# ⚠️ Ces variables doivent exister EN PROD
ECS_CLUSTER = os.getenv("ECS_CLUSTER")
ECS_TASK_DEFINITION = os.getenv("ECS_SURVEYBOT_TASK_DEF")
ECS_CONTAINER_NAME = os.getenv("ECS_SURVEYBOT_CONTAINER", "surveybot")


def is_task_running(account_id: str) -> bool:
    """
    En local : toujours False (dry-run)
    En prod : sera implémenté via ECS list_tasks / describe_tasks
    """
    return False

def _start_task_local_dry_run(account_id: str):
    print(
        f"[SCHEDULER][LOCAL][DRY-RUN] "
        f"would run ECS task for account_id={account_id}"
    )

def start_task(account_id: str):
    """
    Point d’entrée UNIQUE pour lancer un bot.
    - Local : dry-run (log uniquement)
    - Prod : ecs.run_task()
    """
    print(f"[SCHEDULER] Demande lancement bot account_id={account_id}")

    if not IS_PROD:
        _start_task_local_dry_run(account_id)
    else:
        _start_task_ecs(account_id)


# -------------------------
# PROD ECS
# -------------------------

def _start_task_ecs(account_id: str):
    """
    Lance une task ECS surveybot.
    """

    import boto3

    if not ECS_CLUSTER or not ECS_TASK_DEFINITION:
        raise RuntimeError(
            "ECS_CLUSTER ou ECS_SURVEYBOT_TASK_DEF manquant dans l'environnement"
        )

    ecs = boto3.client("ecs")

    print(
        f"[SCHEDULER][ECS] run_task cluster={ECS_CLUSTER} "
        f"taskDef={ECS_TASK_DEFINITION} account_id={account_id}"
    )

    response = ecs.run_task(
        cluster=ECS_CLUSTER,
        taskDefinition=ECS_TASK_DEFINITION,
        launchType="FARGATE",
        count=1,
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": os.getenv("ECS_SUBNETS", "").split(","),
                "securityGroups": os.getenv("ECS_SECURITY_GROUPS", "").split(","),
                "assignPublicIp": "ENABLED",
            }
        },
        overrides={
            "containerOverrides": [
                {
                    "name": ECS_CONTAINER_NAME,
                    "environment": [
                        {"name": "ACCOUNT_ID", "value": account_id},
                        {"name": "RUN_ENV", "value": "aws"},
                    ],
                }
            ]
        },
    )

    failures = response.get("failures")
    if failures:
        raise RuntimeError(f"ECS run_task failure: {failures}")

    tasks = response.get("tasks", [])
    if tasks:
        print(f"[SCHEDULER][ECS] task lancée: {tasks[0].get('taskArn')}")
