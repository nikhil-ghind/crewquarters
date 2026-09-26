# pr-reviewer

Reviews the latest open pull requests of one GitHub repository for **likely bugs** and **code-style
problems**, and (when you turn it on) posts them as one inline-comment review per pull request.

- **Dry run by default.** `postComments: false` reports findings in the run result and posts
  nothing. Set it to `true` to post.
- **Comments only.** The broker always posts a `COMMENT` review, so the agent can never approve,
  request changes or merge. It also cannot touch any repository other than the configured `repo`.
- **Reads what changed.** Each changed file's diff is numbered by its new-file line. Lock files,
  images, removed files and files GitHub gives no patch for are skipped. Big diffs are cut at
  `maxDiffCharsPerFile`, and files are sent to the model in batches of `maxCharsPerBatch`.
- **The model proposes, code decides.** A finding is kept only if it points at a line the diff
  shows. Duplicates on one line collapse to the most important, bugs rank before style, and at most
  `maxCommentsPerPr` are posted. Dropped findings are counted in the result.
- **No double reviews.** Each review carries a hidden marker with the commit it reviewed. A later
  run skips a pull request whose latest commit already has one. Push a new commit to get a new
  review. A review whose delivery is unconfirmed is never retried.
- **Untrusted input.** Diffs and titles go to the model only inside random-boundary evidence
  blocks, and the model has no tools. Text the model writes is stripped of `@` pings, HTML comments
  and one-click ` ```suggestion ` blocks before it is posted.
- **Drafts** are skipped unless `skipDrafts` is `false`.

## Try it (no GitHub needed)

```bash
crewctl test agents/pr_reviewer        # three fake pull requests, mock model, dry run
```

## Use it on a real repository

1. Give the capability broker a token and run in live mode. The easiest token is your own:
   `CQ_GITHUB_TOKEN=$(gh auth token)`. A fine-grained token limited to the one repository, with
   Pull requests: read and write, is safer. See [docs/runbooks/local-demo.md](../../docs/runbooks/local-demo.md).
2. Install **PR Reviewer**, set `repo` (for example `acme/shop`), and run it. Read the findings.
3. Set `postComments` to `true` and run again to post them.

Configuration is in `manifest.yaml`; the result renderer is `crewquarters.pr-review/v1`.
