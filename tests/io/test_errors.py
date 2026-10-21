"""Contract for the connector's errors, whose docstrings become SiLA Defined Execution Error descriptions."""

import inspect

import pytest

from unitelabs.zebra_ds8178.io import errors

ERRORS = [value for _, value in inspect.getmembers(errors, inspect.isclass) if issubclass(value, Exception)]


def test_every_error_is_exported_from_io() -> None:
    """Features import errors from `io`, so none may be missing from its `__all__`."""
    from unitelabs.zebra_ds8178 import io

    assert {error.__name__ for error in ERRORS} <= set(io.__all__)


@pytest.mark.parametrize("error", ERRORS, ids=lambda error: error.__name__)
def test_error_describes_what_happened_and_how_to_recover(error: type[Exception]) -> None:
    """Each docstring has a one-line summary followed by a recovery paragraph."""
    summary, _, recovery = inspect.getdoc(error).partition("\n\n")

    assert summary
    assert "\n" not in summary
    assert recovery.strip()


@pytest.mark.parametrize("error", ERRORS, ids=lambda error: error.__name__)
def test_error_derives_directly_from_exception(error: type[Exception]) -> None:
    """The CDK matches the raised class name against the declared errors, so no error may subclass another."""
    assert error.__bases__ == (Exception,)
