# Git workflow

An empty bootstrap commit establishes `main` as a PR base. Implementation lives on
`chore/ayush-foundation-skeleton`; no implementation is committed directly to main.

Branch convention: `<type>/<owner>-<task>`, for example
`feat/ayush-ledger`, `feat/daksh-claim-extraction`, `fix/ayush-sse-replay`.
Commit convention: `<type>(<scope>): <imperative description>`.
Each commit contains one cohesive concern, usually a few files.

Once a GitHub destination is supplied, publish the empty main baseline and push
the foundation branch. Open a PR from the branch to main, run CI, review, then
merge. Pushing the feature branch must happen before a remote PR can be opened.
Do not merge or push main implementation as part of this local skeleton task.

After foundation merge, both developers start branches from updated main. Rebase
at task boundaries; don't force-push another developer's branch.

Configure your Git author identity before future commits if the local machine's
default identity is not the account you want attached to your commits.
