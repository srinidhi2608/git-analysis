# Story Quality: the story lifecycle report

The **Story Quality** tab follows every user story from creation to release. It combines three things:

- the story's **state history** from Azure Boards,
- the **GitHub pull requests** behind it,
- the **people** who built and tested it.

From these it derives quality signals: time in each stage, rework, integrity checks, and how much of the story's time went into code. Everything on the page can be downloaded as CSV.

This guide covers what is built, how every number is calculated, and how to configure it.

**Contents**

1. [What you get](#1-what-you-get)
2. [Set it up](#2-set-it-up)
3. [Configure the stages (`lifecycle`)](#3-configure-the-stages-lifecycle)
4. [How stories are linked to pull requests](#4-how-stories-are-linked-to-pull-requests)
5. [How each value is calculated](#5-how-each-value-is-calculated)
6. [Flags](#6-flags)
7. [CSV reports](#7-csv-reports)
8. [API](#8-api)
9. [Testing data (placeholder)](#9-testing-data-placeholder)
10. [Troubleshooting](#10-troubleshooting)

---

## 1) What you get

| Part | What it shows |
|---|---|
| **Summary tiles** | Number of stories and their outcome (released / closed without release / in progress), first-time-right rate, QA bounce rate, reopen rate, median lead and cycle time, flow efficiency and code share |
| **Median time per stage** | For every non-final stage (New, Development, QA, QAA, INT, and the waiting states), the median time stories spent there. Also shows bugs per 1,000 changed lines, escaped defects and stories per release version |
| **Stories table** | One row per story: outcome, a timeline bar coloured by stage, lead time (or age if still open), PRs, lines changed, rework count and flags. **Click a row** to see its full state history with dates, durations and assignees, the activity metrics, developers and testers, and every linked PR with how it was linked |
| **Filters** | The date range at the top of the page, plus: finished stories only or also in progress, team, developer/tester, tag, flag, and a title/ID search |
| **CSV buttons** | Stories, state changes, and story–PR links, each with the same filters (section 7) |

**Which stories are included.** Work items of the configured story types (`User Story` and `Product Backlog Item` by default).
- **Released or closed in range** (the default) shows stories that reached their final state inside the date range.
- **Also in progress** adds stories that are still open, plus stories finished after the start of the range.

---

## 2) Set it up

1. **Connect Azure DevOps.** See README section 7.2. No new token scope is needed: state history uses **Work Items: Read**.
2. **Map your board states to stages.** Use `lifecycle` in `config.yaml` (section 3). The built-in defaults already match this workflow:

   | State | Meaning | Stage |
   |---|---|---|
   | `New` | Story created | `new` (waiting) |
   | `Active` | A developer started work | `dev` (work) |
   | `QA Dev In Progress` | QA testing in the lower environment | `qa` (work) |
   | `QA Dev Complete` | QA in the lower environment done | `qa_done` (waiting) |
   | `QAA In Progress` | Integration testing in the QAA environment | `qaa` (work) |
   | `QAA Complete` | QAA done | `qaa_done` (waiting) |
   | `INT` | INT started | `int` (work) |
   | `Released` | Deployed to production | `released` |
   | `Closed` / `Removed` | Finished **without** a release: documentation, spike, or abandoned work | `closed` |

3. **Tag releases.** A released story should carry a tag such as `Release 2026.10.1`. The minor and patch parts are optional, so `Release 2026` and `Release 2026.10` also count. The version is shown on the story and grouped in the summary. A released story without such a tag is flagged.
4. **Sync.**
   - Click **⟳ Sync Azure DevOps** on the DevOps Integration tab. For each work item the sync now also downloads its **update history** (state changes, assignee changes, links). It does this only when the item changed since the last sync, so later syncs stay cheap.
   - Run one **⟳ Sync from GitHub** with **Full refresh on sync** ticked. This stores each PR's state (open / merged / closed), branch name, GitHub id and `AB#` references, which the story links depend on.
5. **Open the Story Quality tab.**

> **API cost:** the first Azure sync after upgrading makes one extra request per work item (its history). After that, only changed items are fetched again; the sync message reports how many.

**Demo without Azure DevOps.** Load the sample data, then open Story Quality with **Last 90 days** and **Also in progress**:

```bash
docker compose exec db psql -U git_user -d git_analysis -f /scripts/seed_data.sql
docker compose exec db psql -U git_user -d git_analysis -f /scripts/seed_devops.sql
```

The demo includes:
- a QA bounce
- a reopened story
- a release that skipped testing
- a story closed without development
- stories released without a release tag
- an open PR on a released story
- PRs linked by branch name
- an escaped defect

---

## 3) Configure the stages (`lifecycle`)

```yaml
lifecycle:
  # Work item types treated as stories (their child Tasks and Bugs roll up into them).
  story_types: ["User Story", "Product Backlog Item"]

  # Stages in workflow order. Every Azure Boards state belongs to exactly one stage.
  stages:
    - {name: new,      label: "New",            kind: wait,     states: ["New"]}
    - {name: dev,      label: "Development",    kind: work,     states: ["Active"]}
    - {name: qa,       label: "QA (lower env)", kind: work,     states: ["QA Dev In Progress"]}
    - {name: qa_done,  label: "QA complete",    kind: wait,     states: ["QA Dev Complete"]}
    - {name: qaa,      label: "QAA testing",    kind: work,     states: ["QAA In Progress"]}
    - {name: qaa_done, label: "QAA complete",   kind: wait,     states: ["QAA Complete"]}
    - {name: int,      label: "INT",            kind: work,     states: ["INT"]}
    - {name: released, label: "Released",       kind: released, states: ["Released"]}
    - {name: closed,   label: "Closed",         kind: closed,   states: ["Closed", "Removed"]}

  # Tag that marks the production release; the first capture group is the version.
  release_tag_pattern: '(?i)^release\s*v?(\d+(?:\.\d+){0,2})$'

  testing:
    enabled: false   # see section 9
```

| Setting | Meaning |
|---|---|
| `name` | Short id, used in the CSV column names (`qa_hours`, `qa_entered_at`, …) |
| `label` | Shown on the page |
| `kind` | `work`: someone is actively working (counts as active time). `wait`: queued between steps. `released`: deployed to production, story finished. `closed`: finished without a release |
| `states` | Azure Boards state names, case-insensitive. Several states can share a stage |
| order | The order of the list is the workflow order. **Moving to an earlier stage is rework** (section 5.3) |

Notes:
- The **first `work` stage is development**. Every non-final stage after it counts as testing (QA, QAA, INT and their waiting states).
- A state that is not listed anywhere is reported as an **unmapped state**: a warning on the page and a flag on the story. Its time is not counted in any stage. Add the state to `stages` and reload; no re-sync is needed.
- Restart the app (or `docker compose restart app`) after editing `config.yaml`.

---

## 4) How stories are linked to pull requests

A PR belongs to a story when **any** of these is true. The page and the CSV show which source(s) linked each PR.

| Source | How | Notes |
|---|---|---|
| `azure link` | The story has a **GitHub Pull Request** or **GitHub Commit** link in Azure Boards (Development section) | Matched by GitHub's PR id; a commit link matches the PR containing that commit |
| `AB# in PR` | `AB#<id>` in the PR **title or description** | The Azure Boards convention |
| `AB# in commit` | `AB#<id>` in a commit message of the PR | |
| `branch name` | A number in the PR's branch name that is the id of a story (or child item) being analysed, e.g. `feature/12345-checkout`, `bugfix/US12345` | Only some developers follow this, so it's one source among several. Numbers that are not story ids are ignored |
| `via Task #n` / `via Bug #n` | Any of the above on a **child** Task or Bug of the story | Child items roll up into their parent story |

PR status:
- **merged**
- **open**
- **abandoned** (closed without merging)
- **unknown** (synced before PR states were recorded; run a GitHub sync with *Full refresh on sync*)

---

## 5) How each value is calculated

All times are UTC. A story's time in a state runs from the change into that state until the next change, or until now if it is still in that state.

### 5.1 Dates

| Value | Definition |
|---|---|
| `created_at` | Work item creation |
| `dev_start_at` | First move into the development stage (or into any `work` stage if development was skipped) |
| `<stage>_entered_at` | First time the story entered that stage |
| `released_at` | Last move into a `released` stage, if the story ends released |
| `closed_at` | Last move into a `closed` stage |
| `finished_at` | `released_at` for released stories; the close date for stories closed without release; empty while in progress |

**Outcome:**
- **released**: entered a `released` stage and ended in a final stage (Released, or Closed after Released)
- **closed_unreleased**: ended in a `closed` stage without ever being released
- **in_progress**: anything else

### 5.2 Durations

| Value | Definition |
|---|---|
| **Lead time** | `created_at` → `finished_at` |
| **Cycle time** | `dev_start_at` → `finished_at` |
| **Age** | `created_at` → now, for stories in progress |
| **Time per stage** (`<stage>_hours`) | Sum of all periods in that stage, including repeated visits |
| **Active time** (`work_hours`) | Time in `work` stages between development start and finish |
| **Waiting time** (`wait_hours`) | Time in `wait` stages (e.g. QA complete, QAA complete) between development start and finish |
| **Flow efficiency** | active ÷ (active + waiting). Low values mean stories spend their time queued between steps |
| **Code time** | First commit of the linked PRs → last merge. Empty while a linked PR is still open |
| **Code share** | code time ÷ cycle time: how much of the story's working life the code took. Low code share with a long cycle points to waiting, unclear requirements or long testing |
| **Lines per point** | (lines added + deleted) ÷ story points: an estimation check |

### 5.3 Rework

| Value | Definition |
|---|---|
| **Rework count** | Moves from a testing stage, or from Released/Closed, back to an earlier non-final stage |
| **QA bounces** | Moves from a testing stage back to development (or earlier) |
| **Reopened** | Moves from Released or Closed back to an earlier stage |
| **Repeated stages** | Stages entered more than once |
| **Skipped stages** | For released stories: `work` stages never entered (e.g. released without QAA or INT) |
| **Closed directly** | Finished without ever entering a `work` stage (e.g. New → Closed) |

### 5.4 People

| Value | Definition |
|---|---|
| **Developers** | Authors of the linked PRs, plus whoever was assigned while the story was in development |
| **Testers** | Whoever was assigned while the story was in a testing `work` stage, plus whoever moved it out of one |

A person's `role` in `teams` (developer / tester) wins over these rules. Names come from the `teams` configuration (README section 3.3), so GitHub and Azure DevOps identities of one person appear as one name. The page lists people per story; it does not rank individuals.

### 5.5 Bugs

| Value | Definition |
|---|---|
| **Bugs linked** | Child Bugs of the story plus Bugs linked to it with a **Related** link |
| **Escaped defects** | Linked bugs created after the story's release |
| **Bugs per 1,000 changed lines** | Bugs linked to released stories ÷ (their changed lines ÷ 1,000) |

### 5.6 Summary rates

| Rate | Definition |
|---|---|
| **First-time-right** | Released stories with no rework ÷ released stories |
| **QA bounce rate** | Stories with at least one QA bounce ÷ stories that entered a testing stage |
| **Reopen rate** | Finished stories that were reopened ÷ finished stories |
| **Medians** | Over released stories (stage medians: over the stories that entered that stage) |

---

## 6) Flags

| Flag | Meaning | What to check |
|---|---|---|
| `qa_bounce` | Sent back from testing to development | Requirement clarity, unit tests, PR review depth |
| `reopened` | Moved back after Released/Closed | Escaped problems, premature closure |
| `skipped_stages` | Released without passing a testing stage | Process bypass |
| `closed_directly` | Finished without development | Fine for duplicates; otherwise check |
| `closed_without_code` | Finished with no linked PR | A non-code story, or a missing `AB#` link |
| `open_pr_at_finish` | Finished while a linked PR is still open | Unmerged work: was it released? |
| `abandoned_pr` | A linked PR was closed without merging | Superseded or wasted work |
| `released_without_release_tag` | Released but no `Release x.y.z` tag | Tagging discipline |
| `release_tag_not_released` | Has a release tag but did not reach Released | Board not updated |
| `closed_unreleased_with_code` | Closed without release although code was merged | Merged code that never shipped, or a mis-set state |
| `unmapped_states` | Visited a state missing from `lifecycle.stages` | Add it to config.yaml |
| `no_history` | No state history yet | Run *Sync Azure DevOps* |

---

## 7) CSV reports

All three use the filters on the page and open directly in Excel:
- UTF-8 with a byte-order mark;
- times in ISO 8601 UTC;
- durations as plain numbers of hours, so they can be summed;
- lists separated by `; ` inside a cell.

| File | One row per | Columns |
|---|---|---|
| `story-lifecycle-<from>-<to>.csv` | story | id, title, type, state, outcome, team, assignee, points, tags, release version; created, dev start, `<stage>_entered_at` for every stage, released, closed; lead / cycle / age hours; `<stage>_hours` for every non-final stage; active, waiting, flow efficiency; rework count, QA bounces, reopened, repeated and skipped stages, closed directly; PR count (merged / open / abandoned), lines added / deleted, files, commits, review comments, changes requested, first code, last merge, code time, code share, lines per point; developers, testers; bugs linked, escaped defects; `test_cases`, `test_pass_rate`, `coverage_pct` (empty until testing is integrated); flags; URL |
| `story-transitions-<from>-<to>.csv` | state change | story id, title, from/to state, from/to stage, changed at, changed by, hours in the previous state, `is_backward` (a move to an earlier stage) |
| `story-pull-requests-<from>-<to>.csv` | story × PR | story id and title, repository, PR number and title, status, author, base and head branch, lines, review comments, changes requested, created, merged, link sources, URL |

---

## 8) API

| Method | Path | Description |
|---|---|---|
| GET | `/api/stories/lifecycle` | `{filters, config, summary, stories}`; each story includes `timeline`, `transitions` and `pull_requests` |
| GET | `/api/reports/story-lifecycle.csv` | Stories CSV |
| GET | `/api/reports/story-transitions.csv` | State changes CSV |
| GET | `/api/reports/story-pull-requests.csv` | Story–PR links CSV |

Query parameters (all optional):

| Parameter | Meaning |
|---|---|
| `date_from`, `date_to` | ISO date or date-time; default the last 30 days |
| `status` | `completed` (default) or `all` |
| `team`, `person` | Person = member key from `teams` |
| `tags` | Repeatable |
| `tag_mode` | `any` or `all` |
| `types` | Override the story types |

Example:

```bash
curl -o stories.csv "http://localhost:8000/api/reports/story-lifecycle.csv?date_from=2026-07-01&date_to=2026-09-30&team=Payments"
```

---

## 9) Testing data (placeholder)

Test results and code coverage are not integrated yet. When pipelines start running integration tests and publishing coverage, this report will read:
- Azure Test Plans test runs linked to stories,
- the pipeline test results and code coverage of each story's builds.

That needs the **Test Management: Read** token scope.

Until then:
- `lifecycle.testing.enabled` is `false`;
- the CSV has empty `test_cases`, `test_pass_rate` and `coverage_pct` columns, so spreadsheets built on it keep working;
- the page says "Testing data: not integrated yet".

Time spent testing is already available from the state history: see the QA, QAA and INT stage hours and the testers per story.

---

## 10) Troubleshooting

| Symptom | Fix |
|---|---|
| "No stories match" | The stories need state history: run **Sync Azure DevOps**. Check the date range and choose **Also in progress**. Check `story_types` matches your work item type names |
| Warning "These board states are not in lifecycle.stages" | Add the listed states to a stage in `config.yaml` and restart |
| A story shows **No history** | It was synced before this feature. Re-sync a range that includes its last change; histories download for items whose change date differs from the stored one |
| Stories have no PRs | Add `AB#<id>` to PR titles or descriptions, link PRs from the story in Azure Boards, or include the id in branch names. Run a GitHub sync with **Full refresh on sync** once so existing PRs get their description references, branch and state |
| PR status shows **unknown** | Run a GitHub sync with **Full refresh on sync** |
| Testers are missing | Assign the story to the tester while it is in a QA/QAA/INT state, or give the person `role: tester` in `teams` |
| A released story has no release version | Tag it `Release <version>` in Azure Boards, or adjust `release_tag_pattern` |
