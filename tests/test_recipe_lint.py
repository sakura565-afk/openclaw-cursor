"""Contract tests for the reference-state negative-prompt linter."""

from __future__ import annotations

import pytest

from scripts.recipe_lint import format_contradictions, lint_recipe


def rules(prompt: str, negative: str, reference: str = "") -> set[str]:
    return {
        finding.rule
        for finding in lint_recipe(prompt, negative, reference)
    }


def test_historical_panties_off_blocks_fingers_under_fabric() -> None:
    findings = lint_recipe(
        prompt=(
            "She slides her hand in her panties, her fingers slide under the fabric. "
            "White briefs still on, panties visible."
        ),
        negative="panties off, lingerie removed",
        reference_description="A woman wearing a white bralette and white high-cut briefs.",
    )

    finding = next(f for f in findings if f.rule == "hand_under_fabric_vs_garment_removal")
    assert "panties off" in finding.terms
    assert "fingers slide under the fabric" in finding.terms
    assert "remove" in finding.suggestion.lower()
    assert "panties off" in str(finding)


@pytest.mark.parametrize("negative_term", ["topless", "nude", "naked", "bare breasts"])
def test_bare_reference_conflicts_with_negative_that_requires_covered_chest(
    negative_term: str,
) -> None:
    found = rules(
        prompt="She caresses her bare breasts and her nipples are visible.",
        negative=negative_term,
        reference="A kneeling topless woman with no clothing on her chest.",
    )
    assert "negative_requires_covered_but_reference_is_bare" in found


def test_nude_negative_can_block_partial_nudity_even_when_panties_stay_on() -> None:
    findings = lint_recipe(
        prompt="She is topless with bare breasts, but her white briefs still stay on.",
        negative="nude",
        reference_description="A topless woman wearing white high-cut briefs.",
    )

    assert "blanket_nude_negative_blocks_intended_partial_nudity" in {
        f.rule for f in findings
    }
    assert any("not fully naked" in f.message for f in findings)


def test_negative_that_forbids_hand_in_panties_conflicts_with_requested_action() -> None:
    found = rules(
        prompt="She slides her hand in her panties and rubs under the fabric.",
        negative="hand in panties, hand under fabric",
        reference="She is wearing white briefs.",
    )
    assert "negative_forbids_requested_hand_under_fabric" in found


def test_negative_that_requires_bare_chest_conflicts_with_bra_staying_on() -> None:
    found = rules(
        prompt="Her black lace bra stays on and her breasts remain covered.",
        negative="bra, clothing on chest, covered breasts",
        reference="A woman wearing a black lace bra.",
    )
    assert "negative_requires_bare_but_reference_is_covered" in found


def test_negative_that_requires_uncovered_crotch_conflicts_with_closed_panties() -> None:
    found = rules(
        prompt="The panties stay on her hips and stay closed over her hand.",
        negative="fabric over the vulva, panties covering the crotch, closed crotch",
        reference="She wears dark lace panties that cover her crotch.",
    )
    assert "negative_requires_bare_but_reference_is_covered" in found


def test_garment_removal_negative_conflicts_with_requested_undressing() -> None:
    found = rules(
        prompt="The gown slips off her shoulders and she is revealed fully naked.",
        negative="gown off, dress removed, nude",
        reference="She initially wears a teal sequin gown.",
    )
    assert "negative_requires_covered_but_reference_is_bare" in found


def test_negative_garment_must_exist_in_reference() -> None:
    findings = lint_recipe(
        prompt="Her fingers rub over her underwear.",
        negative="thong off",
        reference_description="A woman in a white bralette and white high-cut briefs.",
    )

    finding = next(f for f in findings if f.rule == "garment_absent_from_reference")
    assert "thong" in finding.terms
    assert "briefs" in finding.suggestion


def test_positive_garment_lock_must_exist_in_reference() -> None:
    found = rules(
        prompt="Keep the red gown on and let the sleeves stay on her arms.",
        negative="malformed hands",
        reference="A woman in a white bralette and white briefs.",
    )
    assert "garment_absent_from_reference" in found


def test_generic_lingerie_matches_specific_reference_garments() -> None:
    findings = lint_recipe(
        prompt="Her fingers rub over the fabric.",
        negative="lingerie removed",
        reference_description="A woman in a white bralette and white high-cut briefs.",
    )
    assert not any(f.rule == "garment_absent_from_reference" for f in findings)


def test_approved_topless_negative_is_valid() -> None:
    findings = lint_recipe(
        prompt=(
            "Her hands squeeze and massage her bare breasts; her nipples are visible. "
            "No clothing appears on her chest. Her lavender tulle skirt stays on."
        ),
        negative="bra, bikini, top, shirt, clothing on chest, covered breasts",
        reference_description=(
            "A kneeling topless woman, both hands covering her breasts, wearing a sheer "
            "lavender tulle skirt over a low silvery waistband."
        ),
    )
    assert findings == []


def test_approved_under_fabric_negative_is_valid() -> None:
    findings = lint_recipe(
        prompt=(
            "Her fingers stay UNDER the lace, the fabric covers her hand and knuckles, "
            "only the shape of her hand shows beneath the fabric, the lace never tears "
            "and never opens, the panties stay on her hips and stay closed over her hand."
        ),
        negative=(
            "hand through fabric, fingers poking through lace, hand outside the panties, "
            "hand visible over the underwear, fingers outside the fabric, torn lace, open crotch"
        ),
        reference_description="A woman wearing a black lace bra and dark thong.",
    )
    assert findings == []


def test_approved_shifted_panties_negative_is_valid_despite_initially_covered_reference() -> None:
    findings = lint_recipe(
        prompt=(
            "The thong is shifted aside to one side, the fabric pulled over to one hip "
            "so the vulva is exposed and bare, and the fabric sits pushed aside on one hip."
        ),
        negative=(
            "fabric over the vulva, panties covering the crotch, closed crotch, "
            "hand through fabric, fingers poking through lace"
        ),
        reference_description="A standing woman in a black lace bra and minimal dark thong.",
    )
    assert findings == []


def test_approved_undressing_negative_is_valid_despite_initially_clothed_reference() -> None:
    findings = lint_recipe(
        prompt=(
            "The teal sequin gown slips off her shoulders and falls down to the floor, "
            "the sleeves slide down her arms, she is revealed fully naked."
        ),
        negative=(
            "gown staying on body, dress remaining on shoulders, sleeves staying on arms, "
            "fabric covering chest, sequins on body"
        ),
        reference_description="A standing woman initially wearing a teal sequin gown.",
    )
    assert findings == []


def test_nude_negative_with_fully_clothed_scene_is_not_a_contradiction() -> None:
    assert lint_recipe(
        prompt="She keeps her bra and panties on and adjusts her veil.",
        negative="nude, malformed hands",
        reference_description="A woman wearing a black lace bra and dark thong.",
    ) == []


def test_empty_reference_disables_only_reference_dependent_checks() -> None:
    findings = lint_recipe(
        prompt="Her fingers slide under the fabric.",
        negative="panties off, thong off",
        reference_description="",
    )
    assert any(f.rule == "hand_under_fabric_vs_garment_removal" for f in findings)
    assert not any(f.rule == "garment_absent_from_reference" for f in findings)


def test_formatter_lists_terms_and_suggestions() -> None:
    text = format_contradictions(lint_recipe(
        "Her fingers slide under the fabric.", "panties off", "She wears briefs."
    ))
    assert "CONTRADICTION" in text
    assert "panties off" in text
    assert "Suggestion:" in text


def test_valid_result_formatter_is_explicit() -> None:
    assert format_contradictions([]) == "OK: prompt, negative and reference state agree."
