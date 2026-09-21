"""The wall between unpaid reach and advertising, asserted from both sides.

This is the one part of the commerce discovery engine whose failure mode is
legal rather than cosmetic, and the failure is completely silent: no exception,
no wrong-looking screen, just a number that is wrong in a direction that
flatters us, or a label that claims a commercial relationship nobody entered.

Two directions, and they break differently:

* **Organic labelled as paid.** An unpaid recommendation wearing "Sponsored" is
  a false advertising disclosure. Nothing in the system notices.
* **Paid accounted as organic.** A billable impression written to the unpaid
  tables goes unbilled *and* inflates the free-reach number sellers are shown.
  Both halves look plausible forever.

So the defaults are asserted by *direction*, not merely by value: an
unrecognised class must fall toward the recommendation label and away from the
advertising one, and must be refused entry to this package's event tables
rather than absorbed.
"""

import pytest

from services.commerce_discovery import promotion


class TestNormalize:
    def test_recognises_the_three_classes(self):
        assert promotion.normalize("organic") == promotion.ORGANIC
        assert promotion.normalize("house") == promotion.HOUSE
        assert promotion.normalize("paid") == promotion.PAID

    def test_tolerates_casing_and_whitespace_from_the_wire(self):
        assert promotion.normalize("  ORGANIC ") == promotion.ORGANIC
        assert promotion.normalize("House") == promotion.HOUSE

    @pytest.mark.parametrize(
        "value", ["", None, "sponsored", "ad", "free", "organic_v2", 0, [], {"a": 1}]
    )
    def test_refuses_to_guess_at_an_unknown_class(self, value):
        # Empty, never ORGANIC. A default here is precisely the bug: a malformed
        # or hostile class name would be silently accounted as free reach.
        assert promotion.normalize(value) == ""

    def test_is_unpaid_covers_exactly_the_two_this_package_serves(self):
        assert promotion.is_unpaid("organic") is True
        assert promotion.is_unpaid("house") is True
        assert promotion.is_unpaid("paid") is False
        assert promotion.is_unpaid("nonsense") is False


class TestAssertUnpaid:
    def test_admits_the_two_unpaid_classes(self):
        assert promotion.assert_unpaid("organic") == promotion.ORGANIC
        assert promotion.assert_unpaid("house") == promotion.HOUSE

    def test_refuses_a_paid_placement_with_a_reason_an_operator_can_act_on(self):
        with pytest.raises(promotion.PromotionClassError) as excinfo:
            promotion.assert_unpaid("paid")
        # The message has to say where the event *should* have gone, because the
        # person reading it in a log is looking at a billing gap, not a typo.
        assert "business_os/advertising" in str(excinfo.value)

    @pytest.mark.parametrize("value", ["", None, "sponsored", "organic "[:-1] + "x"])
    def test_refuses_an_unknown_class_rather_than_absorbing_it(self, value):
        with pytest.raises(promotion.PromotionClassError):
            promotion.assert_unpaid(value)


class TestLabelKey:
    def test_labels_each_class_truthfully(self):
        assert promotion.label_key("organic") == "commerce:discovery.label.recommended"
        assert promotion.label_key("house") == "commerce:discovery.label.trending"
        assert promotion.label_key("paid") == "commerce:discovery.label.sponsored"

    @pytest.mark.parametrize("value", ["", None, "unknown", "paidx", 7])
    def test_an_unrecognised_class_never_falls_toward_the_advertising_label(self, value):
        # The direction is the assertion. Mislabelling an unknown placement as a
        # recommendation understates a commercial relationship's absence;
        # defaulting to "Sponsored" would claim a payment that never happened.
        key = promotion.label_key(value)
        assert key == "commerce:discovery.label.recommended"
        assert "sponsored" not in key

    def test_sponsored_appears_against_exactly_one_class(self):
        sponsored = [
            name for name, key in promotion.LABEL_KEYS.items() if key.endswith(".sponsored")
        ]
        assert sponsored == [promotion.PAID]

    def test_every_label_is_a_key_rather_than_english(self):
        # Hardcoded copy fails the mobile i18n gate, and the label is the part a
        # regulator reads first — it has to exist in every locale.
        for key in promotion.LABEL_KEYS.values():
            assert key.startswith("commerce:discovery.label.")
            assert " " not in key


class TestClassSets:
    def test_paid_is_named_here_but_never_servable(self):
        assert promotion.PAID in promotion.ALL_CLASSES
        assert promotion.PAID not in promotion.UNPAID_CLASSES

    def test_unpaid_is_a_strict_subset_with_nothing_invented(self):
        assert promotion.UNPAID_CLASSES < promotion.ALL_CLASSES
        assert promotion.ALL_CLASSES - promotion.UNPAID_CLASSES == {promotion.PAID}
