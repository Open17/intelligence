"""Which model does what.

    Haiku 5.5  (medium) the voice: conversation, simple questions, routing
    Sonnet 5.5 (high)   questions needing care, and actions (computer/connectors)
    Opus 5.5   (medium) hard questions and complex multi-step jobs
    Opus 5.5   (high)   anything that builds software: apps, scripts, sites...

Haiku chooses through its tools, but the rule "building always uses Opus at
high effort" is too important to leave to a small model's judgment, so
``classify_task`` also upgrades anything that looks like a build request.
"""

from __future__ import annotations

import re

from ..config import ModelChoice, Settings

QUESTION_DIFFICULTIES = ("medium", "hard")
TASK_KINDS = ("action", "complex", "build")

_BUILD = re.compile(
    r"\b(build|make|create|write|code|develop|program|implement|scaffold|set ?up|generate)\b"
    r".{0,60}?\b(apps?|applications?|scripts?|programs?|websites?|sites?|web ?pages?|tools?|bots?|games?|"
    r"apis?|services?|extensions?|plugins?|clis?|dashboards?|servers?|libraries|library|modules?|"
    r"functions?|projects?|code|software|automations?|cron ?jobs?)\b",
    re.IGNORECASE | re.DOTALL,
)


def looks_like_build(request: str) -> bool:
    return bool(_BUILD.search(request))


def classify_task(kind: str, request: str) -> str:
    """Normalizes Haiku's chosen kind; anything that builds software becomes 'build'."""
    kind = kind if kind in TASK_KINDS else "action"
    return "build" if looks_like_build(request) else kind


def model_for_task(settings: Settings, kind: str) -> ModelChoice:
    return {"action": settings.action, "complex": settings.hard, "build": settings.build}[kind]


def model_for_question(settings: Settings, difficulty: str) -> ModelChoice:
    return settings.hard if difficulty == "hard" else settings.action


def short_name(model: str) -> str:
    """'claude-opus-5-5' -> 'Opus'."""
    for name in ("opus", "sonnet", "haiku", "fable", "mythos"):
        if name in model:
            return name.capitalize()
    return model
