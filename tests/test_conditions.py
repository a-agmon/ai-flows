from app.config.models import ConditionConfig
from app.graph.conditions import evaluate_condition, get_nested_value


def cond(**kwargs) -> ConditionConfig:
    return ConditionConfig.model_validate(kwargs)


def test_equals_and_not_equals():
    state = {"status": "ok"}
    assert evaluate_condition(cond(field="status", equals="ok"), state)
    assert not evaluate_condition(cond(field="status", equals="bad"), state)
    assert evaluate_condition(cond(field="status", not_equals="bad"), state)


def test_exists():
    state = {"a": 1, "b": None}
    assert evaluate_condition(cond(field="a", exists=True), state)
    assert evaluate_condition(cond(field="missing", exists=False), state)
    assert not evaluate_condition(cond(field="a", exists=False), state)


def test_contains_and_in():
    state = {"tags": ["x", "y"], "name": "alpha"}
    assert evaluate_condition(cond(field="tags", contains="x"), state)
    assert evaluate_condition(cond(field="name", **{"in": ["alpha", "beta"]}), state)
    # contains on a non-iterable returns False rather than raising.
    assert not evaluate_condition(cond(field="missing", contains="x"), state)


def test_equals_false_is_distinguished_from_unset():
    # A literal `equals: false` must compare against False, not be ignored.
    assert evaluate_condition(cond(field="flag", equals=False), {"flag": False})


def test_dotted_path():
    state = {"classification": {"request_status": "unsupported"}}
    assert get_nested_value(state, "classification.request_status") == "unsupported"
    assert evaluate_condition(
        cond(field="classification.request_status", equals="unsupported"), state
    )


def test_exactly_one_operator_required():
    import pytest

    with pytest.raises(ValueError):
        cond(field="x")  # no operator
    with pytest.raises(ValueError):
        cond(field="x", equals="a", not_equals="b")  # two operators
