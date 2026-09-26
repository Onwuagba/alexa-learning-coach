"""Voice helpers: forgiving matching of *spoken* answers, intent spotting, spoken feedback, SSML.

Speech-to-text hands us things like "it's to", "forty-two", "three quarters", "c a t". Everything here is
pure functions so it is easy to test. Matching never raises."""
from __future__ import annotations

import difflib
import re
import unicodedata
from xml.sax.saxutils import escape

UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split())}
TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
NUMTOK = set(UNITS) | set(TENS) | {"hundred", "thousand", "and", "a"}
# Homophones speech recognisers return for numbers. Only applied when the expected answer is numeric.
NUM_HOMOPHONES = {"to": "two", "too": "two", "tu": "two", "for": "four", "fore": "four", "won": "one",
                  "ate": "eight", "oh": "zero", "nought": "zero", "none": "zero", "tree": "three", "free": "three",
                  "sicks": "six", "nein": "nine", "fife": "five", "tin": "ten", "fourty": "forty"}
LETTERS = {"sea": "c", "see": "c", "bee": "b", "be": "b", "dee": "d", "gee": "g", "jay": "j", "kay": "k",
           "ell": "l", "em": "m", "en": "n", "oh": "o", "pea": "p", "pee": "p", "queue": "q", "cue": "q",
           "are": "r", "ess": "s", "tea": "t", "tee": "t", "you": "u", "vee": "v", "ex": "x", "why": "y",
           "eye": "i", "ay": "a", "eff": "f", "aitch": "h", "zed": "z", "zee": "z", "double": "", "u": "u"}
DENOM_WORDS = {"half": 2, "halves": 2, "third": 3, "thirds": 3, "quarter": 4, "quarters": 4, "fourth": 4, "fourths": 4,
               "fifth": 5, "fifths": 5, "sixth": 6, "sixths": 6, "seventh": 7, "sevenths": 7, "eighth": 8,
               "eighths": 8, "ninth": 9, "ninths": 9, "tenth": 10, "tenths": 10, "twelfth": 12, "twelfths": 12}
DENOM_SPOKEN = {2: ("half", "halves"), 3: ("third", "thirds"), 4: ("quarter", "quarters"), 5: ("fifth", "fifths"),
                6: ("sixth", "sixths"), 7: ("seventh", "sevenths"), 8: ("eighth", "eighths"), 9: ("ninth", "ninths"),
                10: ("tenth", "tenths"), 12: ("twelfth", "twelfths")}
FILLER_RE = re.compile(
    r"^(?:(?:um+|uh+|er+|erm|hmm+|well|so|okay|ok|alexa|ah)|(?:i think|i guess|maybe|probably|is it|it is|it's|its|"
    r"the answer is|answer is|that's|that is|i say|my answer is|it would be|it must be|is|the|a|an)) +", re.I)
TAIL_RE = re.compile(r" +(?:please|i think|maybe|i guess|right|isn't it)$", re.I)


def strip_accents(s: str) -> str:
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()


def clean(s: str) -> str:
    """Lowercase, ascii, keep letters/digits and / . -; collapse spaces."""
    s = strip_accents(s).lower().replace("'", "'")
    s = re.sub(r"[^a-z0-9/.' -]", " ", s)
    s = re.sub(r"(?<![0-9])[.-]|[.-](?![0-9a-z])", " ", s)  # hyphen in "forty-two" -> handled below
    return " ".join(s.replace("-", " ").split())


def strip_fillers(s: str, keep_articles: bool = False) -> str:
    prev = None
    while prev != s:
        prev = s
        m = FILLER_RE.match(s + " ")
        if m and (not keep_articles or m.group(0).strip() not in {"the", "a", "an"}):
            s = s[m.end():].strip() if m.end() <= len(s) else ""
        s = TAIL_RE.sub("", s).strip()
    return s


# -- numbers ------------------------------------------------------------------------------------
def words_to_numbers(text: str) -> list[int]:
    """Every number in `text`, in order, from digits and/or number words ("one hundred and five" -> 105)."""
    toks, out, i = clean(text).split(), [], 0
    while i < len(toks):
        t = toks[i]
        if re.fullmatch(r"\d+", t):
            out.append(int(t)); i += 1; continue
        if t in UNITS or t in TENS or t in {"hundred", "thousand"} or (t == "a" and i + 1 < len(toks) and toks[i + 1] in {"hundred", "thousand"}):
            total, cur, j, seen = 0, 0, i, False
            while j < len(toks):
                w = toks[j]
                if w in UNITS and not (seen and cur % 10 and w != "zero" and cur >= 10 and cur % 10):
                    cur += UNITS[w]
                elif w in TENS:
                    cur += TENS[w]
                elif w == "a" and j + 1 < len(toks) and toks[j + 1] in {"hundred", "thousand"}:
                    cur += 1
                elif w == "hundred":
                    cur = max(cur, 1) * 100
                elif w == "thousand":
                    total, cur = total + max(cur, 1) * 1000, 0
                elif w == "and" and j + 1 < len(toks) and toks[j + 1] in (set(UNITS) | set(TENS)) and seen:
                    pass
                else:
                    break
                seen = True; j += 1
            out.append(total + cur); i = max(j, i + 1)
            continue
        i += 1
    return out


def spoken_numbers(text: str, homophones: bool = True) -> list[int]:
    s = clean(text)
    if homophones:
        s = " ".join(NUM_HOMOPHONES.get(t, t) for t in s.split())
    return words_to_numbers(s)


# -- fractions ----------------------------------------------------------------------------------
def parse_fraction(text: str) -> tuple[int, int] | None:
    s = strip_fillers(clean(text)).replace(" a half", " one half")
    m = re.search(r"(\d+) ?/ ?(\d+)", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    s = " ".join(NUM_HOMOPHONES.get(t, t) for t in s.split())
    for sep in (" over ", " out of ", " upon "):
        if sep in f" {s} ":
            a, b = f" {s} ".split(sep, 1)
            na, nb = words_to_numbers(a), words_to_numbers(b)
            if na and nb:
                return na[-1], nb[0]
    toks = s.split()
    for i, t in enumerate(toks):
        if t in DENOM_WORDS:
            before = words_to_numbers(" ".join(toks[:i]))
            n = before[-1] if before else 1
            if t in {"half", "quarter", "third", "fifth", "sixth"} and not before:
                n = 1
            return n, DENOM_WORDS[t]
    return None


def say_fraction(n: int, d: int) -> str:
    sing, plur = DENOM_SPOKEN.get(d, (f"{d}th", f"{d}ths"))
    name = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine",
            10: "ten", 11: "eleven", 12: "twelve"}.get(n, str(n))
    return f"{name} {sing if n == 1 else plur}"


# -- phonetic-ish key for names STT mangles (Cairo/Kairo, Accra/Akra) ---------------------------
def phonetic_key(s: str) -> str:
    s = clean(s).replace(" ", "")
    for a, b in (("ph", "f"), ("ck", "k"), ("kn", "n"), ("wr", "r"), ("wh", "w"), ("gh", ""), ("x", "ks"), ("q", "k"),
                 ("ce", "se"), ("ci", "si"), ("cy", "sy"), ("c", "k"), ("z", "s"), ("y", "i")):
        s = s.replace(a, b)
    s = re.sub(r"(.)\1+", r"\1", s)
    return s[:1] + re.sub(r"[aeiouh]", "", s[1:])


# -- the verdict --------------------------------------------------------------------------------
CORRECT, WRONG, UNCLEAR = "correct", "wrong", "unclear"


def _is_number(s: str) -> bool:
    return bool(re.fullmatch(r"-?\d+", s.strip()))


def judge(given: str, expected: str, accept: list[str] | None = None, match: str = "auto") -> str:
    """Return CORRECT, WRONG or UNCLEAR (couldn't make sense of it: worth asking again)."""
    try:
        return _judge(str(given or ""), str(expected), accept or [], match)
    except Exception:  # never let a weird transcript crash a session
        return WRONG


def _judge(given: str, expected: str, accept: list[str], match: str) -> str:
    if not clean(given):
        return UNCLEAR
    if match == "auto":
        match = ("number" if _is_number(expected) else "fraction" if re.fullmatch(r"\d+/\d+", expected) else "text")
    if match == "number":
        nums = spoken_numbers(strip_fillers(clean(given), keep_articles=True))
        if not nums:
            return WRONG if _is_number(clean(given)) else UNCLEAR
        return CORRECT if int(expected) in nums[-1:] or (len(set(nums)) == 1 and int(expected) == nums[0]) else WRONG
    if match in ("fraction", "fraction_exact"):
        f = parse_fraction(given)
        if not f:
            return UNCLEAR
        n, d = (int(x) for x in expected.split("/"))
        if f[1] == 0:
            return WRONG
        if match == "fraction_exact":
            return CORRECT if f == (n, d) else WRONG
        return CORRECT if f[0] * d == f[1] * n else WRONG
    if match == "letters":
        toks = clean(given).replace(".", "").split()
        if len(toks) > 1:
            toks = [LETTERS.get(t, t) for t in toks]
        got = "".join(toks).replace("'", "")
        return CORRECT if got == clean(expected).replace(" ", "") else WRONG
    # text
    g = strip_fillers(clean(given), keep_articles=False) or clean(given)
    cands = [expected, *accept]
    ncands = [strip_fillers(clean(c)) or clean(c) for c in cands]
    if g in ncands:
        return CORRECT
    if match == "exact":
        return WRONG
    tokens = g.split()
    if {"not", "no", "isn't", "wasn't"} & set(tokens[:3]) and len(tokens) > 1:
        return WRONG
    for c in ncands:
        if len(c) >= 4 and len(tokens) <= 8 and re.search(rf"(?<![a-z]){re.escape(c)}(?![a-z])", g):
            return CORRECT
        if len(c) >= 5 and len(g) >= 4:
            if difflib.SequenceMatcher(None, g, c).ratio() >= 0.84:
                return CORRECT
            if phonetic_key(g) == phonetic_key(c) and len(phonetic_key(c)) >= 2 and \
               difflib.SequenceMatcher(None, g, c).ratio() >= 0.6:
                return CORRECT
    return WRONG


# -- intents --------------------------------------------------------------------------------------
_INTENTS = [
    ("skip", r"\b(i (do not|don't|dont) know|no idea|not sure|skip|pass|give up|i give up|next one|don't know)\b"),
    ("hint", r"\b(hint|clue|help( me)?|give me a (hint|clue)|i need help)\b"),
    ("repeat", r"\b(repeat|say (that|it) again|again please|one more time|pardon|what was (that|the question)|"
               r"what did you say|come again|sorry what)\b"),
    ("stop", r"\b(stop|quit|finish(ed)?|i'?m done|that'?s enough|all done|end (the )?(session|practice)|goodbye|bye)\b"),
]


def detect_intent(text: str) -> str | None:
    s = clean(text)
    if s in {"what", "huh", "again", "sorry"}:
        return "repeat"
    for name, rx in _INTENTS:
        if re.search(rx, s):
            return name
    return None


# -- SSML / speech-safe text ------------------------------------------------------------------------
def speakable(text: str) -> str:
    """Plain text safe for TTS: no markup, no emoji, single spaces."""
    text = re.sub(r"<[^>]*>", " ", str(text))
    text = re.sub(r"[<>&*_#`~^|\\{}\[\]]", " ", text)
    text = re.sub(r"[^\x00-\x7FÀ-ɏ]", "", text)
    return " ".join(text.split())


def ssml(*parts: str, pause_ms: int = 350) -> str:
    """`<speak>` document from sentences; XML-escaped so a child's name can never inject markup."""
    body = f'<break time="{pause_ms}ms"/>'.join(escape(speakable(p)) for p in parts if p)
    return f"<speak>{body}</speak>"


# -- encouraging feedback (varied, streak aware) -------------------------------------------------
PRAISE = ["Nice one!", "You got it!", "Yes, that's right!", "Brilliant!", "Spot on!", "Great thinking!",
          "Correct! Well done.", "That's it!", "Fantastic!", "Right again!"]
PRAISE_STREAK = ["{n} in a row! You're on fire.", "That's {n} right in a row. Amazing!", "{n} in a row, keep it up!"]
GENTLE = ["Not quite, but good try.", "Almost! Mistakes help us learn.", "Nice effort. Let's learn from that one.",
          "Good try. That one is tricky.", "Oops, not this time. You'll get the next one."]
REVEAL = ["The answer is {a}.", "It was {a}.", "The right answer is {a}."]
LEVEL_UP = ["You moved up to level {n}!", "Level {n} unlocked. You're growing fast!", "Up to level {n}. Great work!"]
LEVEL_DOWN = ["Let's practise a little easier ones for a moment.", "We'll go one step back and build up again."]
HINT_PRAISE = ["Nice, the hint helped!", "Good, you worked it out with a little help."]
INTROS = ["Here's one.", "Try this one.", "Ready?", "Next question.", "Okay, listen carefully."]
REVISIT = ["Let's try one from before.", "Here's one to remember.", "This one came up earlier. Let's see it again."]


def pick(pool: list[str], rng, **fmt) -> str:
    return rng.choice(pool).format(**fmt)
