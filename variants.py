"""Prompt templates: "{daisies|roses|tulips}" stands for one of the options.

Like catalog.py and gallery.py, all functions here are pure: text in, text out.
The random generator is passed in, so tests (and --variant-seed) get repeatable results.
"""

import itertools
import math
import random
import re

# A placeholder is {...} with at least one | inside. {hello} or JSON-like text stays plain text.
PLACEHOLDER = re.compile(r"\{([^{}]*\|[^{}]*)\}")


def slots(template: str) -> list[list[str]]:
    """The options of every placeholder, in order: [["daisies", "roses"], ["white", "black"]]."""
    return [[option.strip() for option in match.group(1).split("|")] for match in PLACEHOLDER.finditer(template)]


def count(template: str) -> int:
    """How many different prompts the template can produce (1 without placeholders)."""
    # math.prod multiplies all numbers of a list: 12 flowers x 12 colours = 144.
    return math.prod(len(options) for options in slots(template))


def fill(template: str, choices: list[str]) -> str:
    """Replaces the placeholders one after another with the given choices."""
    remaining = iter(choices)
    # re.sub can take a function: it is called for each match and returns the replacement.
    return PLACEHOLDER.sub(lambda match: next(remaining), template)


def pick(template: str, rng: random.Random) -> tuple[str, list[str]]:
    """One random combination: (finished prompt, chosen options)."""
    choices = [rng.choice(options) for options in slots(template)]
    return fill(template, choices), choices


def combination(options: list[list[str]], index: int) -> list[str]:
    """The index-th combination, counted like a mixed-base number (no need to list all of them)."""
    choices = []
    for slot in reversed(options):
        # divmod gives quotient and remainder in one step: divmod(7, 3) == (2, 1).
        index, position = divmod(index, len(slot))
        choices.append(slot[position])
    return list(reversed(choices))


def sample(template: str, number: int | None, rng: random.Random) -> list[tuple[str, list[str]]]:
    """number different combinations at random, or all of them for number=None."""
    options = slots(template)
    total = count(template)
    if number is None:
        # itertools.product yields every combination: (daisies, white), (daisies, black), ...
        return [(fill(template, list(choices)), list(choices)) for choices in itertools.product(*options)]
    if number > total:
        raise ValueError(f"The prompt has only {total} different combinations, not {number}.")
    # rng.sample draws distinct numbers, so no combination comes twice.
    return [
        (fill(template, choices), choices)
        for choices in (combination(options, i) for i in rng.sample(range(total), number))
    ]
