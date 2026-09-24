"""Sanity check that every top-level package imports cleanly."""

import importlib

PACKAGES = [
    "src",
    "src.env",
    "src.tools",
    "src.agent",
    "src.durability",
    "src.risk",
    "src.revalidation",
    "src.audit",
    "src.eval",
    "src.chaos",
    "src.api",
]


def test_all_packages_importable():
    for name in PACKAGES:
        importlib.import_module(name)
