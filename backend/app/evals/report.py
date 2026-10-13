"""The report `wj eval` prints: the evaluation bar's numbers for each suite, then each case.

It holds case IDs, counts, pattern numbers and the verifier's codes, never text from a note, a
fact or an output. So a report on the fictional dataset can be committed as the baseline, and a
report on private cases shows nothing private but the case IDs the owner chose.
"""

import math
from collections.abc import Sequence
from datetime import date

from app.evals.run import BAR_RECALL, CaseResult, Evaluation, SuiteResult, cost_usd
from app.llm.usage import CallRecord

BAR = (
    "The bar for changing a prompt or the model routing (requirements section 10): 0 altered "
    f"numbers, 0 inflated claims and at least {BAR_RECALL:.0%} of facts recovered, with every case "
    "run."
)


def evaluation_report(evaluation: Evaluation, *, dataset: str, on: date) -> str:
    """The report as Markdown. `dataset` says which cases ran, such as the fictional ones."""
    lines = ["# Evaluation report", "", f"{dataset}, run on {on.isoformat()}.", ""]
    lines += _setup(evaluation.calls)
    lines += [_verdict(evaluation), "", BAR, ""]
    lines += [
        "| Suite | Cases passed | Altered numbers | Inflated claims | Fact recall | Calls | "
        "Cost (USD) |",
        "|---|---|---|---|---|---|---|",
    ]
    lines += [_suite_row(suite.suite, [suite]) for suite in evaluation.suites]
    if len(evaluation.suites) > 1:
        lines.append(_suite_row("**Total**", evaluation.suites))
    lines += ["", _cost_note(evaluation.calls)]
    for suite in evaluation.suites:
        lines += ["", f"## {suite.suite}", "", "| Case | Result | Notes |", "|---|---|---|"]
        lines += [_case_row(case) for case in suite.cases]
    return "\n".join(lines) + "\n"


def _setup(calls: Sequence[CallRecord]) -> list[str]:
    """The prompt versions and models the calls used."""
    if not calls:
        return []
    prompts = sorted({call.prompt for call in calls})
    tiers = sorted({call.tier for call in calls})
    models = [
        f"{tier} `{'`, `'.join(sorted({call.model for call in calls if call.tier == tier}))}`"
        for tier in tiers
    ]
    lines = [f"- Prompts: {', '.join(prompts)}", f"- Models: {'; '.join(models)}"]
    if fell_back := sum(call.fell_back for call in calls):
        lines.append(f"- {_count(fell_back, 'call')} answered by the API's fallback model")
    return [*lines, ""]


def _verdict(evaluation: Evaluation) -> str:
    if evaluation.meets_bar:
        return "**Meets the bar.**"
    misses: list[str] = []
    altered = sum(suite.altered_numbers for suite in evaluation.suites)
    inflated = sum(suite.inflated_claims for suite in evaluation.suites)
    errors = sum(suite.errors for suite in evaluation.suites)
    if altered:
        misses.append(_count(altered, "altered number"))
    if inflated:
        misses.append(_count(inflated, "inflated claim"))
    recall = evaluation.recall
    if recall is not None and recall < BAR_RECALL:
        misses.append(f"fact recall {_percent(recall)}")
    if errors:
        misses.append(f"{_count(errors, 'case')} couldn't run")
    return f"**Doesn't meet the bar:** {'; '.join(misses)}."


def _suite_row(name: str, suites: Sequence[SuiteResult]) -> str:
    cases = [case for suite in suites for case in suite.cases]
    calls = [call for suite in suites for call in suite.calls]
    passed = sum(case.passed for case in cases)
    expected = sum(case.expected_facts for case in cases)
    recovered = sum(case.recovered_facts for case in cases)
    recall = f"{recovered}/{expected} ({_percent(recovered / expected)})" if expected else "-"
    cost = cost_usd(calls)
    cells = [
        name,
        f"{passed}/{len(cases)}",
        str(sum(case.altered_numbers for case in cases)),
        str(sum(case.inflated_claims for case in cases)),
        recall,
        str(len(calls)),
        "-" if cost is None else f"{cost:.4f}",
    ]
    return f"| {' | '.join(cells)} |"


def _cost_note(calls: Sequence[CallRecord]) -> str:
    if calls and cost_usd(calls) is None:
        return "Set `LLM_PRICE_*` for each tier to estimate the cost."
    return "Cost is estimated at the tier prices in `LLM_PRICE_*`."


def _case_row(case: CaseResult) -> str:
    if case.error is not None:
        result, notes = "error", case.error
    else:
        result, notes = ("pass" if case.passed else "**fail**"), "; ".join(case.notes)
    return f"| {case.case_id} | {result} | {_cell(notes)} |"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _percent(share: float) -> str:
    """Rounded down to one decimal, so a share just under the bar never shows as on it."""
    return f"{math.floor(share * 1000) / 10:g}%"


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"
