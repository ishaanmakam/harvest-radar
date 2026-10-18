# Agent voice

The morning messages are the product's voice: the plan a farmer hears on the call and reads in the text and the email,
the route the officer gets, and the replies when either of them asks a question. This page is the rule for those
words and says where the rule is enforced.

## Voice of the morning messages

Write in plain sentences. Use no headers and no bullet lists in replies. Use no emoji. Use no exclamation marks.
Use no em dashes and no en dashes. Do not open with a pleasantry. Do not close with an offer of further help.
Do not restate the question. Give every number a unit and a source. State uncertainty as a number or not at all.
When you recommend an action, name the field, the operation, the amount and the time.

## Where the rule lives

The section above is the text of `VOICE_OF_THE_MORNING` in `intelligence/voice_guard.py`.
`intelligence/voice_agent.py` puts it into the Grok Voice session instructions, after the scope rules and before
the day's facts, so every call is spoken to it. The translation prompt in `intelligence/providers.py` is left alone
because translated text goes through the gate below.

## Where the rule is enforced

Every SMS body, voice script and email body is composed by `render_plan` and `render_route` in
`intelligence/queue_morning.py`, and both return through `voice_guard.enforce`. The preview in `messages.json`,
the facts in a voice session and the replies to inbound questions use the same two functions, so they pass the
same gate.

The gate checks seven categories: dashes, glyphs (middle dot, arrows, ellipsis), emoji, exclamation marks, copy
words, copy frames and the words that name the technology. A clean text passes through unchanged. A dirty text is
logged with the category and the match, handed once to a regenerate hook when the caller gives one, and then
scrubbed if it still fails. Scrubbing only touches symbols: dashes become a comma or a period, middle dots become a
slash, arrows become words, ellipses become a period, emoji and exclamation marks are dropped. Banned words and
frames stay in the text and are logged, because rewording is a human job. Dry runs go through the same gate, so
the preview shows the words that would be sent.

To check a text by hand, start Python inside `intelligence/` and call `voice_guard.violations(text)`, or read
the queue log, which prints every violation as a `voice guard:` line. The fixtures in
`intelligence/tests/test_voice_guard.py` show one example per category.
