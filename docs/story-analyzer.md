# Story analyzer

The **Story analyzer** page reviews the quality of one Azure Boards user story. Open it from the left panel; the first item there, **Engineering dashboard**, is the existing dashboard.

You get:
- the story's **health**;
- its **tags**, with suggested ones;
- a colour-coded **timeline** of its stages;
- an analysis of its **description**, **acceptance criteria**, attached **evidence** and **comments**;
- a list of **suggested changes** to the story.

The review is done by your configured LLM, following your team's instructions in [`prompts/story-analysis.md`](../prompts/story-analysis.md).

**Contents**

1. [Use it](#1-use-it)
2. [What it shows](#2-what-it-shows)
3. [How health is calculated](#3-how-health-is-calculated)
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

Options:
- **Load the latest from Azure DevOps** (on by default) reloads the story's fields, links, attachments and comments before analyzing.
- **Re-run even if nothing changed** asks the LLM again instead of reusing the last result (see [Caching](#7-caching)).

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

**Demo:** `scripts/seed_devops.sql` adds text, attachments and comments to stories 1011, 1013, 1009 and 1001:
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

The analyze response contains:
- `health`, `sections` (description, acceptance_criteria, evidence, comments), `comments_insights`;
- `delivery`, `suggestions`, `suggested_acceptance_criteria`, `suggested_tags`;
- `lifecycle` (timeline, stage hours, flags, PRs), `stages`;
- `inputs` (the text, attachments and comments that were analyzed);
- `provider`, `cached`, `warnings`.

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
| The result doesn't change after editing the story | Keep **Load the latest from Azure DevOps** ticked; the cache is refreshed when the story changes |
