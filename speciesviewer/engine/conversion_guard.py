# conversion_guard.py: keep a trip made before a species-list conversion from
# writing its old names into a converted master (ch.6).
#
# ONE JOB: tell whether a trip's species list is behind its master's — the
# master's list carries the Sequence column (converted: '040 passeriformes' is
# now 'passeriformes'), the trip's does not. Such a trip still files species
# under the numbered folders; publishing or merging it would bring them back.
#
# INVARIANT: on an unconverted master this never refuses anything.

import os

from engine.exceptions import SVException

_ACTIVE = os.path.join("metadata", "active", "speciescollection.csv")


def is_converted(repository):
    """True when the repository's species list has a Sequence column."""
    path = os.path.join(repository, _ACTIVE)
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return "Sequence" in f.readline().strip().split(";")
    except OSError:
        return False


def trip_behind_master(trip_repository, master_repository):
    return (bool(master_repository) and trip_repository != master_repository
            and is_converted(master_repository) and not is_converted(trip_repository))


def refuse_if_behind(trip_repository, master_repository, step):
    """Raise SVException with a plain explanation when the trip is behind."""
    if trip_behind_master(trip_repository, master_repository):
        raise SVException(
            f"{step} is stopped for this trip: it was made before the master's species list "
            f"was converted, and it still uses the old names (such as '040 passeriformes'). "
            f"Going on would bring those back into the master. To continue with this trip: "
            f"Reset Trip (it then takes the master's new species list) and run Read & "
            f"Summarize again.")
