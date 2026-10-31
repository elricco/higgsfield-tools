"""Tests for variants.py: prompt templates with {a|b|c} placeholders."""

import random

import pytest

import variants

TEMPLATE = "a figure of {daisies|roses} in a {white|black|mint} jacket"


def test_slots_lists_the_options_in_order():
    assert variants.slots(TEMPLATE) == [["daisies", "roses"], ["white", "black", "mint"]]


def test_braces_without_a_bar_are_plain_text():
    # Only {a|b} is a placeholder; {single} and JSON-like text stay as they are.
    assert variants.slots('a sign that says {hello} and {"k": 1}') == []
    assert variants.count("a sign that says {hello}") == 1


def test_options_are_trimmed_and_may_be_phrases():
    assert variants.slots("{ white and yellow daisies | bright sunflowers }") == [
        ["white and yellow daisies", "bright sunflowers"]
    ]


def test_count_multiplies_the_options():
    assert variants.count(TEMPLATE) == 6
    assert variants.count("no placeholders") == 1


def test_fill_replaces_each_placeholder_in_order():
    assert variants.fill(TEMPLATE, ["roses", "mint"]) == "a figure of roses in a mint jacket"


def test_pick_is_repeatable_with_the_same_seed():
    first = variants.pick(TEMPLATE, random.Random(42))
    again = variants.pick(TEMPLATE, random.Random(42))
    assert first == again
    prompt, choices = first
    assert prompt == variants.fill(TEMPLATE, choices)


def test_pick_without_placeholders_returns_the_prompt():
    assert variants.pick("a lake", random.Random(1)) == ("a lake", [])


def test_sample_gives_different_combinations():
    picked = variants.sample(TEMPLATE, 6, random.Random(7))
    assert len(picked) == 6
    assert len({tuple(choices) for _, choices in picked}) == 6


def test_sample_all():
    picked = variants.sample(TEMPLATE, None, random.Random(7))
    assert sorted(tuple(choices) for _, choices in picked) == sorted(
        (flower, colour) for flower in ("daisies", "roses") for colour in ("white", "black", "mint")
    )


def test_sample_more_than_possible_is_an_error():
    with pytest.raises(ValueError, match="only 6 different combinations"):
        variants.sample(TEMPLATE, 7, random.Random(1))
