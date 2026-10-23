"""Generic macOS Accessibility primitives (no WeChat knowledge).

Kept deliberately small: tree traversal, attribute reads and AXValue geometry
conversions. Everything WeChat-specific lives in :mod:`wechat_accessibility`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ApplicationServices import (
    AXUIElementCopyAttributeValue,
    AXValueGetType,
    AXValueGetValue,
    kAXChildrenAttribute,
    kAXIdentifierAttribute,
    kAXPositionAttribute,
    kAXRoleAttribute,
    kAXSizeAttribute,
    kAXTitleAttribute,
    kAXValueCGPointType,
    kAXValueCGSizeType,
)

from .logging_config import logger

AXPredicate = Callable[[Any, Any, Any, Any], bool]


def ax_get(element: Any, attribute: Any) -> Any:
    """Read one AX attribute, returning ``None`` on any AX error."""
    if element is None:
        return None
    err, value = AXUIElementCopyAttributeValue(element, attribute, None)
    if err != 0:
        return None
    return value


def ax_children(element: Any) -> list[Any]:
    children = ax_get(element, kAXChildrenAttribute)
    return list(children) if children else []


def dfs(
    element: Any, predicate: AXPredicate, *, max_depth: int = 40
) -> Any | None:
    """Depth-first search for the first element matching ``predicate``.

    Iterative and depth-capped: Qt-based WeChat 4.x can report pathologically
    deep or cyclic child chains, which would otherwise blow Python's stack.
    """
    if element is None:
        return None

    stack: list[tuple[Any, int]] = [(element, 0)]
    while stack:
        node, depth = stack.pop()
        if node is None:
            continue

        role = ax_get(node, kAXRoleAttribute)
        title = ax_get(node, kAXTitleAttribute)
        identifier = ax_get(node, kAXIdentifierAttribute)
        if predicate(node, role, title, identifier):
            return node

        if depth >= max_depth:
            continue
        for child in reversed(ax_children(node)):
            stack.append((child, depth + 1))
    return None


def walk(element: Any, visit: Callable[[Any], None], *, max_depth: int = 40) -> None:
    """Visit every AX element below ``element``, depth-first.

    ``max_depth`` guards against pathological trees and AX cycles.
    """
    if element is None:
        return

    # No identity-based dedupe here: CPython reuses ids of temporary PyObjC
    # wrappers, which would silently prune valid subtrees. The depth cap alone
    # is enough to stop cycles.
    stack: list[tuple[Any, int]] = [(element, 0)]
    while stack:
        node, depth = stack.pop()
        visit(node)
        if depth >= max_depth:
            continue
        for child in reversed(ax_children(node)):
            stack.append((child, depth + 1))


def axvalue_to_point(ax_value: Any) -> tuple[float, float] | None:
    if ax_value is None or AXValueGetType(ax_value) != kAXValueCGPointType:
        return None
    ok, cg_point = AXValueGetValue(ax_value, kAXValueCGPointType, None)
    if not ok:
        return None
    return float(cg_point.x), float(cg_point.y)


def axvalue_to_size(ax_value: Any) -> tuple[float, float] | None:
    if ax_value is None or AXValueGetType(ax_value) != kAXValueCGSizeType:
        return None
    ok, cg_size = AXValueGetValue(ax_value, kAXValueCGSizeType, None)
    if not ok:
        return None
    return float(cg_size.width), float(cg_size.height)


def element_frame(element: Any) -> tuple[float, float, float, float] | None:
    """Return ``(x, y, width, height)`` for an element, if exposed by AX."""
    point = axvalue_to_point(ax_get(element, kAXPositionAttribute))
    size = axvalue_to_size(ax_get(element, kAXSizeAttribute))
    if point is None or size is None:
        logger.debug("Element has no AX position/size")
        return None
    return point[0], point[1], size[0], size[1]


def element_center(element: Any) -> tuple[float, float] | None:
    frame = element_frame(element)
    if frame is None:
        return None
    x, y, width, height = frame
    return x + width / 2.0, y + height / 2.0
