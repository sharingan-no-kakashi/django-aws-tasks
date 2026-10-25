"""Public Lambda entry points.

Intentionally empty: importing this package must not eagerly import the
model-dependent handler services before ``django.setup()`` has run.
"""

__all__: list[str] = []
