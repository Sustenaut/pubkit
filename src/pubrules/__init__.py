"""pubrules: the setup every package shares.

A command, `pubrules`, that keeps each repository's copies of the shared files
in step with the version it pins. The pytest plugin and the Gherkin steps
every suite loads are pubtest's (on PyPI as pubtestrules), whose floor the
managed tests/requirements-pubrules.txt names.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pubrules")
except PackageNotFoundError:  # running from a source tree
    __version__ = "0.0.0"
