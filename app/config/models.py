"""Pydantic models describing the YAML flow schema.

These models are the single source of truth for what a flow YAML may contain.
``extra="forbid"`` is set everywhere so that typos in a config (for example
``promt_file`` instead of ``prompt_file``) fail loudly at startup rather than
being silently ignored -- important because flows are authored by people who do
not read the runtime code.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Operators supported by a single condition object. Exactly one must be set.
_CONDITION_OPERATORS = ("equals", "not_equals", "exists", "contains", "in_")


class _Strict(BaseModel):
    """Base model that rejects unknown keys to catch config typos early."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ConditionConfig(_Strict):
    """A single, side-effect-free comparison against a value in state.

    Used by both ``when`` (run only if true) and ``end_if`` (stop after a stage
    if true). Conditions never execute Python -- they only inspect values that
    earlier nodes have already written to state.
    """

    field: str
    equals: Any = None
    not_equals: Any = None
    exists: bool | None = None
    contains: Any = None
    # ``in`` is a Python keyword, so expose it under the alias on the YAML side.
    in_: list[Any] | None = Field(default=None, alias="in")
    # Only meaningful for ``end_if``: surfaced as the completion reason.
    reason: str | None = None

    @model_validator(mode="after")
    def _exactly_one_operator(self) -> ConditionConfig:
        present = [op for op in _CONDITION_OPERATORS if op in self.model_fields_set]
        if len(present) != 1:
            raise ValueError(
                "a condition must define exactly one operator "
                f"({', '.join(_CONDITION_OPERATORS)}); got {present or 'none'}"
            )
        return self


class InputSpec(_Strict):
    """Declares one request input field for a flow."""

    type: str = "string"
    required: bool = False
    default: Any = None


class BaseNodeConfig(_Strict):
    """Fields shared by every node type."""

    id: str
    type: str
    when: ConditionConfig | None = None
    output_key: str | None = None
    merge_output: bool = False


class LLMNodeConfig(BaseNodeConfig):
    """A node that renders a prompt over state and calls an LLM."""

    type: Literal["llm"]
    model: str
    temperature: float = 0.0
    provider: str = "openai"
    prompt: str | None = None
    prompt_file: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> LLMNodeConfig:
        if bool(self.prompt) == bool(self.prompt_file):
            raise ValueError(
                f"llm node '{self.id}' must set exactly one of 'prompt' or 'prompt_file'"
            )
        if not self.output_key:
            raise ValueError(f"llm node '{self.id}' must set 'output_key'")
        return self


class ModuleNodeConfig(BaseNodeConfig):
    """A node that calls a user-defined Python function from ``app.modules``."""

    type: Literal["module"]
    module: str
    function: str
    # Maps the function's logical input names to state keys.
    inputs: dict[str, str] = Field(default_factory=dict)
    # Static per-node configuration, passed through to the function.
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> ModuleNodeConfig:
        if not self.output_key and not self.merge_output:
            raise ValueError(
                f"module node '{self.id}' must set 'output_key' or 'merge_output: true'"
            )
        return self


# Discriminated union: the ``type`` field selects which model is used.
NodeConfig = Annotated[
    LLMNodeConfig | ModuleNodeConfig,
    Field(discriminator="type"),
]


class StageConfig(_Strict):
    """An ordered group of nodes.

    With ``parallel: true`` (default) the nodes run concurrently; with
    ``parallel: false`` they run in declared order, each seeing the previous
    node's output. The next stage always sees everything this stage wrote.
    """

    id: str
    parallel: bool = True
    when: ConditionConfig | None = None
    end_if: ConditionConfig | None = None
    nodes: list[NodeConfig]

    @model_validator(mode="after")
    def _non_empty(self) -> StageConfig:
        if not self.nodes:
            raise ValueError(f"stage '{self.id}' must contain at least one node")
        return self


class FlowConfig(_Strict):
    """A complete flow definition loaded from a single YAML file."""

    id: str
    route: str
    name: str | None = None
    description: str | None = None
    inputs: dict[str, InputSpec] = Field(default_factory=dict)
    outputs: list[str] = Field(default_factory=list)
    stages: list[StageConfig]

    @model_validator(mode="after")
    def _has_stages(self) -> FlowConfig:
        if not self.stages:
            raise ValueError(f"flow '{self.id}' must contain at least one stage")
        return self

    def iter_nodes(self):
        """Yield ``(stage, node)`` pairs across the whole flow."""
        for stage in self.stages:
            for node in stage.nodes:
                yield stage, node
