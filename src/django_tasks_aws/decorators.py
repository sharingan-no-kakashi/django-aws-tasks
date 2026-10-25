"""Function scopes whose context arguments are resolved for each call."""

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from functools import wraps
from inspect import isasyncgenfunction, iscoroutinefunction, isgeneratorfunction, signature
from typing import Any

type ScopeArgument = str | Callable[[Mapping[str, Any]], Any]


def scoped[**P, R](
    context_factory: Callable[..., AbstractContextManager[Any]],
    *arguments: ScopeArgument,
    **keywords: ScopeArgument,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Wrap a synchronous call, resolving dotted argument paths or selectors.

    Each invocation creates a fresh context. Stack decorators in the same order
    as nested scopes: the top decorator enters first and exits last.
    """

    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        if any(
            check(function)
            for check in (iscoroutinefunction, isgeneratorfunction, isasyncgenfunction)
        ):
            raise TypeError("scoped requires a synchronous, non-generator function")
        parameters = signature(function)

        @wraps(function)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            bound = parameters.bind(*args, **kwargs)
            bound.apply_defaults()
            positional = [resolve_scope_argument(value, bound.arguments) for value in arguments]
            named = {
                key: resolve_scope_argument(value, bound.arguments)
                for key, value in keywords.items()
            }
            with context_factory(*positional, **named):
                return function(*args, **kwargs)

        return wrapped

    return decorate


def resolve_scope_argument(selector: ScopeArgument, arguments: Mapping[str, Any]) -> Any:
    if callable(selector):
        return selector(arguments)
    parameter, *attributes = selector.split(".")
    value = arguments[parameter]
    for attribute in attributes:
        value = getattr(value, attribute)
    return value
