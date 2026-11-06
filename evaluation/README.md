# Evaluation: is the model worth its time?

The model takes most of the pipeline's time. These scripts test whether it earns it, by
comparing settings against answer keys checked by a person. Nothing here changes the
project's code, and nothing goes to `output/`, so a test can't affect a real merge.

## The two tests

| Test | Question | Dataset | Compared |
|---|---|---|---|
| Detection | Which names on a fund's page are real companies? | `datasets/detection/<fund>/` | The parser alone, the parser plus the model, the model alone |
| Matching | Are two records the same company? | `datasets/matching/pairs.csv` | The scoring rules alone, the rules plus the judge |

## Steps

### 1. Correct the answer keys (the part only a person can do)

The drafts come from the system under test, so they can't count as the truth until checked.

**Detection:** in each `datasets/detection/<fund>/gold.csv`, compare the rows with
`pages/portfolio.html`:
- set `keep` to `yes` or `no` (`?` rows count neither way). A company counts by its name or its
  website, so you can write the real name (`Jeeves`) where a run named it from its domain (`Tryjeeves`);
- add any company the draft missed, with `keep` = `yes`: a missing company can't be counted as missed;
- then set `"reviewed": true` in that fund's `meta.json`.

**Matching:** in `datasets/matching/pairs.csv`, set `label` to `match` or `no_match`. Leave
it empty to skip a pair. `suggested` is the rules' answer, there only to save reading time:
copying it blindly would make the rules look perfect.

Graphs say DRAFT LABELS until every detection fund is reviewed.

### 2. Choose what to compare

`settings.json` lists the settings. Add as many as you like:

| Field | Meaning | Default |
|---|---|---|
| `name` | The name on the graphs | required |
| `mode` | Detection: `deterministic`, `hybrid` or `agent` | required |
| `use_model` | Matching: ask the model about unsure pairs | `false` |
| `model` | The model's name on the server | `VLLM_MODEL` in `.env` |
| `base_url` | The server's address | `VLLM_BASE_URL` in `.env` |
| `temperature` | Model temperature | `.env` (matching: the judge's own) |
| `max_tokens` | Longest reply | `LLM_MAX_TOKENS` in `.env` |
| `max_turns` | Detection: turns the agent gets | 25 |
| `tool_choice` | Detection: `required` or empty | `LLM_TOOL_CHOICE` in `.env` |
| `extra_body` | Anything else the server accepts, e.g. `{"reasoning_effort": "low"}` | none |

To test another model, add a setting with its `model`. A vLLM server usually serves one model,
so either give each model its own server and `base_url`, or start that model and run only its settings:

```bash
python evaluation/run_detection.py --setting "my new model"
```

A setting whose model the server doesn't offer is skipped with a message. Check that a
setting really takes effect: if `reasoning_effort` does nothing, the server is ignoring it,
and the token counts in the summary will stay the same.

### 3. Run

```bash
python evaluation/run_detection.py                      # all settings; a stopped run continues
python evaluation/run_matching.py
python evaluation/run_detection.py --repeats 3          # steadier times
python evaluation/run_detection.py --setting "parser only" --fresh   # --fresh forgets ALL earlier detection results
```

A test that failed, such as a server error on one fund, runs again the next time you use the same
command, and only its last try counts. If it still failed, it scores as finding nothing, since
leaving it out would flatter that setting. Correcting labels after a run needs no new run: `plot_results.py` scores against the answer keys
as they are now. Only a pair that had no label when the run happened has to be run.

### 4. Graph

```bash
pip install -r evaluation/requirements.txt
python evaluation/plot_results.py
```

In `evaluation/results/`:

| File | Shows |
|---|---|
| `detection_quality.png` | Precision, recall and F1 per setting |
| `detection_time.png` | Time and model calls per fund |
| `detection_tradeoff.png` | Quality against time: look for the point at the top left |
| `detection_by_fund.png` | F1 per fund and setting: which funds need the model |
| `matching_quality.png` | Precision, recall, and what's left for a person |
| `matching_time.png` | Time per pair and wrong merges |
| `matching_tradeoff.png` | Pairs answered correctly against time |
| `*_summary.csv` | The same numbers, plus token counts |

## Reading the scores

- **Precision:** of what it found, how much is real. Low means junk like `Lebanon`.
- **Recall:** of what's real, how much it found. Low means missed companies.
- **F1:** both in one number. A high F1 can hide a low precision, so read all three.
- **Wrong merges:** two different companies joined. The costliest matching mistake.
- **Left for a person:** pairs with no decision. A safe answer, but it saves no work.

Decide what counts as worth it **before** looking at the results, for example "the model must
add at least 5 points of recall or precision". Otherwise any small gain can justify the hours.

## What the results can't show

- **Times are relative.** Saved pages load instantly and each fund's list is kept short, so
  use them to compare settings, not to predict a full crawl.
- **Only the saved copy is tested.** The answer key covers one page, so reading many pages of
  a real site isn't tested here; `tests/test_prepass_browser.py` covers pagination.
- **Enrichment isn't tested.** It takes the most time, and would need a dataset of its own.
- **Fewer funds than the whole list.** A few funds decide a lot: check `detection_by_fund.png`
  before believing an average.
