# Python Manager

**Parent Project:** [dot-lops](https://github.com/lops-td/dot-lops)

Unified Python environment manager for TouchDesigner, providing a robust interface for managing virtual environments (venvs) across various operators. It ensures that complex AI and data science dependencies remain isolated, consistent, and correctly integrated into the TouchDesigner Python path.

## Changelogs

- [1.0.0](changelog/1.0.0.md) - Latest

## Documentation

[Live Documentation](https://docs.dotsimulate.com/lops-controls/python-manager/)

## Developer Notes

- **Venv Orchestration:** Wraps `VenvCore` to provide high-level methods for creating environments, installing packages from `requirements.txt`, and upgrading `pip`, supporting both standard `pip` and high-performance `uv` backends.
- **Dynamic Path Management:** Automatically manages `sys.path` entries, allowing multiple operators to share a "shared" venv or maintain their own isolated environments without manual path configuration.
- **Python Discovery:** Features a standalone Python discovery engine that finds compatible local Python installations to use as base interpreters for new venvs.
- **Operator Integration:** Provides a backward-compatible API (`Pipinstall`, `Addtosyspath`, etc.) used by other LOP operators to automate their own dependency setup.
- **Cross-Platform Utility:** Includes Windows-specific optimizations like 8.3 short-path conversion to handle directory names with spaces and special characters.
- **State Persistence:** Maintains a detailed registry of managed venvs, their status, and installed package lists in structured TouchDesigner DATs.
