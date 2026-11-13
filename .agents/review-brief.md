# Briefing an external reviewer

Every PR gets the automatic reviews (Codex and Greptile comment on the diff when it opens). A **briefed round** is the heavier check, owed by the PRs `AGENTS.md` → Workflow names: the reviewer checks out the head, runs the suites, breaks the code to see the tests go red, and lists the contract on its own before reading the author's tests. A briefed round starts from a brief. The agent opening the PR fills the `‹slots›` in the template below and saves the result as `.dev/<issue>/review-brief-<round>.md` (gitignored). The reviewer is then started with one line: *"Follow the review brief at `<absolute path>`"*. A few lines in chat say what the round asks; the whole brief is not pasted into chat.

**The PR body is the payload; the brief is the envelope.** The brief points at sections of the PR body by heading, so the PR thread stays the one record a second round reads. That works only if the body has the sections.

## PR body shape

| Section | Holds |
|---------|-------|
| opening line | the authoring agent and model, and the head sha |
| **What** | the change, by file; the spec section it implements; what it deliberately leaves alone |
| **Deliberate calls** | numbered, each with its reason: the decisions a reviewer may overrule, including any place the PR changes the spec |
| **Tests** | new tests and counts; which acceptance criterion of the issue each one covers; what is **explicitly untested** and why |
| closing line | suite counts, lint, build, schema change yes/no |

The reviewer picks its own model family from the opening line: a reviewer from the author's family shares its blind spots.

## The template

Send everything outside `‹…›` as written. Drop a paragraph only when it cannot apply (a docs-only PR has no test claim).

````markdown
Review PR #‹N› — branch `‹branch›`, head `‹sha›`, against `main` at `‹main-sha›`. It closes #‹issue›. Read the issue, the PR body, `AGENTS.md` (the architecture invariants) and the spec sections the issue names in `docs/v0.2-spec.md` before the diff.

**Context.** TLG-IPAM is a single-owner homelab IP address manager: FastAPI + SQLite, a React SPA, and an MCP server at `/mcp`. ‹Two or three sentences: what the issue asks for and what this branch does about it. File names, not adjectives.› ‹"No schema change" / "Schema: additive only — new tables …, new nullable column …".›

**Environment.** ‹"You are in a worktree at this head." / "Your worktree was cut from `main`, not from the PR head. Before anything else run `git fetch origin` and `git checkout --detach ‹sha›`, and confirm `git rev-parse HEAD` prints that sha."› Create `api/.venv` from `api/requirements-dev.txt` if it is missing. Run the backend suite from `api/` with `.venv/bin/python -m pytest`. ‹"Frontend untouched" / "From `web/`: `npm ci`, then `npm run lint`, `npm run test`, `npm run build`".› Keep scratch files in a temp directory of your own and delete it before you finish. Leave the tree as you found it: `git status --short` empty, same branch and head. ‹When another PR has merged since the branch was cut: the test count on the head alone, and the count merged with `main`.›

**Two jobs, in this order.**

*First, examine the contract on your own.* Before reading the author's test list, write down what the issue and the named spec sections require: each endpoint, tool, table, and rule, and the value space of each field. Then compare your list with the PR body's **Tests** section. Anything on your list that no test covers is yours to probe first. An "explicitly untested" entry is a question, not a pass.

*Second, check the author's claims.* Re-measure the suite counts rather than reading them. For the tests you judge load-bearing — at minimum ‹two or three, and why those› — break the code at the site the test guards and confirm the test goes red on the assertion that names the behaviour, then restore. Do this in a throwaway worktree (`git worktree add <temp path> ‹sha›`), never with `git checkout -- <path>` in the working tree.

**Invariants in play.** ‹The `AGENTS.md` invariants and spec §7.2 invariants this change touches, one line each.› Check each against the diff: logic lives in `api/app/services/`, every write is audit-logged with the right `source`, schema changes are additive, and nothing reachable over MCP configures agents, scan targets, or scan scope.

**Deliberate calls — judge each.** ‹Restate them in one line apiece, numbered as on the PR.› Say which you would overrule.

**Where I'd push, because these are assumptions and not proofs:**

- ‹One assumption per bullet: what would falsify it and where to look. Three to six. See "Filling this section" below.›

**Severity by real exposure.** This is a single-owner tool on a home network. From M3 the `/mcp` mount and the OAuth routes are internet-facing, and the scan agent holds raw-socket capability on each VLAN: rate findings in authentication, token handling, agent ingest, and anything that sends packets by that exposure.

**A P3 alone is not a NO-GO.** If every finding is a P3, the verdict is GO with the P3s listed as follow-ups to file, unless a finding is something a later fix could not recover (a shipped contract, a schema shape, stored data). Rate severity first; do not raise a level to reach a verdict.

**If something cannot run,** say so at the top and list every claim that depended on it as not re-measured. Reading is not verifying.

**Output.** A verdict (GO / NO-GO), then findings numbered with P1–P3, each with the reproduction at `‹sha›` and the remedy. Open with a line naming yourself, your model, and the head you reviewed. End with what you examined that the PR body did not list, and what you left unexamined, so a second round starts from there.

Write the review to a file and post it once with `gh pr comment ‹N› --body-file <path>` (`--body-file`, because a shell string mangles backticks and `$`). The PR thread is the memory for the next round.
````

## Filling "Where I'd push"

The one section a template cannot write. Answer these about the branch; each answer of "I reasoned, I did not prove" is a bullet:

- **What does the change assume about its neighbours** — another writer to the same table, the order of checks, a value an earlier milestone guarantees?
- **Where does input cross a trust boundary** — an agent report, an OAuth client's metadata, a DNS name, an MCP tool argument — and what is the evidence each field is validated where the spec says?
- **What would a client notice changed** — a status code, a response field, a tool's schema — and is it in the brief?
- **Which test is least deterministic** (timing, ordering, a network mock) and why is it enough?
- **What did you decide where the spec was silent?** Those belong under Deliberate calls; point at one here when you want a second opinion on it specifically.
