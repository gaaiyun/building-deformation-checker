import json5
import json

tests = [
    '{"a": 1, "b": 2,}',  # trailing comma
    "{'a': 1, 'b': 2}",   # single quote
    '{a: 1, b: 2}',         # unquoted key
    '{"a": 1 "b": 2}',     # missing comma
    '{"a": 1\n"b": 2}',    # newline between pairs
]

for t in tests:
    try:
        r = json5.loads(t)
        print(f'json5 OK: {t!r} -> {r}')
    except Exception as e:
        print(f'json5 FAIL: {t!r} -> {e}')
