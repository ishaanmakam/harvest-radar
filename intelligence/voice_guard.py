"""Voice guard: the output gate for every farmer-facing text the morning queue composes (SMS body, voice script,
email body, the facts in a Grok Voice session, inbound replies). queue_morning.render_plan / render_route call
enforce() on their result, so every channel gets the same checked words.

    violations(text) -> [(category, match), ...]     what the text does wrong, in text order per category
    enforce(text, regenerate=None, log=None) -> str   clean text comes back as the same object; dirty text is logged,
                                                      regenerated once when a regenerate(categories) hook is given,
                                                      and scrubbed when it still fails

Categories (docs/agent-voice.md says why): dashes, glyphs, emoji, exclamation, copy-words, copy-frames, ai-word.

Scrubbing is mechanical and only touches punctuation and symbols: em and en dashes become a comma and a space,
or a period when they end a clause (a dash that starts a line is dropped); middle dots become " / "; arrows become
" to " (right) and " up " (north-east); ellipses become a period; emoji and exclamation marks are dropped, with a
period keeping the sentence boundary after a dropped exclamation mark. Banned words and frames are left in place and
logged: rewording is a human job.
"""
import logging
import re

try:                      # the regex module knows the Extended_Pictographic property; it is optional
    import regex as _regex
except ImportError:       # pragma: no cover - depends on the environment
    _regex = None

logger = logging.getLogger("voice_guard")

EM_DASH, EN_DASH = "\u2014", "\u2013"
MIDDLE_DOT, RIGHT_ARROW, NE_ARROW, ELLIPSIS = "\u00b7", "\u2192", "\u2197", "\u2026"
_QUOTES = "\"'\u201c\u201d\u2018\u2019"
_CLOSERS = _QUOTES + ")]"

# Emoji: Extended_Pictographic without the copyright, registered and trademark signs (and without the north-east
# arrow, which the glyphs category owns). Without the regex module, a conservative set of code point ranges.
if _regex is not None:
    EMOJI = _regex.compile(r"(?![\u00a9\u00ae\u2122\u2197])\p{Extended_Pictographic}")
else:
    EMOJI = re.compile("[\U0001F000-\U0001FAFF"            # all the SMP emoji blocks, flags included
                       "\u2600-\u26FF"                      # miscellaneous symbols
                       "\u2702-\u2705\u2708-\u270D\u270F\u2712\u2714\u2716\u271D\u2721\u2728\u2733\u2734\u2744"
                       "\u2747\u274C\u274E\u2753-\u2755\u2757\u2763\u2764\u2795-\u2797\u27A1\u27B0\u27BF"   # dingbats
                       "\u2194-\u2196\u2198\u2199\u21A9\u21AA\u2934\u2935"                                   # arrows
                       "\u231A\u231B\u2328\u23CF\u23E9-\u23F3\u23F8-\u23FA\u24C2\u25AA\u25AB\u25B6\u25C0"
                       "\u25FB-\u25FE\u2B05-\u2B07\u2B1B\u2B1C\u2B50\u2B55\u203C\u2049\u2139\u3030\u303D"
                       "\u3297\u3299]")
_EMOJI_TAIL = "[\uFE0F\u200D\u20E3\U0001F3FB-\U0001F3FF]"     # variation selector, joiner, keycap, skin tones

CATEGORIES = (
    ("dashes", re.compile(f"[{EM_DASH}{EN_DASH}]")),
    ("glyphs", re.compile(f"[{MIDDLE_DOT}{RIGHT_ARROW}{NE_ARROW}{ELLIPSIS}]|\\.{{3}}(?=[{_QUOTES}<>])")),
    ("emoji", EMOJI),
    ("exclamation", re.compile(f"[^\\W_]!+(?=[\\s{_QUOTES}]|$)")),
    ("copy-words", re.compile(
        r"\b(?:unlock|seamless|supercharg|empower|revolutioni[sz]|cutting.edge|next.gen|effortless|ai.powered|"
        r"game.chang|leverag|harness|elevate|powerful|intuitive|robust|streamlin|actionable insight|data.driven|"
        r"state.of.the.art|world.class|best.in.class|at your fingertips|peace of mind|take control|stay ahead|"
        r"informed decisions|delve|tapestry|testament|landscape)", re.IGNORECASE)),
    ("copy-frames", re.compile(
        r"\b(?:oops|whoops|uh.oh|something went wrong|nothing here yet|lorem ipsum|get started|learn more|"
        r"welcome back|let[\u2019']s|great question|i[\u2019']d be happy|here[\u2019']s what i found|hope this helps|"
        r"it.s worth noting|simply|effortlessly)\b", re.IGNORECASE)),
    ("ai-word", re.compile(r"\bAI\b|\bA\.I\.|\bLLM\b|(?i:\b(?:artificial intelligence|machine learning|agentic)\b)")),
)
CATEGORY_NAMES = tuple(name for name, _ in CATEGORIES)
SCRUBBABLE = ("dashes", "glyphs", "emoji", "exclamation")

VOICE_OF_THE_MORNING = (
    "Voice of the morning messages. Write in plain sentences. Use no headers and no bullet lists in replies. "
    "Use no emoji. Use no exclamation marks. Use no em dashes and no en dashes. Do not open with a pleasantry. "
    "Do not close with an offer of further help. Do not restate the question. Give every number a unit and a source. "
    "State uncertainty as a number or not at all. When you recommend an action, name the field, the operation, "
    "the amount and the time.")


def violations(text):
    """[(category, matched text), ...] for everything in `text` that the voice rules ban; [] when it is clean."""
    if not isinstance(text, str) or not text:
        return []
    found = []
    for name, pattern in CATEGORIES:
        found.extend((name, m.group(0)) for m in pattern.finditer(text))
    return found


def categories_of(found):
    """The category names in `found`, in CATEGORIES order, each once."""
    present = {c for c, _ in found}
    return [c for c in CATEGORY_NAMES if c in present]


def describe(found, per_category=3):
    """One line for a log: 'dashes x2 (\u2014), exclamation x1 (y!)'."""
    parts = []
    for cat in categories_of(found):
        matches = [m for c, m in found if c == cat]
        shown = ", ".join(repr(m) for m in matches[:per_category])
        more = f" +{len(matches) - per_category}" if len(matches) > per_category else ""
        parts.append(f"{cat} x{len(matches)} ({shown}{more})")
    return ", ".join(parts)


# ---------------- scrubbing ----------------
_DASH_RUN = re.compile(f"[ \\t]*[{EM_DASH}{EN_DASH}]+[ \\t]*")
_SENTENCE_END = ".!?,;:"


def _dash(m):
    s, start, end = m.string, m.start(), m.end()
    before = s[start - 1] if start > 0 else "\n"
    after = s[end] if end < len(s) else "\n"
    if before == "\n":                              # the dash starts a line: drop it
        return ""
    if after == "\n" or after in _CLOSERS:          # the dash ends a clause: a period, unless one is there already
        return "" if before in _SENTENCE_END else "."
    if after in _SENTENCE_END:                      # punctuation follows: the dash just goes
        return ""
    return " " if before in _SENTENCE_END else ", "       # punctuation already there: no second comma


_EMOJI_RUN = (_regex or re).compile(f"[ \\t]*(?:{EMOJI.pattern})(?:{_EMOJI_TAIL}|{EMOJI.pattern})*{_EMOJI_TAIL}*")
_EXCLAIM = re.compile(f"([^\\W_])!+(?=[\\s{_QUOTES}]|$)")
_ELLIPSIS = re.compile(f"{ELLIPSIS}|\\.{{3}}(?=[{_QUOTES}<>])")


def scrub(text):
    """The mechanical fixes listed in the module docstring; words are never changed."""
    out = _DASH_RUN.sub(_dash, text)
    out = re.sub(f"[ \\t]*{MIDDLE_DOT}[ \\t]*", " / ", out)
    out = re.sub(f"[ \\t]*{RIGHT_ARROW}[ \\t]*", " to ", out)
    out = re.sub(f"[ \\t]*{NE_ARROW}[ \\t]*", " up ", out)
    out = _ELLIPSIS.sub(".", out)
    out = _EMOJI_RUN.sub("", out)
    out = _EXCLAIM.sub(r"\1.", out)
    out = re.sub(r"[ \t]{2,}", " ", out)                       # a dropped symbol never leaves a double space
    out = re.sub(r"(?m)^[ \t]+|[ \t]+$", "", out)              # or a space at a line edge
    return out


def enforce(text, regenerate=None, log=None):
    """The gate. Clean text is returned unchanged (the same object). Otherwise the violations are logged, the
    regenerate(category_names) hook is given one chance to produce clean text, and whatever still violates is
    scrubbed (symbols) or left in place and logged (words and frames). log(str) defaults to the module logger."""
    say = log if log is not None else logger.warning
    found = violations(text)
    if not found:
        return text
    say(f"voice guard: {describe(found)}")
    if regenerate is not None:
        again = regenerate(categories_of(found))
        if isinstance(again, str) and again:
            found = violations(again)
            if not found:
                say("voice guard: regenerated text is clean")
                return again
            say(f"voice guard: regenerated text still fails: {describe(found)}")
            text = again
    cleaned = scrub(text)
    left = violations(cleaned)
    fixed = [c for c in categories_of(found) if c in SCRUBBABLE]
    if fixed:
        say(f"voice guard: scrubbed {', '.join(fixed)}")
    if left:
        say(f"voice guard: left in place for a human to reword: {describe(left)}")
    return cleaned
