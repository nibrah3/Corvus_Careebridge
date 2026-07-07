"""
Unit tests for careerbridge/gemini_vision.py

Tests Gemini API key loading, option matching, and live annotate_image call.
Run: C:\Python314\python.exe -m pytest E:\Corvus_Careebridge\tests\test_gemini_vision.py -v
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, r"E:\Corvus_Careebridge")

import pytest


def test_load_gemini_key_reads_env_file():
    from careerbridge.gemini_vision import _load_gemini_key
    key = _load_gemini_key()
    assert key, "GEMINI_API_KEY must be set in .env"
    assert key.startswith("AQ."), f"Expected AQ. key, got: {key[:8]}..."


def test_match_option_exact():
    from careerbridge.gemini_vision import _match_option
    opts = ["Cat", "Dog", "Bird"]
    assert _match_option("Cat", opts) == "Cat"
    assert _match_option("cat", opts) == "Cat"
    assert _match_option("DOG", opts) == "Dog"


def test_match_option_number():
    from careerbridge.gemini_vision import _match_option
    opts = ["Cat", "Dog", "Bird"]
    assert _match_option("1", opts) == "Cat"
    assert _match_option("2.", opts) == "Dog"


def test_match_option_substring():
    from careerbridge.gemini_vision import _match_option
    opts = ["Cat", "Dog", "Bird"]
    assert _match_option("It is a cat", opts) == "Cat"
    assert _match_option("Looks like a dog", opts) == "Dog"


def test_match_option_word_overlap():
    from careerbridge.gemini_vision import _match_option
    opts = ["Smooth Spiral Galaxy", "Elliptical Galaxy", "Merging Galaxy"]
    result = _match_option("The image shows a spiral galaxy", opts)
    assert result == "Smooth Spiral Galaxy"


def test_annotate_image_live():
    """Live Gemini API call — requires network and valid GEMINI_API_KEY."""
    from careerbridge.gemini_vision import annotate_image
    image_url = (
        "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3a/"
        "Cat03.jpg/1200px-Cat03.jpg"
    )
    options = ["Cat", "Dog", "Bird", "Fish"]
    result = annotate_image(image_url, "What type of animal is in this image?", options)
    assert result is not None, "Gemini returned None — check API key and quota"
    assert result == "Cat", f"Expected 'Cat', got {result!r}"


def test_build_prompt_structure():
    from careerbridge.gemini_vision import _build_prompt
    prompt = _build_prompt("Is this a cat?", ["Yes", "No"], "Extra context here.")
    assert "Is this a cat?" in prompt
    assert "Yes" in prompt
    assert "No" in prompt
    assert "Extra context here." in prompt
    assert "Reply with ONLY" in prompt
