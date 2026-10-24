# baquiano's TODO list

### Todo

- [ ] Entity resolution step 4: an LLM judge for the pairs left in review, with quoted evidence
- [ ] Label 100 to 200 review pairs and fit the resolution thresholds on them
- [ ] Undo command for merged actors
- [ ] Relationships from portfolios and Lens (co-patenting, spin-offs)
- [ ] Search backend (self-hosted SearXNG) and an investigator agent for hard cases
- [ ] Load enrichment results (`tech_area`, country, year) and patents into the database
- [ ] Measure enrichment quality on 10 to 30 hand-labelled startups before the full run
- [ ] Rerun the agent-mode funds so their outputs drop the old guessed sectors
- [ ] Report sister sites that return nothing (The Yield Lab Asia gave 68 instead of 123)
- [ ] Lens patents: the script was removed and the API token is refused
- [ ] Clean up `targets.txt`

### Done

- [x] Manage startups sectors: `sector` keeps only the fund's own label (dropped if no page shows it); `enrichment.tech_area` from `taxonomy.json` is the classification
- [x] Entity resolution v1: candidates, scoring, merges, review CSV export/apply (`scripts/resolve_entities.py`)
- [x] Database loader: portfolio outputs to mentions, actors and investments (`scripts/load_db.py`)
