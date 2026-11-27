# Sessions

Every conversation with the AI assistant is saved as a **session**, so you can come back to it later. List your sessions, reopen any one by its id, and carry on where you left off. You see the earlier chat, and the assistant has it in context: the questions, its answers, and the data its tools returned. It can answer follow-ups such as "and which of those are in eu-west-1?" without starting again.

```bash
spendlens session list                                  # your sessions: id, when, first and last question
spendlens session open s-20261127-141502                # show the earlier chat, then keep asking
spendlens session open s-20261127-141502 "And which of those are in eu-west-1?"   # one follow-up, then stop
```

## What gets saved

A session starts with your first question. Each of these is saved:

| You run | Saved as |
|---|---|
| `spendlens ask "..."` | A new session with an id like `s-20261127-141502` (the date and time it started, UTC) |
| `spendlens ask -i` | One session for the whole conversation |
| `spendlens ask -S ecs "..."` | The session named `ecs`: created the first time, continued after that |
| `spendlens ask -C "..."` | A follow-up in the last session used |
| `spendlens ask --new "..."` | A new session, even if another is current (`-S NAME --new` starts that one over) |

For each question, the session keeps:
- **The question** and **the answer**, as you saw them.
- **The tool calls**, with their arguments: the SQL that ran, the CloudTrail filters, the files read or written.
- **The data behind the answer:** the start of what each tool returned (up to 4,000 characters per call).
- When it was asked, the model, whether live AWS was used, and the number of tool calls.

Sessions are JSON files in `sessions/` next to your history database (`spendlens history path` shows where that is). They stay on your machine and are never uploaded. When a session continues, its earlier exchanges are sent to your AI provider as part of the conversation, the same as any question.

## List, open, show, delete

| Command | What it does |
|---|---|
| `spendlens session list` | Every session, most recent first: id, number of questions, last used, first and last question. `*` marks the one `ask -C` continues |
| `spendlens session open [ID] [QUESTION]` | Show the earlier chat, then continue it: keep asking (type `exit` to stop), or ask one `QUESTION` and stop. Without an id, opens the last session used |
| `spendlens session show [ID]` | Show the questions and answers only. `-o session.html` or `-o session.md` saves the whole session as one page |
| `spendlens session delete ID` | Delete one session |
| `spendlens session clear --yes` | Delete every session |

`session open` options:

| Option | What it does |
|---|---|
| `--last N` | Show only the last N earlier questions. All of them are still in the assistant's context, up to `llm.session_turns` |
| `-q`, `--quiet` | Don't show the earlier chat; just continue |
| `--show-tools` | Show every tool call and the SQL behind it, as with `ask` |
| `--live` / `--no-live` | Allow or block live AWS queries for the new questions |
| `--set KEY=VALUE` | Override a setting for this run, e.g. `--set llm.model=...` |

A reopened session answers from the same history it used before (your saved runs, or the demo data).

## Follow up from `ask`

You don't need `session open` to follow up:

```bash
spendlens ask "Which ECS services are over-provisioned?"         # saved as a new session
spendlens ask -C "Which cluster has the most?"                   # continues the last session used
spendlens ask -C --live "Show their CPU and memory settings"     # mix saved runs and live AWS
spendlens ask -S rds "How many Aurora clusters are there?"       # a separate, named session
spendlens ask -S rds -i                                          # keep asking in it
```

Each answer ends with the session id and how to continue it:

```
session s-20261127-141502 · 1 question(s) · continue it: spendlens session open s-20261127-141502 (or ask -C "...")
```

## How the assistant remembers

When a session continues, spendlens replays the earlier exchanges to the model before your new question:

1. **The most recent questions:** up to `llm.session_turns` (20 by default).
2. **Each answer:** up to 6,000 characters.
3. **The data behind each answer:** each tool call with its arguments and the start of its result, shortened to `llm.session_data_chars` (6,000 characters per question). This is what lets "which of those…" work from the same rows instead of guessing.

The assistant is told to reuse that data for follow-ups, and to query again for anything it doesn't show or when fresher data matters. Replay works with every provider. A follow-up is never served from the answer cache, because its answer depends on the conversation.

Set `llm.session_data_chars: 0` to replay only the questions and answers, which uses fewer tokens.

## How many sessions are kept

spendlens keeps your **newest 100 automatic sessions**, ranked by last use. When there are more, the oldest is deleted the next time you ask a question. Continuing a session counts as using it, so a conversation you keep coming back to stays.

**Named sessions are never deleted automatically.** These are sessions started with `ask -S NAME`. Name a conversation you want to keep, or delete it yourself with `spendlens session delete NAME`.

To keep more or fewer automatic sessions, set the number in `finops.yaml`:

```yaml
llm:
  sessions_keep: 300           # the newest automatic sessions kept (named ones are never deleted)
  sessions_auto: true          # save every conversation; false = only sessions named with -S
  session_turns: 20            # earlier questions replayed when a session continues
  session_data_chars: 6000     # tool data replayed per earlier question; 0 = answers only
```

The same setting for one run or one machine:

```bash
spendlens ask --set llm.sessions_keep=300 "..."
export SPENDLENS__LLM__SESSIONS_KEEP=300
```

To keep one conversation as a document, save it as a page: `spendlens session show ID -o ecs-review.html`.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `llm.sessions_auto` | `true` | Save every conversation as a session. `false`: only `-S NAME`, `--new` and `-C` save one |
| `llm.sessions_keep` | `100` | The newest automatic (unnamed) sessions kept; the oldest go first. Named sessions are never deleted |
| `llm.session_turns` | `20` | Earlier questions replayed when a session continues |
| `llm.session_data_chars` | `6000` | Tool data replayed per earlier question. `0`: questions and answers only |

See [Configuration](../configuration/) for every setting and how to override it.
