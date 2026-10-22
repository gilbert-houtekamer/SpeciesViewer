"""The one place the SpeciesViewer version lives.

Everything that shows or records a version reads it from here: the main
window title, the About box, and (later) the packaged build. A release is
tagged in git as v<major>.<minor> on the commit that sets the final value,
e.g. "0.4.0" -> tag v0.4. Between releases the value carries ".dev", so a
screenshot or log line from a work-in-progress build says so.
"""

__version__ = "0.5.1.dev"

APP_NAME = "SpeciesViewer"
