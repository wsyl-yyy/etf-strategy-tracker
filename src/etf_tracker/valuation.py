from __future__ import annotations


def valuation_status() -> str:
    """Placeholder for valuation data source integration.

    Public valuation sources differ in fields, refresh timing, and usage terms.
    The first implementation intentionally does not make automatic buy decisions
    from an unstable valuation feed. Add a provider only after comparing source
    coverage, stability, free quota, access difficulty, and privacy risk.
    """
    return "估值分位自动源未锁定；依赖估值的新增买入需人工复核。"

