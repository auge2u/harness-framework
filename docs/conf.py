"""Sphinx configuration for Harness Framework documentation."""

import os
import sys

sys.path.insert(0, os.path.abspath("../src"))

from harness import __version__

project = "Harness Framework"
copyright = "2026, Harness Framework Contributors"
author = "Harness Framework Contributors"
release = __version__
version = __version__

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.viewcode",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "myst_parser",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]

autodoc_member_order = "bysource"
autodoc_typehints = "description"

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

myst_enable_extensions = [
    "deflist",
    "tasklist",
]
