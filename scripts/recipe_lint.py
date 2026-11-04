"""Lint H3 negative prompts against the requested action and reference state.

A negative prompt is a list of things to avoid.  Consequently ``nude`` in the
negative asks for a non-nude result, while ``bra`` asks for a bare chest.  The
linter compares those polarities with the prompt and a textual description of
the reference image.  It deliberately does not try to infer anatomy or clothing
from pixels.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Contradiction:
    """One actionable polarity conflict."""

    rule: str
    terms: tuple[str, ...]
    message: str
    suggestion: str

    def __str__(self) -> str:
        return (
            f"CONTRADICTION [{self.rule}]: {self.message}\n"
            f"  Terms: {', '.join(self.terms)}\n"
            f"  Suggestion: {self.suggestion}"
        )


@dataclass(frozen=True)
class _State:
    scope: str
    polarity: str
    term: str
    source: str


def _normalise(text: str) -> str:
    text = text.lower().replace("’", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip()


def _matches(text: str, patterns: Sequence[str]) -> list[str]:
    return [match.group(0) for pattern in patterns if (match := re.search(pattern, text))]


# A transition is important: an initially clothed reference is not a request to
# keep the garment on when the action explicitly removes it or shifts it aside.
_TRANSITION_PATTERNS = (
    r"\b(?:gown|dress|cloth|satin|robe|towel)\b[^.]{0,70}\b(?:slips? off|slides? off|falls?|falls? down|removed)\b",
    r"\b(?:slips? off|slides? off|falls?|falls? down)\b[^.]{0,70}\b(?:gown|dress|cloth|satin|robe|towel)\b",
    r"\b(?:panties|briefs|thong|underwear|fabric|lace)\b[^.]{0,70}\b(?:shifted|pushed|pulled)\s+aside\b",
    r"\b(?:shifted|pushed|pulled)\s+aside\b[^.]{0,70}\b(?:panties|briefs|thong|underwear|fabric|lace)\b",
    r"\bfabric\s+pulled\s+over\s+to\s+one\s+hip\b",
    r"\bshe\s+is\s+revealed\s+(?:fully\s+)?(?:naked|nude)\b",
    r"\bposes?\s+(?:fully\s+)?(?:naked|nude)\b",
)

_BARE_PATTERNS: dict[str, Sequence[str]] = {
    "chest": (
        r"\btopless\b",
        r"\bbare\s+(?:breasts?|chest|nipples)\b",
        r"\b(?:breasts?|nipples)\s+(?:are\s+)?(?:clearly\s+)?visible\b",
        r"\bbreasts?\s+and\s+nipples\s+(?:are\s+)?(?:clearly\s+)?visible\b",
        r"\bno\s+clothing\s+(?:appears\s+)?on\s+(?:her\s+)?chest\b",
        r"\bwithout\s+(?:a\s+)?(?:bra|bralette|top|shirt)\b",
        r"\bupper\s+body\s+(?:is\s+)?bare\b",
    ),
    "lower": (
        r"\b(?:vulva|crotch)\s+(?:is\s+)?(?:exposed|visible|bare|uncovered)\b",
        r"\b(?:exposed|visible|bare|uncovered)\s+(?:vulva|crotch)\b",
        r"\bopen\s+crotch\b",
        r"\b(?:panties|briefs|thong|underwear|fabric|lace)\b[^.]{0,60}\b(?:shifted|pushed|pulled)\s+aside\b",
        r"\bfabric\s+pulled\s+over\s+to\s+one\s+hip\b",
        r"\b(?:panties|briefs|thong|underwear)\s+(?:are\s+)?(?:off|removed)\b",
    ),
    "whole": (
        r"\b(?:fully\s+)?naked\b",
        r"\b(?:fully\s+)?nude\b",
        r"\bno\s+(?:clothing|fabric|cloth)\s+on\s+(?:her\s+)?body\b",
    ),
}

_COVERED_PATTERNS: dict[str, Sequence[str]] = {
    "chest": (
        r"\b(?:bra|bralette|bikini|top|shirt)\s+(?:still\s+)?(?:stays?|remains?)\s+on\b",
        r"\bbreasts?\s+(?:still\s+)?(?:stay|stays|remain|remains)\s+covered\b",
        r"\bcovered\s+breasts?\b",
        r"\bwearing\s+(?:a\s+)?[^,.]{0,45}\b(?:bra|bralette|bikini|top|shirt)\b",
    ),
    "lower": (
        r"\b(?:panties|briefs|thong|underwear)\s+(?:still\s+)?(?:stay|stays|remain|remains)\s+on\b",
        r"\b(?:panties|briefs|thong|underwear)\s+(?:still\s+)?visible\b",
        r"\b(?:panties|briefs|thong|underwear)\b[^.]{0,65}\bstay\s+closed\b",
        r"\b(?:panties|briefs|thong|underwear)\s+cover(?:s|ing)?\s+(?:her\s+|the\s+)?crotch\b",
        r"\bfabric\s+covers\s+her\s+hand\b",
        r"\bwearing\s+(?:a\s+)?[^,.]{0,45}\b(?:panties|briefs|thong|underwear)\b",
    ),
    "whole": (
        r"\b(?:gown|dress|cloth|satin|robe|towel)\s+(?:still\s+)?(?:stays?|remains?)\s+on\b",
        r"\bkeep\s+(?:the\s+)?[^,.]{0,45}\b(?:gown|dress|cloth|satin|robe|towel)\s+on\b",
    ),
}

# Negative terms that ask the model to KEEP a covered/non-nude state.
_NEGATIVE_REQUIRES_COVERED: dict[str, Sequence[str]] = {
    "chest": (
        r"\btopless\b",
        r"\bbare\s+(?:breasts?|chest|nipples)\b",
        r"\b(?:exposed|uncovered)\s+(?:breasts?|chest|nipples)\b",
        r"\bnipples\s+visible\b",
        r"\bbreast\s+exposure\b",
    ),
    "lower": (
        r"\b(?:panties|briefs|thong|underwear)\s+(?:off|removed)\b",
        r"\blingerie\s+(?:off|removed)\b",
        r"\b(?:exposed|bare|visible)\s+vulva\b",
        r"\bvulva\s+(?:exposed|bare|visible)\b",
        r"\b(?:open|uncovered)\s+crotch\b",
        r"\b(?:panties|briefs|thong|underwear|fabric|lace)\b[^,]{0,45}\b(?:shifted|pushed|pulled)\s+aside\b",
    ),
    "whole": (
        r"\b(?:fully\s+)?(?:nude|naked|nudity)\b",
        r"\b(?:gown|dress|cloth|satin|robe|towel)\s+(?:off|removed|falls?|falling)\b",
    ),
}

# Negative terms that ask the model to REMOVE clothing or coverage.  Each
# comma-separated item is checked independently so "hand outside the panties"
# is not misread as a request to remove the panties.
_NEGATIVE_REQUIRES_BARE: dict[str, Sequence[str]] = {
    "chest": (
        r"^(?:a\s+|the\s+)?(?:bra|bralette|bikini|top|shirt)$",
        r"\bclothing\s+on\s+(?:her\s+)?chest\b",
        r"\bcovered\s+breasts?\b",
        r"\b(?:fabric|cloth)\s+covering\s+(?:her\s+)?chest\b",
    ),
    "lower": (
        r"^(?:a\s+|the\s+)?(?:panties|briefs|thong|underwear|lingerie)$",
        r"\bfabric\s+over\s+(?:the\s+|her\s+)?vulva\b",
        r"\b(?:panties|briefs|thong|underwear)\s+covering\s+(?:the\s+|her\s+)?crotch\b",
        r"\bclosed\s+crotch\b",
    ),
    "whole": (
        r"\b(?:clothing|fabric|cloth)\s+on\s+(?:her\s+)?body\b",
        r"\b(?:gown|dress|cloth|satin|robe|towel)\s+(?:staying|remaining)\s+on\b",
        r"\bcurtains\s+covering\s+(?:her\s+)?body\b",
        r"^(?:gown|dress|cloth|satin|robe|towel)$",
        r"\bsequins?\s+on\s+(?:her\s+)?body\b",
        r"\bsequin\s+fabric\b",
    ),
}

_HAND_UNDER_PATTERNS = (
    r"\b(?:she\s+)?(?:slides?\s+)?her\s+hand\s+(?:in|inside|into|under|beneath)\s+(?:her\s+)?(?:panties|briefs|thong|underwear|fabric|lace)\b",
    r"\bhand\s+(?:in|inside|into|under|beneath)\s+(?:her\s+)?(?:panties|briefs|thong|underwear|fabric|lace)\b",
    r"\bfingers?\s+(?:stay\s+|slide\s+|slides\s+|sliding\s+)?(?:in|inside|into|under|beneath)\s+(?:her\s+|the\s+)?(?:panties|briefs|thong|underwear|fabric|lace)\b",
    r"\bfingers?\s+(?:slide|slides|sliding)\s+along\s+(?:her\s+)?vulva\b",
    r"\bfabric\s+covers\s+her\s+hand\b",
    r"\bhand\s+shows?\s+beneath\s+(?:the\s+)?fabric\b",
)

_NEGATIVE_HAND_UNDER_PATTERNS = (
    r"\bhand\s+(?:in|inside|into|under|beneath)\s+(?:her\s+|the\s+)?(?:panties|briefs|thong|underwear|fabric|lace)\b",
    r"\bfingers?\s+(?:slide\s+|slides\s+|sliding\s+)?(?:in|inside|into|under|beneath)\s+(?:her\s+|the\s+)?(?:panties|briefs|thong|underwear|fabric|lace)\b",
)

_GARMENT_REMOVAL_NEGATIVE = re.compile(
    r"\b(?:panties|briefs|thong|underwear|lingerie)\s+(?:off|removed)\b"
)

_GARMENT_ALIASES: dict[str, frozenset[str]] = {
    "bra": frozenset(("bra", "bralette")),
    "bralette": frozenset(("bra", "bralette")),
    "bikini": frozenset(("bikini",)),
    "top": frozenset(("top", "shirt")),
    "shirt": frozenset(("top", "shirt")),
    "panties": frozenset(("panties", "briefs", "thong", "underwear")),
    "briefs": frozenset(("panties", "briefs", "underwear")),
    "thong": frozenset(("thong",)),
    "underwear": frozenset(("panties", "briefs", "thong", "underwear")),
    "lingerie": frozenset(("lingerie", "bra", "bralette", "panties", "briefs", "thong", "underwear")),
    "gown": frozenset(("gown", "dress")),
    "dress": frozenset(("gown", "dress")),
    "skirt": frozenset(("skirt",)),
    "sleeves": frozenset(("sleeve", "sleeves")),
    "robe": frozenset(("robe",)),
    "towel": frozenset(("towel",)),
    "cloth": frozenset(("cloth", "satin", "drape")),
    "satin": frozenset(("cloth", "satin", "drape")),
}


def _states(text: str, source: str, patterns: dict[str, Sequence[str]], polarity: str) -> list[_State]:
    return [
        _State(scope, polarity, term, source)
        for scope, scope_patterns in patterns.items()
        for term in _matches(text, scope_patterns)
    ]


def _negative_items(negative: str) -> list[str]:
    return [item.strip() for item in _normalise(negative).split(",") if item.strip()]


def _reference_garments(reference: str) -> set[str]:
    return {
        canonical
        for canonical, aliases in _GARMENT_ALIASES.items()
        if any(re.search(rf"\b{re.escape(alias)}\b", reference) for alias in aliases)
    }


def _actual_garment_names(reference: str) -> list[str]:
    names = [
        alias
        for aliases in _GARMENT_ALIASES.values()
        for alias in aliases
        if re.search(rf"\b{re.escape(alias)}\b", reference)
    ]
    return sorted(set(names))


def _add_state_conflicts(
    findings: list[Contradiction],
    prompt: str,
    negative: str,
    reference: str,
) -> None:
    prompt_bare = _states(prompt, "prompt", _BARE_PATTERNS, "bare")
    reference_bare = _states(reference, "reference", _BARE_PATTERNS, "bare")
    prompt_covered = _states(prompt, "prompt", _COVERED_PATTERNS, "covered")
    reference_covered = _states(reference, "reference", _COVERED_PATTERNS, "covered")

    # Reference prose often lists a garment without saying "wearing".  Treat a
    # bra/lower garment as present only when no contrary bare state is stated.
    if reference and not _states(reference, "reference", {"chest": _BARE_PATTERNS["chest"]}, "bare"):
        for garment in ("bra", "bralette", "bikini"):
            if re.search(rf"\b{garment}\b", reference):
                reference_covered.append(_State("chest", "covered", garment, "reference"))
                break
    if reference and not _states(reference, "reference", {"lower": _BARE_PATTERNS["lower"]}, "bare"):
        for garment in ("panties", "briefs", "thong", "underwear"):
            if re.search(rf"\b{garment}\b", reference):
                reference_covered.append(_State("lower", "covered", garment, "reference"))
                break

    desired_bare = prompt_bare + reference_bare
    desired_covered = prompt_covered + reference_covered
    if _matches(prompt, _TRANSITION_PATTERNS):
        # The prompt's target state supersedes the initial still.  Explicit
        # prompt locks (for example "skirt stays on") remain in force.
        desired_covered = prompt_covered

    for item in _negative_items(negative):
        for scope, patterns in _NEGATIVE_REQUIRES_COVERED.items():
            for negative_term in _matches(item, patterns):
                compatible_scopes = {scope, "whole"} if scope != "whole" else {"whole", "chest", "lower"}
                for desired in desired_bare:
                    if desired.scope not in compatible_scopes:
                        continue
                    findings.append(Contradiction(
                        rule="negative_requires_covered_but_reference_is_bare",
                        terms=(negative_term, desired.term),
                        message=(
                            f"Negative '{negative_term}' suppresses the {desired.scope} exposure "
                            f"requested by {desired.source} term '{desired.term}'."
                        ),
                        suggestion=(
                            f"Remove '{negative_term}' from the negative, or replace it with a "
                            "targeted unwanted-clothing term that preserves the requested exposure."
                        ),
                    ))

        # Hand/finger negatives are about placement, not garment polarity.
        if re.search(r"\b(?:hand|hands|finger|fingers)\b", item):
            continue
        for scope, patterns in _NEGATIVE_REQUIRES_BARE.items():
            for negative_term in _matches(item, patterns):
                compatible_scopes = {scope, "whole"} if scope != "whole" else {"whole", "chest", "lower"}
                for desired in desired_covered:
                    if desired.scope not in compatible_scopes:
                        continue
                    findings.append(Contradiction(
                        rule="negative_requires_bare_but_reference_is_covered",
                        terms=(negative_term, desired.term),
                        message=(
                            f"Negative '{negative_term}' removes coverage that {desired.source} "
                            f"term '{desired.term}' explicitly preserves."
                        ),
                        suggestion=(
                            f"Remove '{negative_term}' from the negative, or change the prompt/reference "
                            "lock if the garment is actually meant to come off."
                        ),
                    ))


def _add_hand_conflicts(findings: list[Contradiction], prompt: str, negative: str) -> None:
    requested = _matches(prompt, _HAND_UNDER_PATTERNS)
    if not requested:
        return
    # Prefer the most concrete finger/fabric phrase in diagnostics; it is the
    # exact wording involved in the historical MASTURB failure.
    requested.sort(key=lambda term: 0 if "finger" in term else 1)
    for item in _negative_items(negative):
        direct = _matches(item, _NEGATIVE_HAND_UNDER_PATTERNS)
        for negative_term in direct:
            findings.append(Contradiction(
                rule="negative_forbids_requested_hand_under_fabric",
                terms=(negative_term, requested[0]),
                message=(
                    f"Negative '{negative_term}' forbids the same hand placement requested by "
                    f"'{requested[0]}'."
                ),
                suggestion=(
                    f"Remove '{negative_term}'. To prevent intersection artefacts, use "
                    "'hand through fabric' or 'fingers poking through lace' instead."
                ),
            ))
        removal = _GARMENT_REMOVAL_NEGATIVE.search(item)
        if removal:
            findings.append(Contradiction(
                rule="hand_under_fabric_vs_garment_removal",
                terms=(removal.group(0), requested[0]),
                message=(
                    f"Negative '{removal.group(0)}' pulls toward keeping the garment boundary closed "
                    f"while the prompt asks for '{requested[0]}'. This is the conflict that parked "
                    "the hand at the panties edge in the approved-scene history."
                ),
                suggestion=(
                    f"Remove '{removal.group(0)}' from the negative. Keep the underwear state with "
                    "positive prompt locks, and use 'hand through fabric' only if crossing the "
                    "fabric is the defect to suppress."
                ),
            ))


def _add_partial_nudity_note(findings: list[Contradiction], prompt: str, negative: str, reference: str) -> None:
    if not any(re.fullmatch(r"(?:fully\s+)?(?:nude|naked|nudity)", item) for item in _negative_items(negative)):
        return
    combined_bare = _states(prompt, "prompt", _BARE_PATTERNS, "bare") + _states(
        reference, "reference", _BARE_PATTERNS, "bare"
    )
    covered = _states(prompt, "prompt", _COVERED_PATTERNS, "covered") + _states(
        reference, "reference", _COVERED_PATTERNS, "covered"
    )
    if not covered or not any(state.scope in {"chest", "lower"} for state in combined_bare):
        return
    bare = next(state for state in combined_bare if state.scope in {"chest", "lower"})
    negative_term = next(
        item for item in _negative_items(negative)
        if re.fullmatch(r"(?:fully\s+)?(?:nude|naked|nudity)", item)
    )
    findings.append(Contradiction(
        rule="blanket_nude_negative_blocks_intended_partial_nudity",
        terms=(negative_term, bare.term, covered[0].term),
        message=(
            f"Negative '{negative_term}' reads as 'not fully naked', but is also a blanket nudity "
            f"suppression: it can remove the intended partial exposure '{bare.term}' even though "
            f"'{covered[0].term}' keeps underwear or another garment on."
        ),
        suggestion=(
            f"Remove blanket '{negative_term}' and name only unwanted coverage (for example "
            "'clothing on chest' for a topless scene) or the specific garment that must stay off."
        ),
    ))


def _add_garment_checks(findings: list[Contradiction], prompt: str, negative: str, reference: str) -> None:
    if not reference:
        return
    present = _reference_garments(reference)
    prompt_present = _reference_garments(prompt)
    actual = _actual_garment_names(reference)
    actual_text = ", ".join(actual) if actual else "no named garment"

    checked: set[tuple[str, str]] = set()
    for item in _negative_items(negative):
        if not re.search(r"\b(?:off|removed|staying|remaining|stays?|covering)\b", item):
            continue
        for canonical, aliases in _GARMENT_ALIASES.items():
            if not re.search(rf"\b{canonical}\b", item) or canonical in present or canonical in prompt_present:
                continue
            key = ("negative", canonical)
            if key in checked:
                continue
            checked.add(key)
            findings.append(Contradiction(
                rule="garment_absent_from_reference",
                terms=(canonical, item),
                message=(
                    f"Negative garment term '{canonical}' has no matching garment in the reference; "
                    f"the reference names: {actual_text}."
                ),
                suggestion=(
                    f"Remove '{item}', or replace '{canonical}' with the actual reference garment "
                    f"({actual_text}) if that is what the negative was meant to control."
                ),
            ))

    for canonical in _GARMENT_ALIASES:
        if canonical in present:
            continue
        locked = (
            re.search(rf"\bkeep\b[^.]{{0,60}}\b{canonical}\b[^.]{{0,20}}\bon\b", prompt)
            or re.search(rf"\b{canonical}\b[^.]{{0,45}}\b(?:stays?|remains?)\s+on\b", prompt)
            or re.search(rf"\b{canonical}\s+still\s+on\b", prompt)
        )
        if locked:
            findings.append(Contradiction(
                rule="garment_absent_from_reference",
                terms=(canonical, locked.group(0)),
                message=(
                    f"Prompt locks garment '{canonical}', but the reference does not contain it; "
                    f"the reference names: {actual_text}."
                ),
                suggestion=(
                    f"Remove the lock for '{canonical}' or replace it with an actual reference "
                    f"garment ({actual_text})."
                ),
            ))


def lint_recipe(
    prompt: str,
    negative: str,
    reference_description: str = "",
) -> list[Contradiction]:
    """Return every detected contradiction; an empty list means no known conflict.

    The function is deterministic and side-effect free.  It checks text only and
    therefore never claims to have inspected the reference image itself.
    """

    prompt_n = _normalise(prompt)
    negative_n = _normalise(negative)
    reference_n = _normalise(reference_description)
    findings: list[Contradiction] = []
    _add_state_conflicts(findings, prompt_n, negative_n, reference_n)
    _add_hand_conflicts(findings, prompt_n, negative_n)
    _add_partial_nudity_note(findings, prompt_n, negative_n, reference_n)
    _add_garment_checks(findings, prompt_n, negative_n, reference_n)

    # The same phrase can be reached through a generic and a scope-specific
    # pattern.  Keep the output useful by removing exact duplicates only.
    unique: list[Contradiction] = []
    seen: set[tuple[str, tuple[str, ...], str]] = set()
    for finding in findings:
        key = (finding.rule, finding.terms, finding.message)
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    return unique


def format_contradictions(findings: Iterable[Contradiction]) -> str:
    items = list(findings)
    if not items:
        return "OK: prompt, negative and reference state agree."
    return "\n".join(str(finding) for finding in items)


def _scene_from_registry(scene_id: str) -> dict:
    path = Path(__file__).resolve().parent / "recipes" / "registry.json"
    registry = json.loads(path.read_text(encoding="utf-8"))
    for scene in registry["scenes"]:
        if scene["id"] == scene_id:
            return scene
    raise ValueError(f"unknown scene: {scene_id}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", help="lint a scene from scripts/recipes/registry.json")
    parser.add_argument("--prompt", default="", help="positive prompt text")
    parser.add_argument("--prompt-file", help="UTF-8 prompt file; overrides --prompt")
    parser.add_argument("--negative", default="", help="comma-separated negative prompt")
    parser.add_argument(
        "--reference", "--reference-description", dest="reference", default="",
        help="textual description of the reference state",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.scene:
            scene = _scene_from_registry(args.scene)
            prompt = scene["prompt"]
            negative = scene["negative"]
            reference = scene["reference_description"]
        else:
            prompt = Path(args.prompt_file).read_text(encoding="utf-8") if args.prompt_file else args.prompt
            if not prompt.strip():
                raise ValueError("provide --scene, --prompt or --prompt-file")
            negative = args.negative
            reference = args.reference
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    findings = lint_recipe(prompt, negative, reference)
    if args.json:
        print(json.dumps([asdict(finding) for finding in findings], ensure_ascii=False, indent=2))
    else:
        print(format_contradictions(findings))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
