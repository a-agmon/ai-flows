"""Prompt-file resolution: nested folders are allowed, traversal is not."""

import pytest

from app.errors import ConfigError
from app.graph.nodes import message_text, resolve_prompt_path
from app.settings import settings


def test_nested_prompt_resolves_inside_prompts_dir():
    # A prompt organised in a sub-folder resolves to a real file under the dir.
    path = resolve_prompt_path("n", "support/classify.md")
    assert path.is_file()
    assert settings.prompts_dir.resolve() in path.parents


def test_traversal_outside_prompts_dir_is_rejected():
    with pytest.raises(ConfigError, match="outside the prompts directory"):
        resolve_prompt_path("n", "../settings.py")


def test_absolute_prompt_path_is_rejected():
    with pytest.raises(ConfigError, match="outside the prompts directory"):
        resolve_prompt_path("n", "/etc/passwd")


def test_message_text_handles_str_and_blocks():
    class Msg:
        def __init__(self, content):
            self.content = content

    assert message_text(Msg("hello")) == "hello"
    # Responses API / multimodal shape: a list of content blocks.
    blocks = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
    assert message_text(Msg(blocks)) == "ab"
