"""Learn — record and replay computer tasks.

Public API
----------
- :class:`LearnRecorder`  — global pynput recorder with async screenshot capture
- :func:`procedure_path`  — path to a saved procedure JSON
- :func:`load_procedure`  — load a saved procedure
- :func:`list_procedures` — list all saved procedures for a project
"""

from openvibe.learn.recorder import LearnRecorder
from openvibe.learn.storage import list_procedures, load_procedure, procedure_path

__all__ = [
    "LearnRecorder",
    "list_procedures",
    "load_procedure",
    "procedure_path",
]
