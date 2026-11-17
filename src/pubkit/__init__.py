"""pubkit: the setup every package shares.

A command, `pubkit`, that keeps each repository's copies of the shared files
in step with the version it pins. The pytest plugin and the Gherkin steps
every suite loads are pubtest's, which the managed tests/requirements-pubkit.txt
pins.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pubkit")
except PackageNotFoundError:  # running from a source tree
    __version__ = "0.0.0"
