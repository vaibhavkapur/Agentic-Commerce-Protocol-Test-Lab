"""A small JSONPath subset for assertions: ``$.a.b``, ``$.a[0]``, ``$.a[*].b``, ``$.a[?type=total].amount``."""

from __future__ import annotations

import re
from typing import Any, List

_TOKEN = re.compile(r"\.([A-Za-z_][A-Za-z0-9_-]*)|\['([^']+)'\]|\[(\*|\d+|\?[A-Za-z_][A-Za-z0-9_]*=[^\]]+)\]")


class PathError(ValueError):
    pass


def select(value: Any, path: str) -> List[Any]:
    if not path.startswith("$"):
        raise PathError("path must start with $")
    current: List[Any] = [value]
    pos = 1
    while pos < len(path):
        m = _TOKEN.match(path, pos)
        if not m:
            raise PathError(f"cannot parse path near {path[pos:]!r}")
        key, idx = m.group(1) if m.group(1) is not None else m.group(2), m.group(3)
        nxt: List[Any] = []
        for item in current:
            if key is not None:
                if isinstance(item, dict) and key in item:
                    nxt.append(item[key])
            elif idx == "*":
                if isinstance(item, list):
                    nxt.extend(item)
            elif idx.startswith("?"):
                fkey, _, fval = idx[1:].partition("=")
                if isinstance(item, list):
                    nxt.extend(x for x in item if isinstance(x, dict) and str(x.get(fkey)) == fval)
            else:
                if isinstance(item, list) and int(idx) < len(item):
                    nxt.append(item[int(idx)])
        current = nxt
        pos = m.end()
    return current


def first(value: Any, path: str) -> Any:
    hits = select(value, path)
    if not hits:
        raise PathError(f"{path} matched nothing")
    return hits[0]
