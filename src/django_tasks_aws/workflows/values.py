"""Tagged argument trees keep output references distinct from ordinary JSON."""

import json
import math

from .exceptions import PreparationError, WorkflowError
from .graph import Output


def get_output_references(value, depth=0):
    if depth > 50:
        raise WorkflowError("Workflow arguments cannot exceed 50 levels of nesting")
    if isinstance(value, Output):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from get_output_references(item, depth + 1)
    elif isinstance(value, dict):
        for item in value.values():
            yield from get_output_references(item, depth + 1)


def encode_value(value, keys, depth=0):
    if depth > 50:
        raise WorkflowError("Workflow arguments cannot exceed 50 levels of nesting")
    if isinstance(value, Output):
        return ["output", keys[value.node.key], list(value.path)]
    if isinstance(value, list):
        return ["list", [encode_value(item, keys, depth + 1) for item in value]]
    if isinstance(value, dict):
        return ["dict", encode_mapping(value, keys, depth)]
    if value is None or isinstance(value, (str, bool, int)):
        return ["literal", value]
    if isinstance(value, float) and math.isfinite(value):
        return ["literal", value]
    raise WorkflowError(f"Unsupported workflow argument type: {type(value).__name__}")


def encode_mapping(value, keys, depth):
    if not all(isinstance(key, str) for key in value):
        raise WorkflowError("Workflow argument objects must have string keys")
    return [[key, encode_value(value[key], keys, depth + 1)] for key in sorted(value)]


def resolve_value(encoded, results):
    match encoded:
        case ["literal", value]:
            return value
        case ["list", items]:
            return [resolve_value(item, results) for item in items]
        case ["dict", items]:
            return {key: resolve_value(item, results) for key, item in items}
        case ["output", key, path]:
            return select_output(results[key], path)
        case _:
            raise PreparationError("Unsupported saved workflow argument encoding")


def select_output(value, path):
    for selector in path:
        try:
            match value:
                case dict() if isinstance(selector, str):
                    value = value[selector]
                case list() if isinstance(selector, int):
                    value = value[selector]
                case _:
                    raise PreparationError("Output selector does not match the result type")
        except (KeyError, IndexError) as error:
            raise PreparationError("Selected output field or index does not exist") from error
    return value


def serialize_canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
