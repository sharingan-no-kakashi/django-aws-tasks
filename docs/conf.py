"""Sphinx configuration for the django-tasks-aws documentation.

The package is a Django application, so ``django.setup()`` must run before
autodoc imports any module that touches the app registry. A minimal in-memory
configuration is used here; the documentation build never connects to a
database.
"""

import os
import sys

from django.conf import settings

from django_tasks_aws import __version__

# Make the repository root importable so the in-tree ``src`` layout and the
# ``tests`` package resolve during the build.
sys.path.insert(0, os.path.abspath(".."))
sys.path.insert(0, os.path.abspath("../src"))

# Minimal Django configuration. The backend is never instantiated during the
# documentation build, so no ``TASKS`` entry or PostgreSQL alias is required.
if not settings.configured:
    settings.configure(
        SECRET_KEY="docs-build-only",
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.admin",
            "django.contrib.sessions",
            "django.contrib.messages",
            "django.contrib.staticfiles",
            "django_tasks_aws",
        ],
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        USE_TZ=True,
    )

import django

django.setup()

project = "django-tasks-aws"
copyright = "2026, Peter"
author = "Peter"

# The short X.Y version and the full release, mirroring the package metadata.
release = __version__
version = ".".join(release.split(".")[:2])

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
]

# Napoleon parses Google- and NumPy-style docstrings, in addition to plain
# reStructuredText, so the existing docstrings render cleanly.
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_use_rtype = True
napoleon_use_param = True

# Import members with explicit names only; undecorated helpers stay out of the
# public reference.
autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
    "member-order": "bysource",
}
autodoc_typehints = "description"
autosummary_generate = True

# Members imported into a module but not defined there should not be documented
# twice. ``__all__`` on the public modules is honoured when present.
add_module_names = False
python_use_unqualified_type_names = True

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "django": ("https://docs.djangoproject.com/en/6.1/", None),
}

templates_path = ["_templates"]
exclude_patterns = [
    "_build",
    "archive/**",
    "Thumbs.db",
    ".DS_Store",
    # Existing Markdown notes live outside the Sphinx toctree; they are linked
    # from the guide pages where a reader needs the raw operational detail.
    "*.md",
]

html_theme = "sphinx_rtd_theme"
html_theme_options = {
    "navigation_depth": 3,
    "collapse_navigation": False,
    "sticky_navigation": True,
    "includehidden": True,
}

html_static_path = ["_static"]
htmlhelp_basename = "django-tasks-aws-doc"
