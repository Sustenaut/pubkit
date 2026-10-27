"""One connector per data source, and the only code that reads one.

lists (protected.json, governed.json), github (the REST API), actions
(the Actions API), sonarcloud, scorecard (OpenSSF) and live (the
services' own health URLs). Each is a class with one named method per
query; base.py has what they share.
"""
