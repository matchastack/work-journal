"""A repository's history as the model reads it: its digest (FR-GH-3 to FR-GH-5).

Pure functions: `read_history` (`app/github/reader.py`) fetches the history, and `build_digest`
turns it into labelled items, in windows of a few dozen, which the model reads one at a time.

- **Kept** (FR-GH-4): only work that counts as the owner's (`app/github/authors.py`). Each merged
  pull request is one item, with its commits folded in, and so is each commit no pull request
  covers. In a repository the owner's account owns, an "about" item describes the project: its
  description, languages and README.
- **Left out** (FR-GH-5): merges, reverts and what they revert, empty messages, squash commits
  that repeat a pull request, and commits that are part of one.
- **Cleaned** (FR-GH-5): commit hashes, issue and pull-request numbers, addresses, emails,
  @mentions, trailers, checklists, HTML, images and emoji are removed. So are the README's code
  blocks, and its roadmap, to-do and acknowledgement sections, which describe unbuilt work or
  name other people. No incidental number can then back a metric, and other people's names
  never reach the model. No line, commit or star count is added.

An item's `evidence` is the text a fact's numbers and tools are checked against: who made it,
in words, then its title and its cleaned text. The labels and dates written around it hold
digits, so they're never part of it.
"""

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.github.api import Commit, Pull
from app.github.authors import AGENTS, Authorship, Owner, commit_authorship, pull_authorship
from app.github.reader import History, PullHistory
from app.text import plain

ItemKind = Literal["about", "pull", "commit"]
LeftOut = Literal["merge", "revert", "reverted", "empty", "covered", "person", "bot"]

WINDOW_ITEMS = 30
WINDOW_CHARS = 12_000
"""The most items, and roughly the most characters, the model reads at once."""
PULL_CHARS = 2_000
COMMIT_CHARS = 800
DESCRIPTION_CHARS = 300
README_CHARS = 6_000
LEFTOVER_WORDS = 3
"""A cleaned line with fewer words than this, from which something was removed, is dropped."""
PULL_COMMIT_TITLES = 15
"""The most commit titles a pull request's item lists."""
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
LEFT_OUT_WORDS: Mapping[LeftOut, tuple[str, str]] = {
    "merge": ("merge", "merges"),
    "revert": ("revert", "reverts"),
    "reverted": ("change that was reverted", "changes that were reverted"),
    "empty": ("empty message", "empty messages"),
    "covered": ("commit that's part of a pull request", "commits that are part of a pull request"),
    "person": ("item by another person", "items by other people"),
    "bot": ("item by an automation bot", "items by automation bots"),
}
"""How each reason something was left out reads, for one and for several."""


@dataclass(frozen=True)
class Item:
    label: str
    """How the model cites it, such as `pr1` or `c4`, unique in the digest."""
    kind: ItemKind
    ref: str
    """The pull request's number or the commit's hash; empty for the "about" item."""
    url: str
    at: datetime
    """When it happened: the merge, or the commit's authoring."""
    title: str
    body: str
    made_by: str
    """Who made it, in words, such as "By you, with Claude Code"; empty for "about"."""
    agent: str | None = None
    """The AI coding agent that made it or helped make it, if any (FR-GH-4)."""
    coauthored: bool = False
    """Whether another person had a part in it."""

    @property
    def evidence(self) -> str:
        """The text the facts citing this item are checked against."""
        made_by = f"{self.made_by}." if self.made_by else ""
        return "\n".join(part for part in (made_by, self.title, self.body) if part)

    def render(self) -> str:
        """The item as the model reads it: a header with its label and month, then the
        evidence."""
        month = f"{MONTHS[self.at.month - 1]} {self.at.year}"
        match self.kind:
            case "about":
                header = f"[{self.label}]"
            case "pull":
                header = f"[{self.label}] Pull request, merged in {month}."
            case "commit":
                header = f"[{self.label}] Commit, {month}."
        return f"{header}\n{self.evidence}"


@dataclass(frozen=True)
class Digest:
    repository: str
    url: str
    private: bool
    owned: bool
    """Whether the owner's account owns the repository, and it isn't a fork (FR-GH-4)."""
    items: tuple[Item, ...]
    windows: tuple[tuple[Item, ...], ...]
    left_out: Mapping[LeftOut, int]
    pulls_read: int
    commits_read: int
    more_pulls: bool = False
    """Whether the search had more merged pull requests than GitHub returns (C7)."""

    def render_window(self, index: int) -> str:
        """Window `index` (from 0) as the model reads it."""
        whose = (
            "It's the person's own repository."
            if self.owned
            else "It's someone else's repository; only the person's work in it is listed."
        )
        lines = [
            f"Repository: {self.repository}. {whose}",
            f"Part {index + 1} of {len(self.windows)}.",
            "",
            "\n\n".join(item.render() for item in self.windows[index]),
        ]
        return "\n".join(lines)


def build_digest(
    history: History, *, window_items: int = WINDOW_ITEMS, window_chars: int = WINDOW_CHARS
) -> Digest:
    """The history's digest, as described in the module docstring."""
    owner = history.owner
    owned = history.owned
    left_out: Counter[LeftOut] = Counter()
    numbers = {read.pull.number for read in history.pulls}
    in_pulls = {commit.sha for read in history.pulls for commit in read.commits}
    # A rebase merge puts new copies of the commits on the branch: the same title and time.
    copies = {_copy_key(commit) for read in history.pulls for commit in read.commits}
    reverted_pulls = {n for read in history.pulls for n in _reverted_pulls(read.pull)}
    reverted_commits = {sha for commit in history.commits for sha in _reverted_commits(commit)}

    pulls: list[tuple[PullHistory, Authorship]] = []
    for read in history.pulls:
        if _is_revert(read.pull.title):
            left_out["revert"] += 1
        elif read.pull.number in reverted_pulls:
            left_out["reverted"] += 1
        else:
            made = pull_authorship(read.pull, read.commits, owner)
            if made.counts(owned=owned):
                pulls.append((read, made))
            else:
                left_out["bot" if made.maker == "bot" else "person"] += 1

    commits: list[tuple[Commit, Authorship]] = []
    for commit in history.commits:
        title = commit.message.split("\n", 1)[0]
        if commit.is_merge:
            left_out["merge"] += 1
        elif _covered(commit, in_pulls, copies, numbers):
            left_out["covered"] += 1
        elif _is_revert(title):
            left_out["revert"] += 1
        elif any(commit.sha.startswith(sha) for sha in reverted_commits):
            left_out["reverted"] += 1
        elif not clean(title, max_chars=COMMIT_CHARS, leftovers=False):
            left_out["empty"] += 1
        else:
            made = commit_authorship(commit, owner)
            if made.counts(owned=owned):
                commits.append((commit, made))
            else:
                left_out["bot" if made.maker == "bot" else "person"] += 1

    items = _labelled(
        [_pull_item(read, made, owner) for read, made in pulls]
        + [_commit_item(commit, made) for commit, made in commits]
    )
    if history.with_about and (about := _about_item(history, items)) is not None:
        items.insert(0, about)
    return Digest(
        repository=history.repository.full_name,
        url=history.repository.html_url,
        private=history.repository.private,
        owned=owned,
        items=tuple(items),
        windows=_windows(items, window_items, window_chars),
        left_out=dict(left_out),
        pulls_read=len(history.pulls),
        commits_read=len(history.commits),
        more_pulls=history.more_pulls,
    )


def describe_left_out(left_out: Mapping[LeftOut, int]) -> str:
    """What was left out, in words, such as "2 merges and 1 revert"; empty when nothing was."""
    parts = [
        f"{count} {LEFT_OUT_WORDS[reason][count != 1]}"
        for reason in LEFT_OUT_WORDS
        if (count := left_out.get(reason, 0))
    ]
    if len(parts) <= 1:
        return "".join(parts)
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def made_by(made: Authorship) -> str:
    """Who made an item, in words without digits (FR-GH-5)."""
    helpers = ["another person"] if made.coauthored else []
    if made.maker == "agent" and made.agent is not None:
        with_others = f", with {helpers[0]}" if helpers else ""
        return f"By {made.agent}, for you{with_others}"
    if made.agent is not None:
        helpers.append(made.agent)
    return "By you" + (f", with {' and '.join(helpers)}" if helpers else "")


# --- cleaning (FR-GH-5) ---

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_CODE_BLOCK = re.compile(r"^\s*(```|~~~).*?^\s*\1[^\n]*$", re.DOTALL | re.MULTILINE)
_TRAILER = re.compile(
    r"^\s*(?:[a-z]+(?:-[a-z]+)*-by|change-id|[a-z]+-session|cc):\s.*$",
    re.IGNORECASE | re.MULTILINE,
)
_CHECKLIST = re.compile(r"^\s*[-*+]\s+\[[ xX]\].*$", re.MULTILINE)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_CLOSES = re.compile(
    r"\b(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)\s+(?:[\w.-]+/[\w.-]+)?#\d+\b\.?", re.IGNORECASE
)
_REFERENCE = re.compile(r"(?:[\w.-]+/[\w.-]+)?#\d+\b|\bGH-\d+\b")
_MENTION = re.compile(r"(?<![\w.@])@[A-Za-z0-9][A-Za-z0-9-]*(?:\[bot\])?")
_HASH = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")
_EMOJI = re.compile("[\U0001f000-\U0001faff☀-➿️‍]")
_WORD = re.compile(r"[A-Za-z0-9]+")
_EMPTY_BRACKETS = re.compile(r"\(\s*\)|\[\s*\]")
_SPACE_BEFORE_MARK = re.compile(r"[ \t]+([.,;:!?])")
_SPACES = re.compile(r"[ \t]{2,}")
_BLANK_LINES = re.compile(r"\n{3,}")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_DROPPED_SECTION = re.compile(
    r"^(?:road\s*map|to-?dos?|planned|plans|future\b.*|next steps|wish\s*list|"
    r"acknowledge?ments?|credits|thanks|contributors|contributing|sponsors?|licen[cs]e|"
    r"change\s*log)\b",
    re.IGNORECASE,
)
_SQUASHED = re.compile(r"\(#(\d+)\)\s*$")
_REVERTS_COMMIT = re.compile(r"this reverts commit ([0-9a-f]{7,40})", re.IGNORECASE)
_REVERTS_PULL = re.compile(r"\breverts (?:[\w.-]+/[\w.-]+)?#(\d+)", re.IGNORECASE)


def clean(text: str, *, max_chars: int, leftovers: bool = True) -> str:
    """Text from GitHub with what FR-GH-5 lists removed, in ASCII, cut to `max_chars`. With
    `leftovers`, a line that is only what's left around something removed, such as "Notes:"
    before an address, goes too; titles keep theirs."""
    for pattern in (_HTML_COMMENT, _CODE_BLOCK, _TRAILER, _CHECKLIST):
        text = pattern.sub("", text)
    lines: list[str] = []
    for line in text.split("\n"):
        if any(agent.footer is not None and agent.footer.search(line) for agent in AGENTS):
            continue
        scrubbed = _scrubbed(line)
        if leftovers and scrubbed != line and len(_WORD.findall(scrubbed)) < LEFTOVER_WORDS:
            continue
        lines.append(scrubbed)
    text = plain("\n".join(lines))
    text = _EMPTY_BRACKETS.sub("", text)
    text = _SPACE_BEFORE_MARK.sub(r"\1", text)
    text = _SPACES.sub(" ", text)
    # A removed reference can leave a line starting with ":", as in "#12: fix it".
    lines = [line.strip().lstrip(",;:").strip() for line in text.split("\n")]
    text = _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
    return _shortened(text, max_chars)


def _scrubbed(line: str) -> str:
    line = _IMAGE.sub("", line)
    line = _HTML_TAG.sub("", line)
    line = _LINK.sub(r"\1", line)
    for pattern in (_URL, _EMAIL, _CLOSES, _REFERENCE, _MENTION, _HASH, _EMOJI):
        line = pattern.sub("", line)
    return line


def clean_readme(text: str) -> str:
    """The README without its code blocks and the sections FR-GH-5 drops, nor headings left
    with nothing under them, then cleaned."""
    kept: list[str] = []
    dropping: int | None = None
    for line in _CODE_BLOCK.sub("", text).split("\n"):
        if heading := _HEADING.match(line):
            level = len(heading[1])
            if dropping is not None and level <= dropping:
                dropping = None
            if dropping is None and _DROPPED_SECTION.match(heading[2]):
                dropping = level
        if dropping is None:
            kept.append(line)
    return clean("\n".join(_without_empty_sections(kept)), max_chars=README_CHARS)


def _without_empty_sections(lines: Sequence[str]) -> list[str]:
    kept: list[str] = []
    for index, line in enumerate(lines):
        if heading := _HEADING.match(line):
            following = next((later for later in lines[index + 1 :] if later.strip()), None)
            below = None if following is None else _HEADING.match(following)
            if following is None or (below is not None and len(below[1]) <= len(heading[1])):
                continue
        kept.append(line)
    return kept


def _shortened(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 3].rsplit(" ", 1)[0].rstrip(" .,;:")
    return f"{cut}..."


# --- items ---


def _pull_item(read: PullHistory, made: Authorship, owner: Owner) -> Item:
    body = clean(read.pull.body or "", max_chars=PULL_CHARS)
    title = clean(read.pull.title, max_chars=COMMIT_CHARS, leftovers=False)
    titles = _commit_titles(read.commits, owner, exclude=title)
    if titles:
        listed = "\n".join(f"- {line}" for line in titles)
        body = _shortened(f"{body}\n\nCommits:\n{listed}".strip(), PULL_CHARS)
    return Item(
        label="",
        kind="pull",
        ref=str(read.pull.number),
        url=read.pull.html_url,
        at=read.pull.merged_at,
        title=title,
        body=body,
        made_by=made_by(made),
        agent=made.agent,
        coauthored=made.coauthored,
    )


def _commit_titles(commits: Sequence[Commit], owner: Owner, *, exclude: str) -> list[str]:
    """The cleaned titles of a pull request's commits made by the owner or an agent. Other
    people's and bots' commits aren't listed, so they can't back a fact."""
    titles: list[str] = []
    for commit in commits:
        if commit.is_merge or commit_authorship(commit, owner).maker in ("person", "bot"):
            continue
        title = clean(commit.message.split("\n", 1)[0], max_chars=COMMIT_CHARS, leftovers=False)
        if title and title != exclude and title not in titles:
            titles.append(title)
    return titles[:PULL_COMMIT_TITLES]


def _commit_item(commit: Commit, made: Authorship) -> Item:
    title, _, body = commit.message.partition("\n")
    return Item(
        label="",
        kind="commit",
        ref=commit.sha,
        url=commit.html_url,
        at=commit.authored_at,
        title=clean(title, max_chars=COMMIT_CHARS, leftovers=False),
        body=clean(body, max_chars=COMMIT_CHARS),
        made_by=made_by(made),
        agent=made.agent,
        coauthored=made.coauthored,
    )


def _about_item(history: History, items: Sequence[Item]) -> Item | None:
    """What the project is, from an owned repository; None for one with no history at all."""
    parts: list[str] = []
    if history.repository.description:
        parts.append(clean(history.repository.description, max_chars=DESCRIPTION_CHARS))
    if history.languages:
        parts.append(f"Languages: {', '.join(history.languages)}.")
    if history.readme:
        parts.append(f"README:\n{clean_readme(history.readme)}")
    when = history.repository.pushed_at or max((item.at for item in items), default=None)
    if when is None:
        return None
    return Item(
        label="about",
        kind="about",
        ref="",
        url=history.repository.html_url,
        at=when,
        title="About the repository.",
        body="\n\n".join(part for part in parts if part),
        made_by="",
    )


def _labelled(items: list[Item]) -> list[Item]:
    """The items oldest first, with labels: `pr1`, `pr2`... and `c1`, `c2`..."""
    counts: Counter[str] = Counter()
    labelled: list[Item] = []
    for item in sorted(items, key=lambda item: item.at):
        prefix = "pr" if item.kind == "pull" else "c"
        counts[prefix] += 1
        labelled.append(_with_label(item, f"{prefix}{counts[prefix]}"))
    return labelled


def _with_label(item: Item, label: str) -> Item:
    return Item(
        label=label,
        kind=item.kind,
        ref=item.ref,
        url=item.url,
        at=item.at,
        title=item.title,
        body=item.body,
        made_by=item.made_by,
        agent=item.agent,
        coauthored=item.coauthored,
    )


def _windows(items: Sequence[Item], max_items: int, max_chars: int) -> tuple[tuple[Item, ...], ...]:
    windows: list[tuple[Item, ...]] = []
    current: list[Item] = []
    size = 0
    for item in items:
        length = len(item.render())
        if current and (len(current) >= max_items or size + length > max_chars):
            windows.append(tuple(current))
            current, size = [], 0
        current.append(item)
        size += length
    if current:
        windows.append(tuple(current))
    return tuple(windows)


# --- what's left out ---


def _covered(
    commit: Commit, in_pulls: set[str], copies: set[tuple[str, datetime]], numbers: set[int]
) -> bool:
    """Whether a commit is part of a pull request that was read: one of its commits, a copy a
    rebase merge made, or the squash commit that ends with its number."""
    if commit.sha in in_pulls or _copy_key(commit) in copies:
        return True
    squash = _SQUASHED.search(commit.message.split("\n", 1)[0])
    return squash is not None and int(squash[1]) in numbers


def _is_revert(title: str) -> bool:
    return title.startswith('Revert "')


def _reverted_pulls(pull: Pull) -> list[int]:
    if not _is_revert(pull.title):
        return []
    return [int(number) for number in _REVERTS_PULL.findall(pull.body or "")]


def _reverted_commits(commit: Commit) -> list[str]:
    if not _is_revert(commit.message):
        return []
    return [sha.lower() for sha in _REVERTS_COMMIT.findall(commit.message)]


def _copy_key(commit: Commit) -> tuple[str, datetime]:
    return (commit.message.split("\n", 1)[0], commit.authored_at)
