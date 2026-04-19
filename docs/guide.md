---
title: Python Manager
description: Manages Python virtual environments, package installation, and sys.path orchestration with a three-tier config system for portable and machine-specific setups.
related: [chat]
components: [Aside]
docs_updated_at_commit: 51f2008
---

The Python Manager LOP creates and manages Python virtual environments, installs packages via pip or UV, and controls which venv site-packages are available in TouchDesigner's `sys.path`. Environment registrations are stored in a three-tier JSON config system that separates machine-specific paths from portable project settings.

**Most users do not need to place this operator.** ChatTD includes a built-in Python Manager that handles dependency installation for all LOPs operators automatically. Use the standalone Python Manager only when you need:

- A separate venv with a different Python version than the shared LOPs venv
- An isolated environment for a specific operator or workflow (e.g., a custom ML model with conflicting dependencies)
- Manual control over multiple registered environments across projects

## Key Features

- **Dual backend support** -- UV (fast, auto-downloads Python) or pip (standard, uses system Python)
- **Three-tier config system** -- register environments at the user, project folder, or project level with automatic merging and deduplication
- **sys.path management** -- toggle individual venvs into TouchDesigner's import path with per-environment import controls
- **Stale path detection** -- status field flags environments whose paths no longer exist on disk, useful when moving projects between machines
- **Auto-migration** -- legacy sequence-based environment registrations are automatically migrated to the JSON config system on first load
- **Requirements file support** -- install from or export to `requirements.txt`
- **Cross-platform** -- works on Windows and macOS

## Input/Output

This operator has no wired inputs. It has one output but is primarily configured through its parameter panel and exposes its functionality to other operators via promoted methods.

## Three-Tier Config System

Environment registrations are stored across three tiers, merged at load time with higher tiers overriding lower ones when names collide:

| Tier | Location | Use Case |
|------|----------|----------|
| **User** | `{AppData}/ChatTD/python_config.json` | Machine-specific absolute paths. Persists across all projects on this computer. |
| **Project Folder** | `{project.folder}/.lops/python_config.json` | Portable relative paths. Travels with the project folder, resolves correctly on any machine. |
| **Project** | Text DAT inside the operator | Embedded in the `.toe` file. Travels with the project file itself. Highest priority. |

Priority for deduplication: **Project > Folder > User**. If the same environment name appears in multiple tiers, the highest-priority tier wins.

### Choosing a Config Tier

On the **Environments** page, the `Save New Envs To` menu controls where newly created or registered environments are stored:

- **User Config** -- best for machine-local venvs that should persist across all projects (e.g., a shared ML environment)
- **Project Folder (.lops/)** -- best for venvs that live inside or near the project folder and should be portable to other machines. Paths inside the project folder are automatically stored as relative paths.
- **Project (.toe)** -- best for venvs that must travel embedded in the `.toe` file. Useful when distributing a self-contained project.

The read-only `Config Path` field shows the resolved filesystem path for the active tier (or "(stored in operator DAT)" for the Project tier).

## Usage Examples

### Creating a New Virtual Environment

1. On the **Python Env** page, set the `Backend` to your preferred package manager (UV is recommended for speed)
2. If using UV, choose a `Python Version (UV)` and optionally set `Venv Options` (e.g., "Seed" to include pip/setuptools in the venv)
3. Set a `Base Folder` to the directory where the venv should be created
4. Pulse `Create Venv` -- a `venv/` subfolder is created inside your base folder and automatically registered to the active config tier
5. Toggle `Add to sys.path` on if you want the venv's packages available for import inside TouchDesigner

<Aside type="tip" title="No folder set?">
  If you pulse Create Venv without setting a Base Folder, a folder picker dialog opens automatically.
</Aside>

### Installing Packages

1. Select the target environment from the `Selected Environment` menu, or leave on "Custom / New Venv" to use the Base Folder path
2. Under **Install / Manage Packages**, type one or more package names in the `Package` field (space-separated)
3. Pulse `Install`, `Uninstall`, or `Update` as needed
4. To bulk-install, point the `Requirements File` parameter to a `requirements.txt` and pulse `Install from File`

### Registering an Existing Venv

If you already have a venv on disk that was created outside of LOPs:

1. On the **Python Env** page, set `Base Folder` to the parent directory containing the `venv/` folder
2. Pulse `Create Venv` -- the operator detects the existing venv and registers it without recreating it
3. The environment appears on the **Environments** page and in the `Selected Environment` menu

## Managing Multiple Environments

The **Environments** page holds a sequence of registered venvs, rebuilt automatically from the merged JSON config on each load. Each entry shows:

- **name** -- a label for the environment
- **path** -- the resolved filesystem path to the venv directory
- **import** -- toggle to add/remove this venv's site-packages from `sys.path` (runtime-only, not persisted to the JSON config)
- **open** -- pulse to open a terminal with the venv activated
- **Status** -- read-only field showing the config tier label (e.g., `[user]`, `[folder]`, `[project]`), an import indicator, and the Python version. Shows `STALE` if the path no longer exists.

<Aside type="note" title="Display-only sequence">
  The Environments sequence is rebuilt from the JSON config files each time the operator initializes. Editing sequence entries directly (adding/removing rows) does not modify the underlying config. Use the config tier system to add or remove environments persistently.
</Aside>

When a venv is removed from the sequence and `On Seq Remove` is set to "Auto-remove from sys.path," its site-packages path is automatically cleaned from `sys.path`.

### Refreshing and Editing Config

- Pulse `Refresh Registry` to reload environments from all three config tiers and rebuild the sequence display
- Pulse `Edit Config File` to open the active tier's JSON file in your system text editor (User or Folder tier). For the Project tier, edit the `project_venv_config` text DAT directly inside the operator.

## Best Practices

- **Use UV** as the backend for faster venv creation and package installation. UV auto-downloads the requested Python version, so you do not need a standalone Python install.
- **Use the Project Folder tier** for team projects -- relative paths resolve correctly when collaborators clone the project to different locations.
- **Use the User tier** for large shared environments (e.g., a PyTorch/CUDA venv) that should be available across all your projects without duplication.
- **Check the Status column** after moving a project to a new machine. Environments marked `STALE` need their paths updated or the venv recreated locally.

## Troubleshooting

- **"UV Not Found"** -- UV is not installed on the system. The operator will prompt you to switch to the pip backend. Alternatively, install UV separately.
- **"No compatible Python found"** -- When using the pip backend, a standalone Python matching TouchDesigner's version must be installed on the system. UV avoids this requirement by downloading Python automatically.
- **Packages not importable after install** -- Make sure the environment's `import` toggle is enabled on the **Environments** page, or that `Add to sys.path` is on for the primary environment on the **Python Env** page.
- **STALE environments after moving projects** -- The venv path no longer exists on this machine. Either recreate the venv locally with the same Base Folder, or update the path in the config JSON via `Edit Config File`.
- **Sequence looks empty after upgrade** -- On first load after upgrading from a pre-config version, the operator auto-migrates legacy sequence entries to the User config tier. Pulse `Refresh Registry` if entries do not appear immediately.
