"""Workflow parameter parsing and resolution.

Extracts PARAMS declarations from .star files and validates/coerces
CLI-provided -p KEY=VALUE arguments against declared types.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from starloom.types import ParamType

ParamValue = str | int | float | bool | list[str]

# Raw keyword args parsed from a param() call in Starlark source.
RawParamKwargs = dict[str, str | int | float | bool | list[str] | None]

# Dict returned by param() builtin for starlark-go marshalling.
StarlarkParamSpec = dict[str, str | int | float | bool | None]

_UNSET = object()  # sentinel: no default declared (distinct from default=None)


@dataclass(frozen=True, slots=True)
class ParamDecl:
    """A single declared workflow parameter."""

    name: str
    type: ParamType = ParamType.STRING
    default: str | int | float | bool | list[str] | None | object = _UNSET
    description: str = ""

    @property
    def required(self) -> bool:
        """True only when no default was declared (not even None)."""
        return self.default is _UNSET

    @property
    def has_default(self) -> bool:
        return self.default is not _UNSET


# ---------------------------------------------------------------------------
# Parsing PARAMS = [...] from Starlark source
# ---------------------------------------------------------------------------

_PARAMS_BLOCK_RE = re.compile(
    r"^PARAMS\s*=\s*\[([^\]]*)\]",
    re.MULTILINE | re.DOTALL,
)
_PARAM_CALL_RE = re.compile(r"param\s*\(([^)]*)\)")


def parse_params(source: str) -> list[ParamDecl]:
    """Extract PARAMS declarations from Starlark source.

    Returns empty list if no PARAMS block found.
    """
    match = _PARAMS_BLOCK_RE.search(source)
    if not match:
        return []
    body = match.group(1)
    return [_parse_one_param(m.group(1)) for m in _PARAM_CALL_RE.finditer(body)]


def _parse_one_param(call_args: str) -> ParamDecl:
    """Parse a single param(...) call string."""
    name, kwargs = _extract_param_args(call_args)
    param_type = _resolve_param_type(str(kwargs.get("type", "string")))
    return ParamDecl(
        name=str(name),
        type=param_type,
        default=kwargs.get("default", _UNSET),
        description=str(kwargs.get("description", "")),
    )


def _extract_param_args(call_args: str) -> tuple[str, RawParamKwargs]:
    """Split param() args into positional name and keyword args."""
    parts = [p.strip() for p in call_args.split(",")]
    name_val: str | int | float | bool | list[str] | None = None
    kwargs: RawParamKwargs = {}
    for i, part in enumerate(parts):
        if not part:
            continue
        if "=" in part:
            key, _, val = part.partition("=")
            kwargs[key.strip()] = _safe_literal(val.strip())
        elif i == 0:
            name_val = _safe_literal(part)
    if name_val is None:
        name_val = kwargs.pop("name", "")
    return str(name_val), kwargs


def _safe_literal(s: str) -> str | int | float | bool | list[str] | None:
    """Parse a Python literal, falling back to stripped string."""
    try:
        val: str | int | float | bool | list[str] | None = ast.literal_eval(s)
        return val
    except (ValueError, SyntaxError):
        return s.strip("\"'")


def _resolve_param_type(type_str: str) -> ParamType:
    try:
        return ParamType(type_str.lower())
    except ValueError:
        return ParamType.STRING


# ---------------------------------------------------------------------------
# Resolution: declared params + CLI strings → typed values
# ---------------------------------------------------------------------------


def _coerce_bool(v: str | bool) -> bool:
    if isinstance(v, bool):
        return v
    return v.lower() in ("true", "1", "yes")


def _coerce_list(v: str | list[str]) -> list[str]:
    if isinstance(v, list):
        return v
    return [s.strip() for s in v.split(",")]


_COERCERS: dict[ParamType, Callable[..., ParamValue]] = {
    ParamType.STRING: str,
    ParamType.INT: int,
    ParamType.FLOAT: float,
    ParamType.BOOL: _coerce_bool,
    ParamType.LIST: _coerce_list,
}


def resolve_params(
    declared: list[ParamDecl],
    provided: dict[str, str],
) -> dict[str, ParamValue | None]:
    """Validate and coerce CLI params against declarations.

    Raises ValueError for missing required params or type errors.
    """
    resolved: dict[str, ParamValue | None] = {}
    declared_names = set()
    for decl in declared:
        declared_names.add(decl.name)
        resolved[decl.name] = _resolve_one(decl, provided)
    _check_unknown(declared_names, provided)
    return resolved


def _resolve_one(
    decl: ParamDecl,
    provided: dict[str, str],
) -> str | int | float | bool | list[str] | None:
    if decl.name in provided:
        return _coerce_value(decl, provided[decl.name])
    if decl.has_default:
        return cast("str | int | float | bool | list[str] | None", decl.default)
    raise ValueError(
        f"Missing required parameter: '{decl.name}'"
        + (f" — {decl.description}" if decl.description else "")
    )


def _coerce_value(decl: ParamDecl, raw: str) -> str | int | float | bool | list[str]:
    coercer = _COERCERS.get(decl.type, str)
    try:
        return coercer(raw)
    except (ValueError, TypeError) as e:
        raise ValueError(
            f"Parameter '{decl.name}': cannot coerce {raw!r} to {decl.type.value}: {e}"
        ) from e


def _check_unknown(declared_names: set[str], provided: dict[str, str]) -> None:
    unknown = set(provided) - declared_names
    if unknown:
        raise ValueError(f"Unknown parameter(s): {', '.join(sorted(unknown))}")


def make_param_builtin() -> Callable[..., StarlarkParamSpec]:
    """Return the param() builtin for Starlark (dict spec, not ParamDecl)."""

    def param(
        name: str,
        type: str = "string",
        default: str | int | float | bool | None = None,
        description: str = "",
    ) -> StarlarkParamSpec:
        return {
            "name": name,
            "type": type,
            "default": default,
            "description": description,
        }

    return param
