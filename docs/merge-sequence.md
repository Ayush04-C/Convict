# Integration and merge sequence

Ayush and Daksh can merge independent work between each other's PRs. Neither
person needs to finish everything first. Respect file ownership, shared contracts,
and each stacked PR's base branch. A clean merge still needs passing combined tests.

The inspected open PRs are:

| PR | Work | Dependency |
|---|---|---|
| [#2](https://github.com/Ayush04-C/Convict/pull/2) | PDF precheck | Foundation |
| [#3](https://github.com/Ayush04-C/Convict/pull/3) | Claim extraction | Foundation; provider settings/dependencies help full CI coverage |
| [#4](https://github.com/Ayush04-C/Convict/pull/4) | Funding analyst | Foundation |
| [#5](https://github.com/Ayush04-C/Convict/pull/5) | Scale/team analyst | #4; currently based on its branch |
| [#8](https://github.com/Ayush04-C/Convict/pull/8) | Extraction fallback | #3; currently based on its branch |

Recommended sequence: provider PR, #2, #3, #8, #4, #5. The independent PDF and
analyst chains may move earlier. After #3 or #4 merges, check the child PR's base
and diff before merging #8 or #5. Retarget to main if necessary, especially after
squash merges. Re-run CI against the updated main; resolve changes in your own
files and coordinate shared contract changes with the owner.

No Daksh-owned source files are changed by the provider branch. Its default
dependencies include Google GenAI and PyMuPDF so ingestion tests do not silently
skip for missing optional packages. The offline demo explicitly uses stubs even
after concrete ingestion/agent implementations merge.

Next Ayush stage: company resolution and private context construction, followed
by sandbox/pipeline integration, run APIs, and scoring/report integration. Merge
Daksh implementations behind their agreed interfaces as they become ready.
Chat/interface work and submission preparation remain deferred.

## Compatibility check on 2026-10-31

Virtual merges of all five inspected feature heads into the provider branch were
clean. A temporary export of that combined tree passed **659 tests**, Ruff and
all three offline demos, with no skipped tests. The actual branches, index and
GitHub PRs were not merged or modified by this check. It covers the inspected
heads only; new commits still require CI. Passing these checks does not complete
the live agent pipeline or replace review of each PR's behavior.
