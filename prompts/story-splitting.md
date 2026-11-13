<!--
  Story creation (splitting) instructions.

  Everything in this file (except these HTML comments) is sent to the LLM when
  it splits a requirement into user stories on the Story analyzer page
  ("Create stories"), together with prompts/story-analysis.md (what a good
  story looks like). Edit it to match how your team slices work.
  Another location can be set with STORY_SPLITTING_INSTRUCTIONS in .env.
-->

# How we split requirements into stories

- Slice **vertically**: every story delivers something a user, tester or operator can see and verify end to end. Never split by layer ("API story", "UI story", "DB story").
- A story fits in **one sprint** and is ideally **5 points or less**. Split anything larger by workflow step, business rule, data variation, or happy path vs. edge cases.
- The **first story** is the simplest end-to-end path (the "walking skeleton"). Later stories add rules, variations and edge cases.
- Keep **non-functional work** (performance, monitoring, accessibility) inside the story it applies to, as acceptance criteria, unless it is large enough to stand alone.
- Every story has at least one **failure or edge case** in its acceptance criteria.
- Name **dependencies** between stories by title, and avoid them where a different slice would remove them.
- Put anything unclear in **open questions**; never guess limits, numbers or integrations.
- Suggest tags only from: `frontend`, `backend`, `api`, `ui`, `payments`, `checkout`, `auth`, `performance`, `tech-debt`, `testing`.
