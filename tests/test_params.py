"""Tests for params.py — parsing and resolution."""

from __future__ import annotations

import pytest

from starloom.params import ParamDecl, make_param_builtin, parse_params, resolve_params
from starloom.types import ParamType


class TestParseParams:
    def test_empty_source(self) -> None:
        assert parse_params("") == []

    def test_no_params_block(self) -> None:
        assert parse_params("def main():\n  pass") == []

    def test_single_param(self) -> None:
        source = 'PARAMS = [param("query", type="string", description="Search query")]'
        result = parse_params(source)
        assert len(result) == 1
        assert result[0].name == "query"
        assert result[0].type == ParamType.STRING
        assert result[0].description == "Search query"

    def test_multiple_params(self) -> None:
        source = """PARAMS = [
    param("count", type="int", default=3),
    param("model", type="string", default="haiku"),
]"""
        result = parse_params(source)
        assert len(result) == 2
        assert result[0].name == "count"
        assert result[0].type == ParamType.INT
        assert result[0].default == 3
        assert result[1].name == "model"
        assert result[1].default == "haiku"

    def test_bool_param(self) -> None:
        source = 'PARAMS = [param("verbose", type="bool", default=False)]'
        result = parse_params(source)
        assert result[0].type == ParamType.BOOL
        assert result[0].default is False

    def test_required_param(self) -> None:
        source = 'PARAMS = [param("query")]'
        result = parse_params(source)
        assert result[0].required is True


class TestResolveParams:
    def test_resolves_provided(self) -> None:
        decls = [ParamDecl(name="count", type=ParamType.INT)]
        result = resolve_params(decls, {"count": "5"})
        assert result["count"] == 5

    def test_uses_default(self) -> None:
        decls = [ParamDecl(name="model", type=ParamType.STRING, default="haiku")]
        result = resolve_params(decls, {})
        assert result["model"] == "haiku"

    def test_missing_required_raises(self) -> None:
        decls = [ParamDecl(name="query", type=ParamType.STRING)]
        with pytest.raises(ValueError, match="Missing required"):
            resolve_params(decls, {})

    def test_unknown_param_raises(self) -> None:
        decls = [ParamDecl(name="query", type=ParamType.STRING)]
        with pytest.raises(ValueError, match="Unknown"):
            resolve_params(decls, {"query": "test", "extra": "bad"})

    def test_bool_coercion(self) -> None:
        decls = [ParamDecl(name="verbose", type=ParamType.BOOL)]
        assert resolve_params(decls, {"verbose": "true"})["verbose"] is True
        assert resolve_params(decls, {"verbose": "false"})["verbose"] is False
        assert resolve_params(decls, {"verbose": "1"})["verbose"] is True

    def test_list_coercion(self) -> None:
        decls = [ParamDecl(name="items", type=ParamType.LIST)]
        result = resolve_params(decls, {"items": "a,b, c"})
        assert result["items"] == ["a", "b", "c"]

    def test_float_coercion(self) -> None:
        decls = [ParamDecl(name="rate", type=ParamType.FLOAT)]
        result = resolve_params(decls, {"rate": "3.14"})
        assert abs(result["rate"] - 3.14) < 1e-9  # type: ignore[operator]

    def test_type_error_raises(self) -> None:
        decls = [ParamDecl(name="count", type=ParamType.INT)]
        with pytest.raises(ValueError, match="cannot coerce"):
            resolve_params(decls, {"count": "not_a_number"})


class TestParamBuiltin:
    def test_returns_dict(self) -> None:
        param = make_param_builtin()
        result = param("query", type="string", description="Search term")
        assert result == {
            "name": "query",
            "type": "string",
            "default": None,
            "description": "Search term",
        }

    def test_with_default(self) -> None:
        param = make_param_builtin()
        result = param("count", type="int", default=3)
        assert result["default"] == 3
