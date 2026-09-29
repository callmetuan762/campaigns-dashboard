"""video-report: noise filtering, hook/beat extraction, balanced lift, and the notes file's own rules."""

import re
from collections import Counter

import yaml

from teardown import video_report as vr


def test_ocr_noise_filter_keeps_words_drops_fragments():
    assert vr.clean_line("Make a long trip feel mini...") == "Make a long trip feel mini..."
    for junk in ("12R2B", "БL:5", "REMO ESCE"[:4], "5:19"):
        assert vr.clean_line(junk) is None


def test_hook_and_beats_from_evidence():
    f = {"ocr": [(0, "Most birthday gifts"), (0, "get used once..."), (1000, "But not Yoto!"), (3000, "12R2B"),
                 (3000, "The screen-free audio player")],
         "asr": [(500, "Hi there"), (4000, "Stories and music")]}
    hook = vr.hook_of(f)
    assert hook["on_screen"] == ["Most birthday gifts", "get used once...", "But not Yoto!"]
    assert hook["said"] == "Hi there"
    beats = vr.beats_of(f)
    assert beats[0] == (0, "screen", "Most birthday gifts / get used once...")
    assert (3, "screen", "The screen-free audio player") in beats  # the junk line was dropped
    assert (4, "said", "Stories and music") in beats


def test_notes_file_follows_the_brand_rules():
    notes = yaml.safe_load(vr.NOTES.read_text())
    ideas = notes["ideas"]
    grid = Counter((i["segment"], i["template"]) for i in ideas)
    assert set(grid.values()) == {3} and len(grid) == 20  # 4 segments x 5 templates x 3
    assert len({i["id"] for i in ideas}) == len(ideas)
    for i in ideas:
        lp = i.get("lp") or notes["segments"][i["segment"]]["lp"]
        if i["segment"] == "overview":
            assert lp == "/", i["id"]  # product overview links to the homepage only
    banned = re.compile(r"—|screen[- ]free|magical|clinically|tamagotchi|no microphone|deeply feeling", re.IGNORECASE)
    for i in ideas:
        blob = " ".join([i["title"], i["hook"], i.get("vo", ""), i["primary_text"], *(b[1] for b in i["beats"])])
        assert not banned.search(blob), (i["id"], banned.search(blob).group(0))
