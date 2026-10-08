"""Priority check: which application task runs above which BSW task, and what that costs.

The choice stays free (user decision 2026-09-15: free number and priority of the application tasks). This module
never refuses anything, it explains what an assignment costs. Only rtegen forbids one case with an error:
an application task above a task marked above_application (the watchdog task).

The role of a task (safety, communication, ...) is configuration, not knowledge of the tool: roles.yaml holds the
defaults and --roles replaces them (see scenario.load_roles).
"""

from math import ceil

from .findings import INFO, WARNING, Finding
from .model import ROLE_COMMUNICATION, ROLE_SAFETY

CRITICAL_ROLES = (ROLE_SAFETY, ROLE_COMMUNICATION)

REASON = {
    ROLE_SAFETY: "a safety task (watchdog, supervision) that must run on time even when the application is late",
    ROLE_COMMUNICATION: "a communication task whose timing a peer expects (CAN cycle times, UDS timeouts)",
}


def analyse(model, response=None) -> dict:
    """Compares every application task with the BSW tasks below it."""
    entries, findings = [], []
    responses = {e["task"]: e for e in (response or {}).get("tasks", [])}
    order = [{"task": t.name, "kind": t.kind, "priority": t.priority, "context": t.os_priority,
              "period_ms": t.period_ms, "roles": list(t.roles), "above_application": t.above_application,
              "where": t.where}
             for t in sorted(model.tasks, key=lambda t: t.os_priority)]

    for app in model.application_tasks():
        for bsw in model.tasks:
            if bsw.kind == app.kind or bsw.os_priority >= app.os_priority:
                continue
            critical = [r for r in bsw.roles if r in CRITICAL_ROLES]
            if not critical:
                continue
            cost = app.worst_cycle_us()
            bsw_response = responses.get(bsw.name, {})
            delay = None
            if cost is not None and app.period_ms and bsw_response.get("response_us"):
                jobs = ceil(bsw_response["response_us"] / (float(app.period_ms) * 1000.0))
                delay = jobs * cost
            entry = {"application_task": app.name, "bsw_task": bsw.name, "roles": critical,
                     "application_priority": app.priority, "bsw_priority": bsw.priority,
                     "application_context": app.os_priority, "bsw_context": bsw.os_priority,
                     "delay_us": delay, "bsw_response_us": bsw_response.get("response_us"),
                     "bsw_deadline_ms": bsw.deadline, "where": app.where}
            entries.append(entry)
            reason = " and ".join(REASON[r] for r in critical)
            extra = ""
            if delay is not None:
                extra = (f" It delays {bsw.name} by up to {delay:.0f} us per response "
                         f"(response time {bsw_response['response_us']:.0f} us).")
            findings.append(Finding(
                WARNING, "application_above_critical_bsw",
                f"application task {app.name} (key {app.priority}, context {app.os_priority}) runs above the BSW "
                f"task {bsw.name} (key {bsw.priority}, context {bsw.os_priority}), which has the role "
                f"{'/'.join(critical)}: {reason}.{extra} The choice stays yours - if the application really is "
                f"the more urgent one, keep it and give {bsw.name} the reserve it needs",
                app.name, app.where, entry))

    # rate monotonic remark: a shorter period should normally get the higher priority
    for task in model.tasks:
        if not task.period_ms:
            continue
        for other in model.tasks:
            if not other.period_ms or other.os_priority <= task.os_priority or other.above_application:
                continue  # above_application is a decision of the configuration, not a priority mistake
            if other.period_ms > task.period_ms and (task.is_application or other.is_application):
                findings.append(Finding(
                    INFO, "not_rate_monotonic",
                    f"task {other.name} ({other.period_ms} ms) runs above task {task.name} "
                    f"({task.period_ms} ms): with fixed priorities the shorter period is usually the higher "
                    f"priority (rate monotonic). Only a real urgency should turn that around",
                    other.name, other.where,
                    {"faster": task.name, "faster_period_ms": task.period_ms,
                     "slower": other.name, "slower_period_ms": other.period_ms}))

    used: dict = {}
    for task in model.tasks:
        other = used.get(task.priority)
        if other is not None:
            findings.append(Finding(
                WARNING, "priority_used_twice",
                f"tasks {other} and {task.name} use the ordering key {task.priority}; OpenBSW gives every task "
                f"its own FreeRTOS priority, so rtegen refuses this configuration",
                task.name, task.where))
        used[task.priority] = task.name

    for app in model.application_tasks():
        for bsw in model.tasks:
            if bsw.above_application and app.os_priority > bsw.os_priority:
                findings.append(Finding(
                    WARNING, "above_application_violated",
                    f"application task {app.name} runs above {bsw.name}, which is marked above_application: "
                    f"rtegen refuses this configuration",
                    app.name, app.where))

    return {"order": order, "above_critical": entries, "roles_source": model.roles_source,
            "findings": findings}
