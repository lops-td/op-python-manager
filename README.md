# Python Manager

**Parent Project:** [dot-lops](https://github.com/lops-td/dot-lops)

Unified Python environment manager for TouchDesigner, providing a robust interface for managing virtual environments (venvs) across various operators. It ensures that complex AI and data science dependencies remain isolated, consistent, and correctly integrated into the TouchDesigner Python path.

## Download

[Download the latest standalone TOX from GitHub Releases](https://github.com/lops-td/op-python-manager/releases/latest).

## Changelogs

- [1.3.2](changelog/1.3.2.md) - Latest
- [1.3.0](changelog/1.3.0.md)
- [1.2.0](changelog/1.2.0.md)
- [1.1.2](changelog/1.1.2.md)
- [1.1.1](changelog/1.1.1.md)
- [1.1.0](changelog/1.1.0.md)
- [1.0.0](changelog/1.0.0.md)

## Documentation

[Live Documentation](https://docs.dotsimulate.com/lops-controls/python-manager/)

## Developer Notes

- **Venv Orchestration:** Wraps `VenvCore` to provide high-level methods for creating environments, installing packages from `requirements.txt`, and upgrading `pip`, supporting both standard `pip` and high-performance `uv` backends.
- **Dynamic Path Management:** Automatically manages `sys.path` entries, allowing multiple operators to share a "shared" venv or maintain their own isolated environments without manual path configuration.
- **Python Discovery:** Features a standalone Python discovery engine that finds compatible local Python installations to use as base interpreters for new venvs.
- **Operator Integration:** Provides a backward-compatible API (`Pipinstall`, `Addtosyspath`, etc.) used by other LOP operators to automate their own dependency setup.
- **Cross-Platform Utility:** Includes Windows-specific optimizations like 8.3 short-path conversion to handle directory names with spaces and special characters.
- **State Persistence:** Maintains a detailed registry of managed venvs, their status, and installed package lists in structured TouchDesigner DATs.

## License

`op-python-manager` is licensed under the [Apache License 2.0](LICENSE).

It is a standalone system component and may be used freely, including in
commercial projects. Other DOTsimulate products — including the LOPs operator
library — are **not** covered by this license and are governed by the
[DOTsimulate Licensing and Usage terms](https://docs.dotsimulate.com/legal/licensing-and-usage/).

Note: builds up to and including 1.3.1 shipped under the LOPs Operators License
v2.0. The Apache 2.0 grant applies from 1.3.2 onward.

Questions: licensing@dotsimulate.com
