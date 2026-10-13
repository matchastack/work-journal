"""The evaluation harness: LLM quality measured on a fixed dataset (T-016, requirements §10).

`dataset.py` loads the cases, `run.py` runs them against the real models and scores them, and
`report.py` writes the report that `wj eval` prints.
"""
