# Pull request timeline, story links and grids

This guide covers three connected features:

- the **Pull request timeline** grid on Team Overview: every PR with its phases, linked stories and flags, including PRs that have **no story**;
- how GitHub pull requests, commits and Azure Pipelines runs are **linked to Azure Boards work items**, including the **Stories** column of the Pipeline runs grid;
- **sorting and filtering** in every grid of the app.

For the per-story view, see [story-lifecycle.md](story-lifecycle.md).

**Contents**

1. [Pull request timeline (Team Overview)](#1-pull-request-timeline-team-overview)
2. [How work items are linked](#2-how-work-items-are-linked)
3. [Stories behind pipeline runs](#3-stories-behind-pipeline-runs)
4. [Sorting and filtering grids](#4-sorting-and-filtering-grids)
5. [Setup and data refresh](#5-setup-and-data-refresh)
6. [API and CSV](#6-api-and-csv)
7. [Troubleshooting](#7-troubleshooting)

---

## 1) Pull request timeline (Team Overview)

At the bottom of **Team Overview**. It follows the page's date range, team and target-branch filters, and lists every PR **created or merged** in the range.

### Summary

| Tile | Meaning |
|---|---|
| **Pull requests** | PRs in the range, split into merged / open / closed without merging |
| **Without a story** | PRs with no linked story, with their share. PRs that mention an `AB#` work item that isn't synced are counted separately |
| **Merged without review** | Merged PRs nobody other than the author reviewed |
| **Median cycle** | First commit → merge (and → production, when deployments are synced) |

Below the tiles, a bar chart shows the **median time per phase** for merged PRs.

### Phases

Each PR's timeline bar is split into phases. A phase runs from its milestone to the next milestone the PR has reached. For an open PR, the current phase runs until now and is drawn faded.

| Phase | From → to |
|---|---|
| **Coding** | First commit → PR opened. Skipped when every commit was pushed after opening |
| **Waiting for review** | Opened → first review by someone other than the author (any review state, or the first feedback comment) |
| **In review** | First review → first approval, or the merge if the PR was never approved |
| **Waiting to merge** | First approval → merge |
| **Waiting to deploy** | Merge → the successful production deployment that shipped it. Shown only when Azure Pipelines deployments are synced |

**The deployment that shipped a PR** is the successful production deployment whose commit is the PR's merge commit. Otherwise it's the first successful production deployment of the same repository after the merge. This is the same rule as the DORA lead time.

### Columns

PR (repository, number, title, branches), Author, Status, **Stories**, Timeline, Cycle, Merged, Lines, Reviewers, Flags.

Click a PR to see:
- its phases with durations;
- its milestones: first commit, opened, first review, approved, merged, deployed, and the deployment build;
- every linked work item, with how it was linked.

### Flags

| Flag | Meaning |
|---|---|
| **No story linked** | No linked story, directly or through a child Task/Bug |
| **Linked work item not synced** | The PR mentions `AB#<id>`, but that work item isn't in the database (sync Azure DevOps, or it belongs to another project) |
| **Merged without review** | Merged with no reviewer other than the author |
| **Large PR** | Lines added + deleted above `LARGE_PR_THRESHOLD_LINES` (default 600) |
| **Changes requested** | At least one review requested changes |
| **Open for over a week** | Still open 7 days after opening |
| **Closed without merging** | Abandoned |
| **Merged but not deployed** | Merged more than 24 hours ago with no production deployment found (only when deployments are synced) |
| **State unknown** | Synced before PR states were recorded; run a GitHub sync with *Full refresh on sync* |

**To list PRs without a story:** in the **Stories** column filter choose **No story**, or choose **No story linked** in the Flags filter. The CSV has the same data.

---

## 2) How work items are linked

A PR is linked to a work item when **any** of these is true:

| Source | How |
|---|---|
| `azure link` | The work item has a **GitHub Pull Request** or **GitHub Commit** link in Azure Boards (Development section) |
| `AB# in PR` | `AB#<id>` in the PR title or description |
| `AB# in commit` | `AB#<id>` in a commit message of the PR |
| `branch name` | The work item id appears in the PR's branch name, e.g. `feature/12345-checkout`. Only ids of stored work items count |

A linked **Task or Bug whose parent is a story** links the PR to that story too.

The same links are used everywhere:
- **Team Overview:** the PR timeline's Stories column;
- **DevOps Integration:** the PRs column of the Work items grid;
- **Story Quality:** each story's PRs;
- **Pipeline runs:** their stories (section 3).

Hover a link to see its source.

---

## 3) Stories behind pipeline runs

The **Pipeline runs** grid on DevOps Integration has a **Stories** column. A run is linked to a work item from three sources:

| Source | Where it comes from |
|---|---|
| `Azure (build)` | Work items Azure DevOps itself associates with the run (Builds API `builds/{id}/workitems`), from the commits and PRs the run built |
| `AB# in commit` | `AB#<id>` in the messages of the commits the run built (Builds API `builds/{id}/changes`: the commits since the previous run of that pipeline) |
| `via PR owner/repo#n` | The run built a commit that belongs to a PR (its merge commit, or one of its commits), and that PR is linked to the work item (section 2) |

How it's shown:
- A Task or Bug is shown as `#task → #story` when it has a parent story.
- The filter's values are the story ids, plus **No story**.

**Cost:** for each new pipeline run, the Azure sync makes two extra requests (`workitems` and `changes`), once. A completed run never changes, so these are never fetched again. To turn this off, set `run_details: false` under `azure_devops` in `config.yaml`; the **via PR** source still works without it.

Runs synced before this feature have no details yet. The grid says so, and the next **Sync Azure DevOps** over their date range fills them in.

---

## 4) Sorting and filtering grids

Every grid works the same way:

- **Sort:** click a column header. The first click sorts ascending, the second descending, the third returns to the default order. Empty values always sort last.
- **Filter:** each column has a box under its header, and filters on several columns combine.
  - **Text columns:** type part of the value (case-insensitive).
  - **Category columns** (status, author, kind, team, …): pick a value from the list. For columns with several values per row (stories, tags, flags, developers, testers), a row matches when **any** of its values is the one picked.
  - **Number columns:** `>5`, `>=5`, `<5`, `<=5`, `=5` (or just `5`), or a range `5-10`.
- **Clear filters and sorting** resets the grid. The row counter shows how many rows match.
- Long lists show the first 200 rows; **Show more** loads the next 200.
- Click a row, in grids that have details (PR timeline, Story Quality), to expand it.

Grids:
- **Team Overview:** Pull request timeline.
- **Story Quality:** Stories.
- **DevOps Integration:** People & identities, Efficiency by developer / tester (click a row for that person's DORA), Work items, Pipeline runs.

Useful filters:

| Question | Grid → filter |
|---|---|
| Which PRs have no story? | PR timeline → Stories = **No story** |
| Which big PRs were merged without review? | PR timeline → Lines `>600`, Flags = **Merged without review** |
| What did story 1234 deploy with? | Pipeline runs → Stories = **1234**, Kind = **Production deployment** |
| Which finished work items have no code? | Work items → PRs `0` |
| Which released stories bounced back from QA? | Story Quality → Outcome = **Released**, Flags = **Bounced back from QA** |

---

## 5) Setup and data refresh

Nothing new to configure. To get complete data after upgrading:

1. **Sync from GitHub** with **Full refresh on sync** ticked once. This stores each PR's state, branch, description references, first review time and first approval time.
2. **Sync Azure DevOps** over the range you want. This stores work item history, links, and each pipeline run's work items and commits.

Optional, in `config.yaml`:

```yaml
azure_devops:
  # ...
  run_details: true   # fetch each new run's work items and commits (default true)
```

The token scopes are unchanged: **Work Items: Read** and **Build: Read**.

---

## 6) API and CSV

| Method | Path | Description |
|---|---|---|
| GET | `/api/pull-requests` | `{window, summary, pull_requests}`. Each PR has its `timeline` phases, `stories`, `work_items` (with `sources`), `unresolved_work_item_refs` and `flags`. Parameters: `date_from` / `date_to` (or `days`), `team`, repeatable `branches` |
| GET | `/api/reports/pull-requests.csv` | The same rows as CSV |
| GET | `/api/devops/dora` | Each item in `runs` now has `work_items` (with `sources`), `stories` and `details_fetched` |

CSV columns:
- repository, PR number, title, status, author, team, base and head branch;
- first commit, opened, first review, approved, merged, closed, deployed;
- hours per phase (`coding_hours`, `pickup_hours`, `review_hours`, `merge_hours`, `deploy_hours`), cycle hours, lead to production, age;
- lines added and deleted, files, commits, reviewers, review comments, changes requested;
- stories, work items, unresolved `AB#` references, flags, URL.

The file is UTF-8 with a byte-order mark, so Excel opens it correctly.

---

## 7) Troubleshooting

| Symptom | Fix |
|---|---|
| Almost every PR shows **No story** | Work items aren't synced yet (sync Azure DevOps), or PRs don't reference them. Use `AB#<id>` in PR titles or descriptions, or link PRs from the story in Azure Boards |
| PR shows **Linked work item not synced** | The `AB#` id isn't stored. Sync a date range that includes that work item's last change, check it's one of `azure_devops.work_item_types`, or note it belongs to another project |
| No **Waiting to deploy** phase | No production deployments are synced. Check `deployment_pattern` / `production_pattern` (README 7.2) |
| Pipeline runs show no stories | Sync Azure DevOps again: runs synced before this feature need their details. Commits must reference work items (`AB#`) or belong to PRs linked to stories |
| **Coding** phase missing | Every commit was pushed after the PR was opened, or commits weren't synced (they're limited to team members when `teams` is configured) |
| First review / approval empty | PRs synced before these were recorded: run a GitHub sync with **Full refresh on sync** |
