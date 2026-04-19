# Starlark Notes for LLM Authoring

Use this as a compact rule reference for writing correct Starlark `.star` files.
Default language behavior only. Exclude optional, flag-gated, host-specific, or Python-only features unless explicitly provided by the platform.

## 1. Core model

- Starlark is a deterministic, embedded, Python-like configuration language.
- A file is one module.
- Module source is UTF-8 text.
- Host platforms may provide extra functions/types. Do not invent them.
- No classes.
- No exceptions.
- No imports; use `load(...)`.
- No `global` or `nonlocal`.
- No Python standard library assumptions.

## 2. Lexical rules

- `#` starts a comment outside strings.
- Newlines and indentation are significant.
- Identifier syntax: Unicode letters/digits/`_`, not starting with a digit.

Keywords:

```text
and break continue def elif else for if in lambda load not or pass return while
```

Reserved and unavailable as identifiers:
- `as`
- `assert`
- `class`
- `del`
- `except`
- `finally`
- `from`
- `global`
- `import`
- `is`
- `nonlocal`
- `raise`
- `try`
- `with`
- `yield`
