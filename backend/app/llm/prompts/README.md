# Prompts

One folder per prompt and one subfolder per version:

```
prompts/
  fact_extraction/
    v1/
      system.md   stable instructions, cached across calls
      user.md     the request, with $placeholders for the variables
```

Code loads a named version (`load_prompt("fact_extraction", 1)`), and every call logs the version
it used. Never edit a released version. Add the next one, run `wj eval` (`backend/evals/README.md`),
and give the numbers in the PR next to the baseline (see `CLAUDE.md`).
