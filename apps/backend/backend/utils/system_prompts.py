"""System prompt loader for global + role-specific instructions."""

from __future__ import annotations

import os
from functools import lru_cache


_DEFAULT_PROMPT_DIR = os.path.join("backend", "config", "system_prompts")


def _prompt_dir() -> str:
    override = os.getenv("SYSTEM_PROMPT_DIR", "").strip()
    return override or _DEFAULT_PROMPT_DIR


def _read_prompt(filename: str) -> str:
    path = os.path.join(_prompt_dir(), filename)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except FileNotFoundError:
        return ""


@lru_cache(maxsize=1)
def load_system_prompts() -> dict[str, str]:
    return {
        "global": _read_prompt("global.md"),
        "researcher": _read_prompt("researcher.md"),
        "guardian": _read_prompt("guardian.md"),
        "researcher_reminder": _read_prompt("researcher_reminder.md"),
        "guardian_reminder": _read_prompt("guardian_reminder.md"),
    }


def build_system_prompt(
    base_prompt: str,
    role: str,
    *,
    include_role: bool = True,
    include_global: bool = True,
) -> str:
    prompts = load_system_prompts()
    sections = []
    if include_global:
        global_prompt = prompts.get("global", "").strip()
        if global_prompt:
            sections.append(global_prompt)
    if include_role:
        role_prompt = prompts.get(role, "").strip()
        if role_prompt:
            sections.append(role_prompt)
    sections.append(base_prompt)
    return "\n\n".join(section for section in sections if section)


def prepend_system_prompts(base_prompt: str, role: str) -> str:
    return build_system_prompt(base_prompt, role)
