import pytest

from learning_coach import voice as v


@pytest.mark.parametrize("said,expected", [
    ("2", "2"), ("two", "2"), ("to", "2"), ("too", "2"), ("it's to", "2"), ("for", "4"), ("won", "1"), ("ate", "8"),
    ("forty two", "42"), ("forty-two", "42"), ("the answer is fifty five", "55"), ("one hundred and five", "105"),
    ("a hundred", "100"), ("I think it's twenty one", "21"), ("um seven", "7"), ("seven please", "7"),
    ("5 no wait 6", "6"), ("zero", "0"), ("oh", "0"), ("twelve", "12"),
])
def test_numbers_accepted(said, expected):
    assert v.judge(said, expected) == v.CORRECT


@pytest.mark.parametrize("said,expected", [("three", "4"), ("41", "42"), ("fifty", "55"), ("2", "12")])
def test_numbers_wrong(said, expected):
    assert v.judge(said, expected) == v.WRONG


def test_unclear_vs_wrong():
    assert v.judge("", "5") == v.UNCLEAR
    assert v.judge("banana", "5") == v.UNCLEAR  # no number at all: worth asking again
    assert v.judge("um", "Abuja") == v.UNCLEAR or v.judge("um", "Abuja") == v.WRONG
    assert v.judge(None, "5") == v.UNCLEAR


@pytest.mark.parametrize("said,expected", [
    ("Abuja", "Abuja"), ("it's abuja", "Abuja"), ("the answer is Abuja", "Abuja"), ("nairobbi", "Nairobi"),
    ("kairo", "Cairo"), ("brasilia", "Brasília"), ("the sun", "the sun"), ("sun", "the sun"), ("Addis Ababa", "Addis Ababa"),
])
def test_text_forgiving(said, expected):
    assert v.judge(said, expected, ["sun"] if expected == "the sun" else []) == v.CORRECT


def test_text_negation_and_wrong():
    assert v.judge("it's not paris", "Paris") == v.WRONG
    assert v.judge("London", "Paris") == v.WRONG
    assert v.judge("lagos", "Abuja") == v.WRONG


def test_accept_list():
    assert v.judge("washington dc", "Washington", ["Washington DC"]) == v.CORRECT


@pytest.mark.parametrize("said", ["c a t", "see ay tee", "sea a tea", "cat", "C. A. T."])
def test_spelling_letters(said):
    assert v.judge(said, "cat", match="letters") == v.CORRECT


def test_spelling_wrong():
    assert v.judge("c a d", "cat", match="letters") == v.WRONG
    assert v.judge("k a t", "cat", match="letters") == v.WRONG


@pytest.mark.parametrize("said,frac", [("three quarters", (3, 4)), ("3/4", (3, 4)), ("three over four", (3, 4)),
                                       ("one half", (1, 2)), ("a half", (1, 2)), ("half", (1, 2)), ("two thirds", (2, 3)),
                                       ("three out of five", (3, 5)), ("a quarter", (1, 4)), ("four fifths", (4, 5))])
def test_fractions_parse(said, frac):
    assert v.parse_fraction(said) == frac


def test_fraction_judging():
    assert v.judge("two fourths", "1/2", match="fraction") == v.CORRECT       # equivalent value
    assert v.judge("two fourths", "1/2", match="fraction_exact") == v.WRONG   # must be simplest form
    assert v.judge("one half", "1/2", match="fraction_exact") == v.CORRECT
    assert v.judge("apple", "1/2", match="fraction") == v.UNCLEAR


def test_intents():
    assert v.detect_intent("I don't know") == "skip"
    assert v.detect_intent("can I have a hint") == "hint"
    assert v.detect_intent("say that again") == "repeat"
    assert v.detect_intent("what") == "repeat"
    assert v.detect_intent("I'm done") == "stop"
    assert v.detect_intent("seven") is None


def test_ssml_is_safe():
    s = v.ssml("Hi <b>Ada</b> & co", 'x" onload="y')
    assert s.startswith("<speak>") and s.endswith("</speak>")
    inner = s[len("<speak>"):-len("</speak>")].replace('<break time="350ms"/>', "")
    assert "<" not in inner and "onload" in inner  # markup stripped, no injection
    import xml.dom.minidom
    xml.dom.minidom.parseString(s)


def test_speakable_strips_emoji_and_markup():
    assert v.speakable("Great! \U0001F389 <i>job</i> *ok*") == "Great! job ok"


def test_say_fraction():
    assert v.say_fraction(3, 4) == "three quarters" and v.say_fraction(1, 2) == "one half"


def test_feedback_has_variety():
    import random
    rng = random.Random(1)
    assert len({v.pick(v.PRAISE, rng) for _ in range(40)}) >= 5
