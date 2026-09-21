"""Load :class:`Scenario` subclasses from directories or modules."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import List

from harness.scenarios.base import Scenario


class ScenarioLoader:
    """Utility class for discovering and instantiating :class:`Scenario`
    subclasses from the filesystem or importable Python modules.
    """

    @classmethod
    def from_directory(cls, path: str) -> List[Scenario]:
        """Scan a directory for ``*.py`` files and instantiate any
        :class:`Scenario` subclasses found inside them.

        Files named ``__init__.py`` or starting with an underscore are
        ignored.

        Args:
            path: Absolute or relative path to a directory containing
                Python files.

        Returns:
            A list of instantiated :class:`Scenario` objects.
        """
        directory = Path(path).resolve()
        if not directory.is_dir():
            return []

        instances: List[Scenario] = []

        for py_file in sorted(directory.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_name = f"_harness_dyn_{py_file.stem}"
            try:
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = mod
                spec.loader.exec_module(mod)
            except Exception:
                continue

            for obj in vars(mod).values():
                if (
                    inspect.isclass(obj)
                    and issubclass(obj, Scenario)
                    and obj is not Scenario
                    and not inspect.isabstract(obj)
                ):
                    try:
                        instances.append(obj())
                    except Exception:
                        continue

        return instances

    @classmethod
    def from_module(cls, module_path: str) -> List[Scenario]:
        """Import a module by its dotted path and instantiate any
        concrete :class:`Scenario` subclasses defined within it.

        Args:
            module_path: Dotted Python module path (e.g.
                ``"my_project.scenarios.auth"``).

        Returns:
            A list of instantiated :class:`Scenario` objects.
        """
        try:
            mod = importlib.import_module(module_path)
        except Exception:
            return []

        instances: List[Scenario] = []
        for obj in vars(mod).values():
            if (
                inspect.isclass(obj)
                and issubclass(obj, Scenario)
                and obj is not Scenario
                and not inspect.isabstract(obj)
            ):
                try:
                    instances.append(obj())
                except Exception:
                    continue
        return instances
