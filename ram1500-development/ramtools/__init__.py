"""Read-only, offline analysis helpers for the Ram 1500 development effort.

Nothing in this package talks to a vehicle, a panda, the network (except `schema.fetch_pinned_schema`,
which downloads public Cap'n Proto schema files from GitHub), or any controls process. The analysis
modules operate on plain Python records so they can be unit-tested without openpilot or pycapnp.
"""
