# Story analyzer

The **Story analyzer** page reviews the quality of one Azure Boards user story. Open it from the left panel; the first item there, **Engineering dashboard**, is the existing dashboard.

You get:
- the story's **health**;
- its **tags**, with suggested ones;
- a colour-coded **timeline** of its stages;
- an analysis of its **description**, **acceptance criteria**, attached **evidence** and **comments**;
- a list of **suggested changes** to the story;
- optionally (deep analysis), a check of the **code in the linked GitHub pull requests** and of the **TestRail test cases and their evidence** against the acceptance criteria.

The page has a second mode, **Create stories**, where the LLM splits a requirement into several suggested user stories (section 3d).

The review is done by your configured LLM, following your team's instructions in [`prompts/story-analysis.md`](../prompts/story-analysis.md).

**Contents**

1. [Use it](#1-use-it)
2. [What it shows](#2-what-it-shows)
3. [How health is calculated](#3-how-health-is-calculated)
   - [3a) Deep analysis: parent items, attachments and your documents](#3a-deep-analysis-parent-items-attachments-and-your-documents)
   - [3b) Code check: linked GitHub pull requests](#3b-code-check-linked-github-pull-requests)
   - [3c) Test check: TestRail test cases and evidence](#3c-test-check-testrail-test-cases-and-evidence)
   - [3d) Create stories: split a requirement](#3d-create-stories-split-a-requirement)
4. [Team instructions (`prompts/story-analysis.md`)](#4-team-instructions-promptsstory-analysismd)
5. [LLM and fallback](#5-llm-and-fallback)
6. [Data it reads](#6-data-it-reads)
7. [Caching](#7-caching)
8. [API](#8-api)
9. [Troubleshooting](#9-troubleshooting)

---

## 1) Use it

1. Click **Story analyzer** in the left panel. Its link is `/#story-analyzer`.
2. Choose a story:
   - **Find a stored story:** search synced stories by title or ID, then click one. Double-click a story to analyze it straight away.
   - **Story number:** type any work item number (`12345`, `#12345` or `AB#12345`). It doesn't need to be synced yet: with Azure DevOps configured, it is loaded on demand.
3. Click **Analyze**. An LLM review can take up to a minute; **Cancel** stops it, and the server stops the LLM call too.

To share a result, click **⤓ Export HTML** above it. You get a static, self-contained page with the analysis as shown, without the picker or the underlying data (see README [4.7](../README.md#47-share-a-page-export-html)).

Options:
- **Load the latest from Azure DevOps** (on by default) reloads the story's fields, links, attachments and comments before analyzing.
- **Re-run even if nothing changed** asks the LLM again instead of reusing the last result (see [Caching](#7-caching)).
- **Deep analysis** (off by default) also reads the parent Feature/Epic, the story's and parents' attachments, and documents you upload (see [3a](#3a-deep-analysis-parent-items-attachments-and-your-documents)).

---

## 2) What it shows

| Section | Content |
|---|---|
| **Header** | Story id, title (link to Azure Boards), state, assignee, points and release. A one-paragraph summary. Who produced the analysis (e.g. `Ollama (qwen2.5-coder:14b)`), when, and whether team instructions were used |
| **Story health** | Overall score 0–100 and level, with its two parts: **content quality** and **delivery health** |
| **Tags** | Current tags; **suggested** tags shown dashed (limit them in the instructions file) |
| **Delivery health** | Score, and each problem found in the board history with the points it cost. Developers and testers |
| **Timeline** | Bar of the time spent in each stage, coloured by stage (same colours as Story Quality), the legend with hours per stage, and every state change with its date and duration. Stages come from `lifecycle` in `config.yaml` ([story-lifecycle.md](story-lifecycle.md#3-configure-the-stages-lifecycle)) |
| **Description** | Score, assessment, strengths (✓) and issues (!). **Show the text on the story** displays the description as plain text |
| **Acceptance criteria** | The same, for the acceptance criteria |
| **Evidence** | Attachments (links), inline images in the description and comments, and linked PRs with their status |
| **Comments** | Key points, unanswered questions, and **Show the comments** |
| **Suggested changes to the story** | Changes by priority (high / medium / low) and area, each with an example; suggested acceptance criteria in Given/When/Then, with **Copy** |

---

## 3) How health is calculated

**Health = 60% content quality + 40% delivery health.** The levels are:
- **Healthy:** 75 or more
- **Needs attention:** 50 or more
- **At risk:** below 50

**Content quality** is the weighted average of the four section scores, each 0–100:

| Section | Weight |
|---|---|
| Acceptance criteria | 35% |
| Description | 25% |
| Evidence | 20% |
| Comments | 20% |

**Delivery health** is computed from the board history and linked PRs, never by the LLM. It starts at 100 and loses points for each problem:

| Problem | Points |
|---|---|
| Sent back from testing to development | −15 each (max −30) |
| Reopened after release or close | −20 |
| Skipped a testing stage | −15 |
| Finished without a linked PR | −10 |
| A linked PR still open when it finished | −10 |
| Closed without release although code was merged | −10 |
| Mostly waiting (flow efficiency below 40%) | −10 |
| A linked PR closed without merging | −5 |
| Released without a `Release x.y.z` tag | −5 |
| Visited a state missing from the stage mapping | −5 |

---

## 3a) Deep analysis: parent items, attachments and your documents

By default the review looks only at the story itself. Tick **Deep analysis** to give it the surrounding context. Each part can be switched off:

| Option | What is read |
|---|---|
| **Parent Feature / Epic** | The story's parent work item, usually the Feature, and its parent, usually the Epic: title, state, description, acceptance criteria and the names of their attachments. Reloaded from Azure DevOps when it is configured |
| **Attachments on the story and its parents** | Files attached to the story and to those parents, downloaded from Azure DevOps. Word (`.docx`), PDF and text files are read; images and other types are listed but not read |
| **Upload documents** | Up to 5 files from your machine: Word (`.docx`), PDF or text (`.txt`, `.md`, `.csv`, `.log`, `.json`, `.xml`, `.yaml`), each up to 10 MB. They're used for this analysis only and **not stored**; only their names and sizes are saved with the result |

Limits:
- Old Word files (`.doc`) aren't read: save them as `.docx`.
- Scanned PDFs without a text layer have no text to read.
- At most 10 attachments are downloaded.
- About 30,000 characters of context in total go to the LLM, shared between the parents and documents; longer texts are cut off.

**What you get:** a **Context** card showing:
- **Alignment score** (0–100): how well the story covers and agrees with its Feature and documents.
- **Missing from the story:** requirements stated in the Feature or documents that the story doesn't cover.
- **Conflicts:** where the story disagrees with them.
- **Sources used** by the LLM.
- **Parent items** and every **document** with its source (story, Feature #…, Epic #…, uploaded), and whether it was **read** or why it was skipped.

The other sections and the suggestions also take the context into account and cite their sources. Without an LLM, the rule-based fallback lists the most frequent terms of the Feature and documents that the story never mentions.

The context review is shown separately and **doesn't change the health score**, so scores stay comparable between normal and deep analyses.

Results are cached separately for deep and normal runs. A deep result is reused only while the parent items and documents are the same: a different upload means a new analysis.

API: `POST /api/story-analyzer/analyze/deep` (multipart form) takes:
- `work_item_id`, `refresh`, `use_cache`, `include_parents`, `include_attachments`;
- one `files` part per upload.

It returns `400` for an unsupported file type, more than 5 files, or a file over 10 MB.

## 3b) Code check: linked GitHub pull requests

Under **Deep analysis**, tick **Code in the linked pull requests**. The analyzer then:

1. Takes the pull requests linked to the story (the same ones listed under Evidence: Azure Boards links and `AB#1234` mentions in PR titles, bodies and branches). Merged PRs come first; at most **5** are read.
2. Downloads each PR's changed files and diffs from GitHub (`GET /repos/{owner}/{repo}/pulls/{n}/files`) with your `GITHUB_TOKEN`.
3. Sends the diffs to the LLM, which decides for **each acceptance criterion** whether the code implements it: **implemented**, **partial**, **not found** or **unclear** (for example, when the relevant code is not in the diff), with evidence (file and what the change does).

The **Implementation** card shows:
- a score and an assessment;
- each criterion with its status and evidence;
- **Missing tests**: criteria with no test changes;
- **Changes unrelated to the story**;
- each PR with its files (test files marked) and how many files were skipped.

Limits and what is skipped:
- Lock files, generated, minified and binary files, and files GitHub shows without a diff are skipped (listed as a count).
- About 40,000 characters of diff go to the LLM, at most 4,000 per file. Non-test files come first, then tests. Files over the budget are named but their diff is left out.
- The diff shows the **change**, not the whole program: code that already existed is not seen, so a criterion may be **unclear** rather than wrong.

Configuration: nothing new. It uses `GITHUB_TOKEN` (needs read access to the repositories' contents and pull requests). For GitHub Enterprise Server set `GITHUB_API_URL` (e.g. `https://github.example.com/api/v3`).

Without an LLM, a rule-based fallback matches the key words of each criterion against the added code lines and file names. It's labelled as such and can't judge meaning.

## 3c) Test check: TestRail test cases and evidence

Under **Deep analysis**, tick **TestRail test cases and evidence**. The analyzer finds the story's test cases in TestRail:

- cases whose **References** field contains the story id (this is what TestRail's Azure DevOps integration fills in), found with `get_cases?refs=` (TestRail 6.5.2 or newer);
- cases linked on the story itself: TestRail case links (`…/index.php?/cases/view/123`) or mentions like `C123` in the description, acceptance criteria or comments.

For each case (at most 30) it reads the title, preconditions, steps and expected results, the names of attachments on the case, and the **latest result** in the most recent test runs: status, comment, defects and the names of the attachments on that result (the evidence). The LLM then decides for **each acceptance criterion** whether the tests cover it (**covered**, **partial**, **not covered**, naming the cases), lists cases that match no criterion, and **evidence problems**: failed, blocked or untested cases, passes without evidence, or evidence that does not show the expected result.

The LLM sees attachment **names**, not their content (screenshots are not opened).

### Configure TestRail

1. In TestRail, create (or choose) a user that can read the projects. Under **My Settings → API Keys** create an API key. An administrator must enable the API once under **Administration → Site Settings → API**.
2. Put the key in `.env`:
   ```
   TESTRAIL_API_KEY=your-api-key
   ```
3. Add a `testrail:` section to `config.yaml`:
   ```yaml
   testrail:
     url: "https://yourcompany.testrail.io"
     user: "qa-bot@yourcompany.com"     # the user the API key belongs to
     project_ids: [1]                   # TestRail project ids (the number in index.php?/projects/overview/1)
     suite_ids: []                      # needed only for projects with several suites
     reference_format: "{id}"           # how the story appears in References, e.g. "AB#{id}"
     runs_to_scan: 10                   # recent runs searched for the latest results
   ```
4. Restart the backend. The page shows a hint next to the checkbox while something is missing.

`reference_format` must match what your team writes in **References**: with TestRail's Azure DevOps integration it is usually the plain id (`1234`). Matching is exact per reference, so `1234` does not match `12345`.

Without an LLM, a rule-based fallback matches criterion key words against the case titles and steps, and still lists the evidence problems (no result, failed, passed without evidence).

## 3d) Create stories: split a requirement

Switch the page to **Create stories** (top of the page). Then:

1. Paste the requirement, and/or upload documents (same formats and limits as deep analysis), and/or give the parent **Feature** number (its description, acceptance criteria and attachments are read).
2. Choose the maximum number of stories (3–15).
3. Click **Split into stories**.

The LLM splits the requirement into **vertical slices** following INVEST and your team's rules. For each story you get a title, an "As a …, I want … so that …" description, Given/When/Then acceptance criteria (including a failure case), story points, tags, dependencies on other suggested stories and notes. You also get **open questions** to answer before refinement and the **assumptions** the LLM made.

What to do with them:
- **Remove** any story you don't want (left out of the exports; **Restore** brings them back).
- **CSV for Azure Boards**: in Azure Boards, **Boards → Work items → Import Work Items**, choose the file, and check the preview before saving. Columns: Work Item Type, Title, Description, Acceptance Criteria, Story Points, Tags. Set the parent link (Feature) in Azure Boards afterwards.
- **Copy Markdown** to paste into a wiki, chat or email; **Export HTML** for a standalone page.

**Nothing is written to Azure DevOps** by the app.

The splitting rules live in [`prompts/story-splitting.md`](../prompts/story-splitting.md) (another file can be set with `STORY_SPLITTING_INSTRUCTIONS`). They're sent together with `prompts/story-analysis.md`, so the suggested stories follow the same rules the analyzer checks. Without an LLM, the requirement is split by headings, then top-level bullets, then paragraphs, and the page says so.

---

## 4) Team instructions (`prompts/story-analysis.md`)

The file holds your team's rules for a good story, in Markdown. For example: user-story form, Given/When/Then, the evidence expected once a story reaches QA, release tags, and allowed tags. A starter version is included; edit it to match how your team works.

How it's used:
- Everything except `<!-- HTML comments -->` is sent to the LLM as **team instructions**, which take precedence over its general advice.
- **Empty or missing file:** the review uses general rules only, and the page says so.
- **Editing it** makes the next analysis run again, because cached results depend on the instructions.
- **In Docker,** `./prompts` is mounted into the container, so edits apply without a rebuild.
- **Another location:** set `STORY_ANALYSIS_INSTRUCTIONS=/path/to/file.md` in `.env`.
- **To check what's in use,** click the file name under **Analyze**.

---

## 5) LLM and fallback

The analyzer uses the same LLM settings as the developer analytics (README section 6):

1. **Online model:** used when `ONLINE_AI_ENABLED=true` and `ONLINE_AI_API_KEY` are set. `gemini-*` models go to Google; other models go to OpenAI.
2. **Ollama:** used when `OLLAMA_BASE_URL` is reachable (`OLLAMA_MODEL`).
3. **Rule-based review:** used when neither answers. The page says so in a warning.

The rule-based review checks:
- **Description:** user-story form, "so that" value, length, structure.
- **Acceptance criteria:** Given/When/Then, number of criteria, failure scenarios, measurable values, and vague words such as "fast", "etc." or "properly".
- **Evidence:** attachments, test-like file names and linked PRs, but only expected once the story reached testing.
- **Comments:** questions without a reply.

The rule-based review also fills any part the LLM leaves out. The header then shows, for example, `Ollama (…) + rule-based for evidence`.

The LLM receives:
- the story fields;
- the description and acceptance criteria as plain text (up to 6,000 characters each);
- the attachment names;
- the last 30 comments;
- the child items;
- delivery facts: stage hours, rework, flags, PRs, developers and testers.

It answers in a fixed JSON format: section scores and findings, comment insights, suggestions, suggested criteria and suggested tags.

---

## 6) Data it reads

| Data | Source |
|---|---|
| Description, acceptance criteria | `System.Description`, `Microsoft.VSTS.Common.AcceptanceCriteria`, stored on every Azure sync and reloaded on analyze |
| Attachments | Work item links of type `AttachedFile` |
| Comments | Azure Boards Comments API (`workItems/{id}/comments`, version `7.1-preview.4`), loaded on analyze |
| Timeline, PRs, people, flags | The story lifecycle ([story-lifecycle.md](story-lifecycle.md)) |

No new token scope is needed: **Work Items: Read** covers all of it.

**Demo:** story 1011 has a parent Feature #1100, whose rules the story partly misses, and an Epic #1200, for trying the deep analysis. `scripts/seed_devops.sql` also adds text, attachments and comments to stories 1011, 1013, 1009 and 1001:
- 1011: a good story;
- 1013: vague, with an open question;
- 1009: no failure scenarios and no evidence;
- 1001: a description only.

---

## 7) Caching

Each result is stored in the `story_analyses` table. Analyzing the same story again **reuses** it, with no LLM call, while all of these are unchanged:
- the story's fields, attachments, comments, PRs and stage;
- the instructions file;
- the LLM settings.

The page shows "reused (nothing changed since)". Tick **Re-run even if nothing changed** to ask the LLM again. In the picker, analyzed stories show an **analyzed** badge.

---

## 8) API

| Method | Path | Description |
|---|---|---|
| GET | `/api/story-analyzer/stories?search=` | Stories for the picker (most recently changed first), with `last_analyzed` |
| GET | `/api/story-analyzer/instructions` | `{path, exists, used, text}` of the instructions file |
| POST | `/api/story-analyzer/analyze` | Body `{"work_item_id": 1234, "refresh": true, "use_cache": true}` |
| POST | `/api/story-analyzer/analyze/deep` | Deep analysis, multipart form: `work_item_id`, `refresh`, `use_cache`, `include_parents`, `include_attachments`, `include_code`, `include_tests`, and `files` (repeat per document) |
| POST | `/api/story-analyzer/split` | Create stories, multipart form: `requirement`, `parent_id` (optional), `max_stories` (1–15, default 8), `files`. Returns `summary`, `stories`, `open_questions`, `assumptions`, `parent`, `documents`, `provider`, `warnings` |
| GET | `/api/story-analyzer/integrations` | Which integrations are configured (Azure DevOps, GitHub token, TestRail) and what is missing; no secrets |

The analyze response contains:
- `health`, `sections` (description, acceptance_criteria, evidence, comments), `comments_insights`;
- `delivery`, `suggestions`, `suggested_acceptance_criteria`, `suggested_tags`;
- `lifecycle` (timeline, stage hours, flags, PRs), `stages`;
- `inputs` (the text, attachments and comments that were analyzed);
- `provider`, `cached`, `warnings`;
- with a deep analysis: `context`, and `implementation` (`pull_requests`, `review`) and `test_coverage` (`configured`, `missing`, `errors`, `cases`, `review`) when those checks were ticked.

It returns `404` when the story isn't stored and can't be loaded from Azure DevOps.

---

## 9) Troubleshooting

| Symptom | Fix |
|---|---|
| "No LLM available, so this is the rule-based review" | Configure an online model or start Ollama (README section 6). From Docker, Ollama on your machine must be reachable at `host.docker.internal:11434` |
| `404 … is not stored and Azure DevOps is not configured` | Sync Azure DevOps, or configure it (README 7.2) so typed numbers are loaded on demand |
| "Could not load the latest version from Azure DevOps" | The stored copy was analyzed instead. Check the PAT and organization (README 7.2) |
| Description or acceptance criteria are empty for synced stories | They were synced before this feature: run **Sync Azure DevOps** again, or analyze with **Load the latest from Azure DevOps** |
| Comments missing | Comments are loaded when analyzing with **Load the latest from Azure DevOps** ticked |
| Timeline says "No state history yet" | Run **Sync Azure DevOps** (it downloads each story's history) |
| The page says no team instructions are used | `prompts/story-analysis.md` is missing or only has comments; check `STORY_ANALYSIS_INSTRUCTIONS` |
| A document shows "Azure DevOps is not configured, so attachments cannot be downloaded" | Attachments are downloaded with `AZURE_DEVOPS_PAT`; configure Azure DevOps (README 7.2), or upload the document instead |
| A document shows "no text found" | A scanned PDF without a text layer, or an empty file: use a PDF with selectable text, or a `.docx` |
| "The story has no parent work item (Feature) to include" | The story has no parent link in Azure Boards. Add it under **Related Work → Parent** |
| Implementation card: "GitHub refused access (401/403)" | The `GITHUB_TOKEN` can't read that repository; give it read access to contents and pull requests |
| Implementation card: "pull request not found" | The PR was deleted, or it's in a repository the token can't see |
| "The story has no linked pull requests" | Link the PR in Azure Boards (**Development → Add link**) or put `AB#1234` in the PR title or body, then sync |
| "TestRail is not configured" | Add the `testrail:` section to `config.yaml` and `TESTRAIL_API_KEY` to `.env` (section 3c), then restart |
| "TestRail rejected the credentials" | `testrail.user` must be the user that owns `TESTRAIL_API_KEY`, and the API must be enabled in TestRail |
| No test cases found although they exist | Check the cases' **References** contain the story id in the form of `reference_format`; for projects with several suites set `suite_ids` |
| Test cases have no latest result | They're not in the last `runs_to_scan` runs; raise the number |
| Create stories gives a single story per heading or paragraph | No LLM was available, so the rule-based split was used |
| The result doesn't change after editing the story | Keep **Load the latest from Azure DevOps** ticked; the cache is refreshed when the story changes |
