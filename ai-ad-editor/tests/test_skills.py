"""The Claude skills and CLAUDE.md that drive the editor stay well formed and name real commands."""
import re
import shlex

import pytest

from adeditor import cli, paths

ROOT = paths.ROOT
SKILLS = ["new-client", "edit-ad", "ad-notes"]
FENCE = re.compile(r"^```.*?^```[ \t]*$", re.S | re.M)
INLINE = re.compile(r"`[^`\n]*`")
DASHES = re.compile("—|–|--")
ACTIONS = {"client": {"new", "colours", "music", "approve", "lockup"}, "plan": {"check"}}


def skill_path(name):
    return ROOT / ".claude" / "skills" / name / "SKILL.md"


def doc_files():
    return [ROOT / "CLAUDE.md", *(skill_path(n) for n in SKILLS)]


def front_matter(text):
    assert text.startswith("---\n"), "front matter must start on the first line"
    head = text.split("\n---\n", 1)[0][4:]
    fields = {}
    for line in head.splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def prose(text):
    """The text outside fenced code, without the front matter fences and table rules, which are markup."""
    kept = INLINE.sub("", FENCE.sub("", text)).splitlines()
    return "\n".join(line for line in kept if not re.fullmatch(r"[|\s:-]+", line))


def fenced(text):
    return [m.group(0) for m in FENCE.finditer(text)]


@pytest.mark.parametrize("name", SKILLS)
def test_skills_are_well_formed(name):
    path = skill_path(name)
    assert path.is_file()
    fields = front_matter(path.read_text(encoding="utf-8"))
    assert fields["name"] == name
    assert 0 < len(fields["description"]) < 1024
    assert len(path.read_text(encoding="utf-8").splitlines()) < 500


def test_commands_named_in_the_docs_exist():
    parser = cli.build_parser()
    for path in doc_files():
        text = path.read_text(encoding="utf-8")
        # every adeditor command in prose or code, by name
        for m in re.finditer(r"adeditor (\w[\w-]*)(?: (\w[\w-]*))?", text):
            first, second = m.groups()
            if first == "import":  # python code: from adeditor import ...
                continue
            if first in ("client", "plan"):
                assert second in ACTIONS[first], f"{path.name}: adeditor {first} {second}"
            else:
                assert first in cli.COMMANDS, f"{path.name}: adeditor {first}"
        # every command line in a fenced block parses with its real flags
        for block in fenced(text):
            for line in block.splitlines():
                if line.startswith("uv run adeditor "):
                    argv = shlex.split(re.sub(r"<[^>]+>", "x", line))[3:]
                    try:
                        parser.parse_args(argv)
                    except SystemExit:
                        pytest.fail(f"{path.name}: does not parse: {line}")


def test_no_dashes_outside_fenced_code():
    for path in doc_files():
        bad = [line for line in prose(path.read_text(encoding="utf-8")).splitlines() if DASHES.search(line)]
        assert not bad, f"{path.name}: {bad}"


def test_nothing_else_under_claude_has_dashes():
    for path in (ROOT / ".claude").rglob("*"):
        if path.is_file() and path.suffix == ".md":
            assert not DASHES.search(prose(path.read_text(encoding="utf-8"))), path


def test_the_rules_are_carried():
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    for must in ["raw/", ".env", "AI-generated", "faces, voices or deepfakes", "uv run adeditor doctor", "exact words"]:
        assert must in claude, must
    # The speaker and the B-roll people are real likenesses we use; the rule is about faking one.
    assert "anyone's likeness" not in claude


def test_edit_ad_says_where_to_is_looked_for():
    text = skill_path("edit-ad").read_text(encoding="utf-8")
    assert "`to` is looked for only after the `from` match" in text
    assert "With `to_end`, `to` is looked for from the start of the `from` match" in text


def test_new_client_says_how_to_fix_wrong_colours():
    text = skill_path("new-client").read_text(encoding="utf-8")
    assert "uv run adeditor client colours <client> --primary" in text and "colours look wrong" in text


def test_edit_ad_keeps_pixabays_licence_rules():
    text = skill_path("edit-ad").read_text(encoding="utf-8")
    assert "trademark, logo or brand" in text and "misleading way" in text


def test_edit_ad_teaches_the_graphics():
    text = skill_path("edit-ad").read_text(encoding="utf-8")
    for kind in ["cycle", "steps", "versus", "stat", "checklist", "quote"]:
        assert f"`{kind}`" in text, kind
    assert "every word on a graphic was said in the kept take" in text
    assert "uv run adeditor preview <client>" in text and "No sound effects: <reason>." in text
    assert "ground" in text and "0.75" in text


def test_ad_notes_teach_the_sound_notes():
    text = skill_path("ad-notes").read_text(encoding="utf-8")
    for must in ["sfx_offset_db", '"sfx": false', '"flash": false', '"sounds": false']:
        assert must in text, must


def test_new_fenced_commands_parse():
    parser = cli.build_parser()
    assert parser.parse_args(["preview", "x", "v.mp4"]).command == "preview"
    assert parser.parse_args(["preview", "x", "v.mp4", "--ratio", "9x16"])
    assert parser.parse_args(["doctor", "--fix"])
    seen = set()
    for path in doc_files():
        for block in fenced(path.read_text(encoding="utf-8")):
            seen.update(l.split()[3] for l in block.splitlines() if l.startswith("uv run adeditor "))
    assert {"preview", "doctor"} <= seen


def test_doctor_fix_is_run_on_a_warning_and_after_the_keys():
    edit = skill_path("edit-ad").read_text(encoding="utf-8")
    assert "uv run adeditor doctor --fix" in fenced(edit)[0] or "uv run adeditor doctor --fix" in "".join(fenced(edit))
    assert "failed or warned" in edit and "only if the first one failed" not in edit
    guide = (ROOT / "GUIDE.md").read_text(encoding="utf-8")
    step5 = guide.split("## Step 5.", 1)[1].split("## Step 6.", 1)[0]
    assert "uv run adeditor doctor --fix" in step5 and "No sound effects" in step5 and "free ElevenLabs plan" in step5
    for doc in [ROOT / "CLAUDE.md", skill_path("new-client"), skill_path("ad-notes")]:
        assert "uv run adeditor doctor --fix" in "".join(fenced(doc.read_text(encoding="utf-8"))), doc.name


def test_guide_step_5_gives_one_fixed_doctor_line_and_names_the_face_model():
    guide = (ROOT / "GUIDE.md").read_text(encoding="utf-8")
    step5 = guide.split("## Step 5.", 1)[1].split("## Step 6.", 1)[0]
    assert "uv run adeditor doctor --fix" in "".join(fenced(step5))
    assert "FAIL face model" in step5 and "downloads the face model" in step5
    assert "run it again with the fix" not in step5


def test_the_sound_switch_is_named_where_a_person_looks():
    for path in [ROOT / "GUIDE.md", ROOT / "README.md", ROOT / ".env.example", skill_path("edit-ad")]:
        assert "ADEDITOR_NO_SOUNDS=1" in path.read_text(encoding="utf-8"), path.name
    guide = (ROOT / "GUIDE.md").read_text(encoding="utf-8")
    assert "the switch turned off" not in guide


def test_the_docs_say_how_few_extras_a_short_ad_holds():
    edit = skill_path("edit-ad").read_text(encoding="utf-8")
    assert "12 seconds" in edit and "2.5 seconds" in edit
    guide = (ROOT / "GUIDE.md").read_text(encoding="utf-8")
    assert "12 seconds" in guide and "up to 4 an ad" not in guide and "broll/own/" in guide


def test_edit_ad_gives_the_strikes_file_shape():
    edit = skill_path("edit-ad").read_text(encoding="utf-8")
    assert '{"strikes": [' in edit and '"fillers": []' in edit and '"approved": false}' in edit


def test_the_guide_docs_have_no_dashes_outside_code():
    for name in ["GUIDE.md", "README.md"]:
        text = (ROOT / name).read_text(encoding="utf-8")
        assert not [l for l in prose(text).splitlines() if DASHES.search(l)], name


def test_step_4_prompt_hands_claude_the_doctor_command():
    guide = (ROOT / "GUIDE.md").read_text(encoding="utf-8")
    step4 = guide.split("## Step 4.", 1)[1].split("## Step 5.", 1)[0]
    assert "`uv run adeditor doctor --fix`" in step4


def test_the_doctor_lines_are_quoted_as_the_doctor_prints_them():
    guide = (ROOT / "GUIDE.md").read_text(encoding="utf-8")
    edit = skill_path("edit-ad").read_text(encoding="utf-8")
    for text in (guide, edit):
        assert "The sound effects are switched off" in text
