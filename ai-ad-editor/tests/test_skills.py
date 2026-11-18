"""The Claude skills and CLAUDE.md that drive the editor stay well formed and name real commands."""
import re
import shlex

import pytest

from adeditor import cli, paths

ROOT = paths.ROOT
SKILLS = ["new-client", "edit-ad", "ad-notes"]
FENCE = re.compile(r"^```.*?^```[ \t]*$", re.S | re.M)
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
    kept = FENCE.sub("", text).splitlines()
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
