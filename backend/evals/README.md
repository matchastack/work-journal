# Evaluations

`wj eval` measures the LLM steps on a fixed set of cases, so a change to a prompt or to the model
routing is judged by numbers (requirements section 10). Run it before such a change, and give the
numbers in the PR next to the baseline.

```sh
uv run wj eval all                        # the fictional cases in this folder
uv run wj eval extraction --private       # your own cases in local/evals/
uv run wj eval all --out evals/baseline.md
```

It needs `ANTHROPIC_API_KEY` and `LLM_MODEL_STANDARD`, and `LLM_PRICE_STANDARD` for the cost. It
exits with status 1 when the results miss the bar: **0 altered numbers, 0 inflated claims and at
least 90% of facts recovered**, with every case run. Each call is also logged to `LLM_CALL_LOG`.

## Suites

| Suite | Cases | What it runs |
|---|---|---|
| `extraction` | journal notes and the facts each one gives | the fact-extraction prompt, then the code checks on each fact |
| `writing` | facts and what a bullet written from them must and mustn't say | the resume writer, with the verifier and its one rewrite |
| `adversarial` | known-bad text the verifier must catch, and faithful text it must pass | the verifier |
| `repo` | items of a repository's history and the facts they give | the repository-extraction prompt, then the code checks on each fact |

Notes and facts that try to trick a step, such as instructions inside a note, are cases in that
step's suite, with `never` patterns.

## What is counted

- **Altered numbers.** Extraction: each number the model wrote that the note doesn't give. Code
  drops those facts, but the evaluation scores the prompt, so they count. Writing: each number
  error the verifier still finds in the final bullet. Adversarial: known-bad text with a wrong
  number that the verifier doesn't catch.
- **Inflated claims.** Extraction: each fact with a tool the note doesn't name, more ownership
  than the expected fact allows, or a match for a `never` pattern. Writing: each unsupported claim
  the verifier still finds in the final bullet, and each `never` match. Adversarial: other
  known-bad text the verifier doesn't catch.
- **Fact recall.** The expected facts that a fact the checks kept matches, one to one.
- **Repository history** (`repo`) counts the same way, against the items each fact cites rather
  than a note: a number they don't give is altered. A fact is inflated when it cites nothing or an
  item it wasn't given, names a tool its items don't name, claims more ownership than expected,
  or matches a `never` pattern. It recovers an expected fact only if it cites one of that fact's
  `items`.
- **Cost.** The estimated cost of the run's calls, at the `LLM_PRICE_*` tier prices.

The report holds case IDs, counts, pattern numbers and the verifier's codes. It never quotes a
note, a fact or an output, so for private cases it shows only the IDs you chose.

## Files

One TOML file per suite. Paths are relative to the file. Patterns are regular expressions,
searched for anywhere in the text and ignoring case; write them in single quotes, so a backslash
stays a backslash.

### `extraction.toml`

```toml
profile = "../tests/fixtures/profile.json"   # what facts link to
written = 2026-09-14                         # the day the notes were written

[[cases]]
id = "a modest part"                         # its name in the report
note = "helped sam move order processing onto rabbitmq this sprint"
written = 2026-09-10                         # optional: this note's day
role = "northwind"                           # optional: the role the owner chose for the entry
never = ['\bled\b']                          # optional: no fact may match these

[[cases.expected]]                           # one table per fact the note gives; none for a note
terms = ["order", "rabbitmq|queue"]          #   with no work in it. Every part is optional:
numbers = [50, 12]                           #   patterns its statement, outcome or tools match,
role = "northwind"                           #   numbers its metrics hold, the role or project
project = "recipe_box"                       #   it links to, and the most ownership the note
ownership = "contributed"                    #   gives (assisted, contributed, owned, led)
```

A fact recovers an expected fact when it matches every part given. Facts the model adds beyond the
expected ones are noted, and fail the case only when the note gives none.

### `writing.toml`

```toml
profile = "../tests/fixtures/profile.json"
fact_files = ["../tests/fixtures/facts.json"]   # JSON lists of facts, as `wj extract` prints

[[facts]]                                       # more facts, written here
id = "fact_nw_alerts"
statement = "Added an alert that pages the on-call engineer when the nightly export fails."
ownership = "owned"
roleId = "northwind"

[[cases]]
id = "an edit adds a new fact"
facts = ["fact_nw_export", "fact_nw_alerts"]    # the facts to write from
edit = "nw_export"                              # optional: write a new version of this bullet
under = "northwind"                             # optional: default, the edited bullet's or the facts' item
avoid = ["leveraged"]                           # optional: the words-to-avoid list
keeps = ['\b50\b', '\b12\b']                    # optional: the bullet must match these
never = ['^Led\b']                              # optional: the bullet mustn't match these
```

A case passes when the bullet passes the verifier, matches every `keeps` and no `never` pattern.

### `adversarial.toml`

The same `profile`, `fact_files` and `facts` as `writing.toml`, then:

```toml
[[cases]]
id = "inflated ownership"
text = "Led the move of order processing from a cron job to a message queue."
facts = ["fact_nw_queue"]                  # the facts the text cites
catch = ["inflated_ownership"]             # known-bad: one of these verifier codes must fire
under = "northwind"                        # optional: default, the facts' item
kind = "resume_bullet"                     # optional: or linkedin_headline, linkedin_about, linkedin_position
posting_terms = ["distributed systems"]    # optional: the posting the text is tailored to
```

Without `catch`, the text is faithful and must pass. The codes are the verifier's: the claim
check's (`inflated_ownership`, `added_tool`, `added_team_size`, `added_outcome`, `added_scope`,
`honesty_boundary`, `skill_gap`, `posting_term`, `unsupported_claim`, `contradicted_claim`), the
number check's (`unsupported_number`, `unit_changed`, `qualifier_stronger`), the style check's,
and the resume rules' (such as `R3` for a skill on the gaps list).

### `repo.toml`

```toml
profile = "../tests/fixtures/profile.json"

[[cases]]
id = "a benchmark in a pull request"
project = "recipe_box"                # the project the facts belong to, or new_project = "Name"
repository = "casey-example/example"  # optional
owned = true                          # optional: false for someone else's repository
never = ['\bled\b']                   # optional: no fact may match these

[[cases.items]]                       # what the digest shows, one table per item (at most 30)
label = "pr1"                         # about, pr1, pr2... or c1, c2...
kind = "pull"                         # pull, commit or about
month = "2023-06"
title = "Cache parsed recipes"
body = "Cached results cut page loads from 1200 ms to 300 ms."
maker = "owner"                       # optional: or agent, with agent = "Claude Code"
coauthored = false                    # optional: another person had a part in it

[[cases.expected]]                    # one table per fact the items give; none for routine work
terms = ["cach"]                      #   every part is optional: patterns, metric numbers,
numbers = [1200, 300]                 #   the items it must cite at least one of, and the most
items = ["pr1"]                       #   ownership the items give
ownership = "owned"
```

The items are cleaned as the digest cleans them before the model reads them, so a case can check
that a reference such as `#142` never backs a number.

## Your own cases

Put them in `local/evals/`, which git ignores, in files with the same names, such as
`local/evals/writing.toml` with `profile = "../profile.json"` and
`fact_files = ["../facts.json"]`. Run them with `--private`. Never commit them, and don't commit a
report of them either: give their numbers in a PR, not their case IDs.
