<!--
  Story Analyzer instructions.

  Everything in this file (except these HTML comments) is sent to the LLM as the
  team's instructions for reviewing a user story, and takes precedence over its
  general advice. Edit it to match how your team writes stories; changes apply
  to the next analysis (results are re-computed when this file changes).
  Delete the content to use only the general review.

  In Docker this file is mounted from ./prompts, so no rebuild is needed.
  Another location can be set with STORY_ANALYSIS_INSTRUCTIONS in .env.
-->

# How we review a user story

## Description
- Starts with the user story sentence: **As a** <role>, **I want** <capability> **so that** <benefit>.
- Explains the context and the problem, not only the solution.
- States what is **in scope** and what is **out of scope**.
- Mentions dependencies, feature flags, and impacted systems or APIs when they exist.
- Links designs (Figma), specs or support tickets when the work came from them.

## Acceptance criteria
- Every criterion is testable and written as **Given / When / Then**.
- One behaviour per criterion; no "and also" criteria.
- Covers the happy path **and** at least one failure or edge case: invalid input, permissions, empty states, timeouts.
- Uses measurable values instead of vague words ("fast", "user friendly", "etc.", "properly").
- Non-functional needs are explicit when relevant: performance, accessibility, security, logging/monitoring.

## Evidence
- Once a story reaches QA (QA Dev In Progress or later), it should have test evidence: screenshots, recordings or test-run reports, ideally named after the criterion they prove.
- Released stories must have their pull requests linked (AB#<id> in the PR title or description).
- Released stories carry a `Release <version>` tag.

## Comments
- Questions from QA or developers must be answered on the story, and the decision reflected in the description or acceptance criteria.
- Flag comments that change scope without the description being updated.

## Health
- Treat a story as **at risk** when its acceptance criteria are missing or untestable, when it bounced back from QA more than once, or when open questions remain on a released story.

## Suggestions
- Suggest concrete rewrites, not general advice. Rewrite weak acceptance criteria as Given / When / Then.
- Suggest tags only from: `frontend`, `backend`, `api`, `ui`, `payments`, `checkout`, `auth`, `performance`, `tech-debt`, `testing`.
