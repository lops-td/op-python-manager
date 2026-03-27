"""
PythonExternalLibExt - Unified Python virtual environment management for LOPs

Wraps VenvCore with TouchDesigner parameter interface.
Provides backwards-compatible API for existing operators.

External access pattern:
    pm = op.LOP.op('python_manager')
    pm.venv.create_venv('/path/to/venv', backend='uv')
    pm.venv.install_packages('/path/to/venv', ['torch'])
"""

import subprocess
import sys
import os
import json
import tdu

from dot_lop_utils import DotLOPUtils


def is_mac():
    return sys.platform == 'darwin'


def get_short_path_name(long_path):
    """Convert Windows path with spaces to 8.3 short format."""
    if is_mac() or ' ' not in long_path:
        return long_path
    try:
        import ctypes
        from ctypes import wintypes
        GetShortPathNameW = ctypes.windll.kernel32.GetShortPathNameW
        GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        GetShortPathNameW.restype = wintypes.DWORD
        buffer_size = GetShortPathNameW(long_path, None, 0)
        if buffer_size == 0:
            return long_path
        buffer = ctypes.create_unicode_buffer(buffer_size)
        result = GetShortPathNameW(long_path, buffer, buffer_size)
        if result > 0:
            return buffer.value.replace('\\', '/')
    except Exception:
        pass
    return long_path


class PythonExternalLibExt(DotLOPUtils):
    """
    TouchDesigner extension for Python virtual environment management.

    Exposes VenvCore as self.venv for direct stateless operations.
    Parameter-driven methods operate on the "primary" venv from Basefolder.
    """

    def __init__(self, ownerComp):
        super().__init__(ownerComp)

        # Import VenvCore from sibling DAT
        VenvCore = mod('venv_core').VenvCore
        self.venv = VenvCore(logger=self._log)

        # Import config storage module
        PythonConfigStorage = mod('python_config_storage').PythonConfigStorage
        self.storage = PythonConfigStorage(ownerComp, self.logger, self.ChatTD)

        # Track which tier owns each sequence index (rebuilt on load)
        self._entry_layers = {}

        self._setup_project_config_dat()
        self._setup_parameters()
        try:
            self.Setpython()  # Auto-detect python on init
            self._maybe_migrate_from_legacy_sequence()  # One-time migration
        except Exception as e:
            self._log(f"Init error (operator still loaded): {e}", level='ERROR')
        self._log("PythonExternalLibExt initialized")
        # Defer sequence-dependent init by 1 frame — TD 2025 resets sequence block
        # parameter values after __init__ returns, discarding any writes made during init.
        run("args[0].ext.PythonExternalLibExt._deferred_init()", self.ownerComp, delayFrames=1)

    def _log(self, msg, level='INFO'):
        """Wrapper for logger.log to simplify logging calls."""
        self.logger.log(msg, level=level)

    def _deferred_init(self):
        """Deferred post-init: runs 1 frame after __init__ so sequence block parameters
        are fully committed by TD before we write to them."""
        try:
            self._load_and_rebuild_registry()
            self.Refreshenvmenu()
            self._import_sequence_venvs()
        except Exception as e:
            self._log(f"Deferred init error: {e}", level='ERROR')

    # ==================== PROJECT CONFIG DAT ====================

    def _setup_project_config_dat(self):
        """Create or find the project_venv_config text DAT for project-tier storage."""
        dat_name = 'project_venv_config'
        existing = self.ownerComp.op(dat_name)
        if existing:
            self.project_config_dat = existing
        else:
            self.project_config_dat = self.ownerComp.create(textDAT, dat_name)
            if self.project_config_dat:
                self.project_config_dat.text = '{}'
                self.project_config_dat.nodeX = 400
                self.project_config_dat.nodeY = -300
                self.project_config_dat.viewer = False

    def _read_project_venvs(self) -> dict:
        """Read venv entries from the project config DAT."""
        if not self.project_config_dat:
            return {}
        try:
            text = self.project_config_dat.text.strip()
            if not text:
                return {}
            data = json.loads(text)
            if 'version' in data and 'venvs' in data:
                return data['venvs']
            elif isinstance(data, dict):
                return data
        except (json.JSONDecodeError, Exception):
            pass
        return {}

    def _write_project_venvs(self, venvs: dict):
        """Write venv entries to the project config DAT."""
        if not self.project_config_dat:
            return
        data = {'version': '1.0', 'venvs': venvs}
        self.project_config_dat.text = json.dumps(data, indent=2)

    # ==================== PARAMETER SETUP ====================

    def _setup_parameters(self):
        """Define all parameters following parameter_ui_design_guide patterns."""
        page = 'Python Env'

        # -------------------- STATUS (TOP) --------------------
        self.create_parameter('Status', 'str', page=page,
            label='', default='Ready', readOnly=True,
            help="Current status message")

        run("args[0].par.Status.expr = \"op('./Logger/out1')[1,2].val if op('./Logger/out1').numRows > 1 else 'Ready'\"",
            self.ownerComp, delayFrames=1)
        run("args[0].par.Status.label = \"\"",
            self.ownerComp, delayFrames=1)

        # -------------------- ENVIRONMENT SELECTION --------------------
        self.create_parameter('Selectedenv', 'menu', page=page,
            label='Selected Environment', default='custom',
            menuNames=['custom'], menuLabels=['Custom (Base Folder)'],
            help="Select registered environment or Custom to use Base Folder",
            section=True)

        self.create_parameter('Basefolder', 'folder', page=page,
            label='Base Folder', default='',
            help="Root folder for custom virtual environment",
            enableExpr="me.par.Selectedenv == 'custom'")

        self.create_parameter('Createvenv', 'pulse', page=page,
            label='Create Venv',
            help="Create virtual environment in Base Folder",
            section=True,
            enableExpr="me.par.Selectedenv == 'custom'")

        self.create_parameter('Openvenv', 'pulse', page=page,
            label='Open Console',
            help="Open terminal with venv activated",
            enableExpr="me.par.Selectedenv != 'custom' or me.par.Basefolder != ''")

        self.create_parameter('Addtosyspath', 'toggle', page=page,
            label='Add to sys.path', default=False,
            help="Add venv site-packages to TouchDesigner sys.path. Syncs with sequence import toggle.")

        # -------------------- BACKEND --------------------
        self.create_parameter('Backend', 'menu', page=page,
            label='Backend', default='uv',
            menuNames=['uv', 'pip'],
            menuLabels=['UV (Fast)', 'pip (Standard)'],
            help="Package manager backend. UV auto-downloads Python, pip uses system Python.",
            section=True)

        self.create_parameter('Pythonversion', 'strmenu', page=page,
            label='Python Version (UV)', default='3.11',
            menuNames=['3.9', '3.10', '3.11', '3.12', '3.13'],
            menuLabels=['3.9', '3.10', '3.11', '3.12', '3.13'],
            help="Python version for UV venv creation",
            enableExpr="me.par.Backend == 'uv'")

        self.create_parameter('Venvoptions', 'strmenu', page=page,
            label='Venv Options', default='',
            menuNames=['none', '--seed', '--system-site-packages', '--relocatable', '--seed --relocatable'],
            menuLabels=['None (Isolated)', 'Seed (pip/setuptools)', 'System Packages', 'Relocatable', 'Seed + Relocatable'],
            help="UV venv creation flags. Isolated is default. Seed adds pip/setuptools. You can type custom UV args.",
            enableExpr="me.par.Backend == 'uv'")

        self.create_parameter('Pythonexe', 'str', page=page,
            label='Detected Python', default='', readOnly=True,
            help="Auto-detected Python executable path",
            enableExpr="me.par.Backend == 'pip'")

        self.create_parameter('Custompython', 'toggle', page=page,
            label='Use Custom Python', default=False,
            help="Use a custom Python from the menu below instead of auto-detected",
            enableExpr="me.par.Backend == 'pip'")

        self.create_parameter('Pythonexemenu', 'strmenu', page=page,
            label='Custom Python', default='',
            menuNames=[''], menuLabels=['(none found)'],
            help="Select from detected Python installations",
            enableExpr="me.par.Backend == 'pip'")

        self.create_parameter('Setpython', 'pulse', page=page,
            label='Refresh Python',
            help="Re-detect available Python installations",
            enableExpr="me.par.Backend == 'pip'")

        # -------------------- INSTALL / MANAGE PACKAGES --------------------
        self.create_parameter('Actionsheader', 'header', page=page,
            label='Install / Manage Packages', section=True)

        self.create_parameter('Pippackage', 'str', page=page,
            label='Package', default='',
            help="Package name(s) to install/uninstall (space-separated)")

        self.create_parameter('Pipinstall', 'pulse', page=page,
            label='Install',
            help="Install package(s)")

        self.create_parameter('Pipuninstall', 'pulse', page=page,
            label='Uninstall',
            help="Uninstall package(s)")

        self.create_parameter('Pipupdate', 'pulse', page=page,
            label='Update',
            help="Update package(s)")

        self.create_parameter('Listinstalledpackages', 'pulse', page=page,
            label='List Packages',
            help="List all installed packages",
            section=True)

        self.create_parameter('Createreqtxt', 'pulse', page=page,
            label='Export requirements.txt',
            help="Export installed packages to requirements.txt")

        self.create_parameter('Pipinstallfromlist', 'pulse', page=page,
            label='Install from File',
            help="Install packages from requirements file",
            section=True)

        self.create_parameter('Requirementsfile', 'file', page=page,
            label='Requirements File', default='',
            help="Path to requirements.txt file")

        # -------------------- UTILITIES / DEBUG --------------------
        self.create_parameter('Utilheader', 'header', page=page,
            label='Utilities / Debug', section=True)

        self.create_parameter('Printsyspath', 'pulse', page=page,
            label='Print sys.path',
            help="Print current sys.path to textport and logger")

        self.create_parameter('Printpythoninfo', 'pulse', page=page,
            label='Print Python Info',
            help="Print venv info, CUDA, and packages to textport")

        self.create_parameter('Upgradepip', 'pulse', page=page,
            label='Upgrade pip',
            help="Upgrade pip to latest version")

        self.create_parameter('Showconsole', 'toggle', page=page,
            label='Show Console', default=False,
            help="Show console window for operations",
            section=True)

        self.create_parameter('Blockthread', 'toggle', page=page,
            label='Block Thread', default=False,
            help="Wait for operations to complete")

        self.create_parameter('Showpopups', 'toggle', page=page,
            label='Show Popups', default=False,
            help="Show popup notifications")

        # -------------------- ENVIRONMENTS PAGE --------------------
        env_page = 'Environments'

        self.create_parameter('Configlocation', 'menu', page=env_page,
            label='Save New Envs To', default='user',
            menuNames=['user', 'project_folder', 'project'],
            menuLabels=['User Config', 'Project Folder (.lops/)', 'Project (.toe)'],
            help="Where new venvs are saved. User = machine-wide, Folder = portable, Project = in .toe file.",
            section=True)

        self.create_parameter('Configpath', 'str', page=env_page,
            label='Config Path', default='', readOnly=True,
            help="Resolved path of the active config location")

        self.create_parameter('Refreshregistry', 'pulse', page=env_page,
            label='Refresh Registry',
            help="Reload venv registry from all config tiers")

        self.create_parameter('Editconfigfile', 'pulse', page=env_page,
            label='Edit Config File',
            help="Open the active config JSON in system editor")

        self.create_parameter('Seqremoval', 'menu', page=env_page,
            label='On Seq Remove', default='auto',
            menuNames=['auto', 'keep'],
            menuLabels=['Auto-remove from sys.path', 'Keep in sys.path'],
            help="What to do when a venv is removed from the sequence",
            section=True)

    # ==================== PYTHON DETECTION ====================

    def Setpython(self):
        """Detect and set Python executable paths."""
        self._set_python_executable_path()
        self._find_all_python_executables()

    def _set_python_executable_path(self):
        """Find and set the compatible Python executable."""
        compatible_python = self._find_compatible_python()
        if compatible_python:
            self.ownerComp.par.Pythonexe = compatible_python
            self._log(f"Python exe found: {compatible_python}")
        else:
            self._log("No compatible Python found. Install Python or use UV backend.", level='WARNING')

    def _find_compatible_python(self):
        """Find a standalone Python matching TD's version."""
        td_major = sys.version_info.major
        td_minor = sys.version_info.minor
        td_ver = f"{td_major}{td_minor}"

        if is_mac():
            possible_paths = [
                f"/opt/homebrew/opt/python@{td_major}.{td_minor}/bin/python{td_major}.{td_minor}",
                f"/opt/homebrew/bin/python{td_major}.{td_minor}",
                f"/Library/Frameworks/Python.framework/Versions/{td_major}.{td_minor}/bin/python{td_major}.{td_minor}",
                f"/usr/local/opt/python@{td_major}.{td_minor}/bin/python{td_major}.{td_minor}",
                f"/usr/local/bin/python{td_major}.{td_minor}",
                f"/usr/bin/python{td_major}.{td_minor}",
            ]
        else:
            try:
                username = os.getlogin()
            except Exception:
                username = os.environ.get('USERNAME', 'User')
            possible_paths = [
                f"C:/Python{td_ver}/python.exe",
                f"C:/Program Files/Python{td_ver}/python.exe",
                f"C:/Program Files (x86)/Python{td_ver}/python.exe",
                f"C:/Users/{username}/AppData/Local/Programs/Python/Python{td_ver}/python.exe",
            ]

        # Also check PATH
        path_env = os.environ.get('PATH', '')
        for path in path_env.split(os.pathsep):
            python_exe = os.path.join(path, 'python3' if is_mac() else 'python.exe')
            if os.path.isfile(python_exe):
                possible_paths.append(python_exe)

        for path in possible_paths:
            if os.path.exists(path):
                return get_short_path_name(path)

        return None

    def _find_all_python_executables(self):
        """Populate the Pythonexemenu with all found Python installations."""
        td_major = sys.version_info.major
        td_minor = sys.version_info.minor
        td_ver = f"{td_major}{td_minor}"

        if is_mac():
            possible_paths = [
                f"/opt/homebrew/opt/python@{td_major}.{td_minor}/bin/python{td_major}.{td_minor}",
                f"/opt/homebrew/bin/python{td_major}.{td_minor}",
                f"/Library/Frameworks/Python.framework/Versions/{td_major}.{td_minor}/bin/python{td_major}.{td_minor}",
                f"/usr/local/opt/python@{td_major}.{td_minor}/bin/python{td_major}.{td_minor}",
                f"/usr/local/bin/python{td_major}.{td_minor}",
                f"/usr/bin/python{td_major}.{td_minor}",
            ]
        else:
            try:
                username = os.getlogin()
            except Exception:
                username = os.environ.get('USERNAME', 'User')
            possible_paths = [
                f"C:/Python{td_ver}/python.exe",
                f"C:/Program Files/Python{td_ver}/python.exe",
                f"C:/Program Files (x86)/Python{td_ver}/python.exe",
                f"C:/Users/{username}/AppData/Local/Programs/Python/Python{td_ver}/python.exe",
            ]

        # Check PATH
        path_env = os.environ.get('PATH', '')
        for path in path_env.split(os.pathsep):
            python_exe = os.path.join(path, 'python3' if is_mac() else 'python.exe')
            if os.path.isfile(python_exe):
                possible_paths.append(python_exe)

        found_exes = [p for p in possible_paths if os.path.exists(p)]

        if found_exes:
            labels = []
            for path in found_exes:
                if 'Python' in path:
                    labels.append('Python' + path.split('Python')[-1].split('/')[0].split('\\')[0])
                else:
                    labels.append(os.path.basename(path))
            self.ownerComp.par.Pythonexemenu.menuNames = found_exes
            self.ownerComp.par.Pythonexemenu.menuLabels = labels
        else:
            self.ownerComp.par.Pythonexemenu.menuNames = ['']
            self.ownerComp.par.Pythonexemenu.menuLabels = ['(none found)']

    def _get_system_python(self):
        """Get the Python executable to use for pip backend venv creation."""
        if self.ownerComp.par.Custompython.eval():
            custom = self.ownerComp.par.Pythonexemenu.eval()
            if custom and os.path.exists(custom):
                return get_short_path_name(custom)
        detected = self.ownerComp.par.Pythonexe.eval()
        if detected and os.path.exists(detected):
            return get_short_path_name(detected)
        return None

    # ==================== HELPER METHODS ====================

    def Venvoptions(self):
        """Handle Venvoptions parameter change - convert 'none' to empty string."""
        if self.ownerComp.par.Venvoptions.eval() == 'none':
            self.ownerComp.par.Venvoptions = ''

    def _resolve_venv_path(self, venv_path: str = None) -> str:
        """
        Resolve the venv path to operate on.

        Args:
            venv_path: Optional explicit path. If provided, used directly.
                       If None, resolves from Selectedenv or Basefolder.

        Returns:
            Resolved venv path, or empty string if none available.
        """
        # Explicit path provided - use it directly
        if venv_path:
            return venv_path

        # Check Selectedenv parameter
        selected = self.ownerComp.par.Selectedenv.eval()
        if selected and selected != 'custom':
            # Selected is a registered environment path
            return selected

        # Fall back to custom basefolder
        base = self.ownerComp.par.Basefolder.eval()
        if base:
            return os.path.join(base, 'venv')

        return ''

    def _check_venv_path(self, venv_path: str = None) -> str:
        """
        Resolve and validate venv path. Logs error if none available.

        Args:
            venv_path: Optional explicit path to use.

        Returns:
            Resolved venv path, or empty string (with error logged) if none.
        """
        resolved = self._resolve_venv_path(venv_path)
        if not resolved:
            self._log("Error: No environment selected. Set Selectedenv or Basefolder.", level='ERROR')
        return resolved

    def _update_venv_python_exe(self):
        """Update display after venv operations."""
        venv_path = self._resolve_venv_path()
        if venv_path and os.path.exists(venv_path):
            exe = self.venv.get_python_exe(venv_path)
            self._log(f"Venv Python: {exe}" if exe else "Venv Python not found")

    def _check_backend_available(self) -> str:
        """
        Check if the selected backend is available. If UV is selected but not
        available, prompt user to switch to pip.

        Returns:
            str: Backend to use ('uv', 'pip') or None if user cancelled
        """
        backend = self.ownerComp.par.Backend.eval()

        if backend == 'uv':
            if not self.venv.check_uv_available():
                # UV not available - prompt user
                result = ui.messageBox(
                    'UV Not Found',
                    'UV package manager is not installed on this system.\n\n'
                    'Would you like to switch to pip (standard Python package manager) instead?\n\n'
                    'Note: pip requires Python to be installed separately.',
                    buttons=['Switch to pip', 'Cancel']
                )
                if result == 0:  # Switch to pip
                    self.ownerComp.par.Backend = 'pip'
                    self._log("Switched backend to pip (UV not available)")
                    return 'pip'
                else:
                    self._log("Operation cancelled - UV not available", level='WARNING')
                    return None
        return backend

    # ==================== BACKWARDS-COMPATIBLE API ====================

    def Createvenv(self, folder: str = None):
        """Create virtual environment."""
        # Check backend availability first
        backend = self._check_backend_available()
        if not backend:
            return False

        target_folder = folder or self.ownerComp.par.Basefolder.eval()

        # Check if Selectedenv is a valid existing venv
        selected_env = self.ownerComp.par.Selectedenv.eval()
        if selected_env and selected_env != 'custom' and os.path.exists(selected_env):
            self._log(f"Venv already exists at: {selected_env}")
            return True

        needs_folder_selection = selected_env in ('', 'new', 'custom')

        # If no folder and Selectedenv needs selection, show folder dialog
        if not target_folder and needs_folder_selection:
            selected = ui.chooseFolder(title="Select folder for new virtual environment")
            if selected:
                target_folder = selected
                self._log(f"User selected folder: {target_folder}")
            else:
                self._log("Folder selection cancelled", level='WARNING')
                return False

        if not target_folder:
            self._log("Error: No folder specified. Set Base Folder or select 'Custom'.", level='ERROR')
            return False

        venv_path = os.path.join(target_folder, 'venv')

        if backend == 'uv':
            # Venvoptions contains the exact UV flags (e.g., '--seed', '--seed --relocatable')
            venv_option = self.ownerComp.par.Venvoptions.eval().strip()
            extra_args = venv_option.split() if venv_option else []

            option_label = f" ({venv_option})" if venv_option else ""
            self._log(f"Creating venv at {venv_path} with UV (Python {self.ownerComp.par.Pythonversion.eval()}){option_label}...")
            success = self.venv.create_venv(
                venv_path=venv_path,
                backend='uv',
                python_version=self.ownerComp.par.Pythonversion.eval(),
                extra_args=extra_args if extra_args else None
            )
        else:
            system_python = self._get_system_python()
            if not system_python:
                self._log("Error: No system Python found. Use UV backend or install Python.", level='ERROR')
                return False
            self._log(f"Creating venv at {venv_path} with pip (using {system_python})...")
            success = self.venv.create_venv(
                venv_path=venv_path,
                backend='pip',
                system_python=system_python
            )

        if success:
            self._log(f"Venv created successfully at {venv_path}")
            self._update_venv_python_exe()
            # Auto-add to registry if sequence parameters exist
            name = os.path.basename(target_folder)
            if self._add_to_registry(name, venv_path):
                # Update Selectedenv to point to newly created venv
                self.ownerComp.par.Selectedenv = venv_path
                # If Addtosyspath toggle is on, set sequence import toggle (delayed 1 frame)
                if self.ownerComp.par.Addtosyspath.eval():
                    run("args[0]._set_sequence_import_for_path(args[1], True)",
                        self, venv_path, delayFrames=1)
        else:
            self._log(f"Failed to create venv at {venv_path}", level='ERROR')

        return success

    def Pipinstall(self, package_name: str = None, venv_path: str = None):
        """Install package(s)."""
        backend = self._check_backend_available()
        if not backend:
            return False
        resolved = self._check_venv_path(venv_path)
        if not resolved:
            return False
        packages = package_name or self.ownerComp.par.Pippackage.eval()
        if not packages:
            self._log("Error: No package specified", level='ERROR')
            return False
        package_list = packages.split() if isinstance(packages, str) else [packages]
        self._log(f"Installing: {', '.join(package_list)} to {resolved}")
        success = self.venv.install_packages(
            venv_path=resolved,
            packages=package_list,
            backend=backend
        )
        if success:
            self._log(f"Installed: {', '.join(package_list)}")
        else:
            self._log(f"Failed to install: {', '.join(package_list)}", level='ERROR')
        return success

    def Pipuninstall(self, package_name: str = None, venv_path: str = None):
        """Uninstall package(s)."""
        backend = self._check_backend_available()
        if not backend:
            return False
        resolved = self._check_venv_path(venv_path)
        if not resolved:
            return False
        packages = package_name or self.ownerComp.par.Pippackage.eval()
        if not packages:
            self._log("Error: No package specified", level='ERROR')
            return False
        package_list = packages.split() if isinstance(packages, str) else [packages]
        self._log(f"Uninstalling: {', '.join(package_list)} from {resolved}")
        success = self.venv.uninstall_packages(
            venv_path=resolved,
            packages=package_list,
            backend=backend
        )
        if success:
            self._log(f"Uninstalled: {', '.join(package_list)}")
        else:
            self._log(f"Failed to uninstall: {', '.join(package_list)}", level='ERROR')
        return success

    def Pipupdate(self, package_name: str = None, venv_path: str = None):
        """Update package(s)."""
        backend = self._check_backend_available()
        if not backend:
            return False
        resolved = self._check_venv_path(venv_path)
        if not resolved:
            return False
        packages = package_name or self.ownerComp.par.Pippackage.eval()
        if not packages:
            self._log("Error: No package specified", level='ERROR')
            return False
        package_list = packages.split() if isinstance(packages, str) else [packages]
        self._log(f"Updating: {', '.join(package_list)} in {resolved}")
        success = self.venv.install_packages(
            venv_path=resolved,
            packages=package_list,
            backend=backend,
            extra_args=['--upgrade']
        )
        if success:
            self._log(f"Updated: {', '.join(package_list)}")
        else:
            self._log(f"Failed to update: {', '.join(package_list)}", level='ERROR')
        return success

    def Pipinstallfromlist(self, requirements_file_path: str = None, venv_path: str = None):
        """Install packages from requirements file."""
        backend = self._check_backend_available()
        if not backend:
            return False
        resolved = self._check_venv_path(venv_path)
        if not resolved:
            return False
        req_file = requirements_file_path or self.ownerComp.par.Requirementsfile.eval()
        if not req_file or not os.path.exists(req_file):
            self._log(f"Error: Requirements file not found: {req_file}", level='ERROR')
            return False
        self._log(f"Installing from {req_file} to {resolved}...")
        success = self.venv.install_packages(
            venv_path=resolved,
            packages=['-r', req_file],
            backend=backend
        )
        if success:
            self._log(f"Installed packages from {req_file}")
        else:
            self._log(f"Failed to install from {req_file}", level='ERROR')
        return success

    def Listinstalledpackages(self, venv_path: str = None) -> list:
        """List all installed packages in the venv."""
        backend = self._check_backend_available()
        if not backend:
            return []
        resolved = self._check_venv_path(venv_path)
        if not resolved:
            return []
        packages = self.venv.list_packages(
            venv_path=resolved,
            backend=backend
        )
        if not packages:
            self._log("No Packages Installed / Venv Exists")
        else:
            pkg_strs = []
            for pkg in packages:
                name = pkg.get('name', str(pkg))
                version = pkg.get('version', '')
                pkg_strs.append(f"{name}=={version}" if version else name)
            self._log(f"Packages ({len(packages)}): {', '.join(pkg_strs)}")
        return packages

    def Createreqtxt(self, venv_path: str = None, output_path: str = None):
        """Export installed packages to requirements.txt."""
        backend = self._check_backend_available()
        if not backend:
            return False
        resolved = self._check_venv_path(venv_path)
        if not resolved:
            return False
        packages = self.venv.list_packages(
            venv_path=resolved,
            backend=backend
        )
        # Determine output path - use provided, or derive from venv parent, or basefolder
        if output_path:
            req_path = output_path
        else:
            venv_parent = os.path.dirname(resolved)
            req_path = os.path.join(venv_parent, 'requirements.txt')
        try:
            with open(req_path, 'w') as f:
                for pkg in packages:
                    name = pkg.get('name', str(pkg))
                    version = pkg.get('version', '')
                    f.write(f"{name}=={version}\n" if version else f"{name}\n")
            self._log(f"Exported {len(packages)} packages to {req_path}")
            return True
        except Exception as e:
            self._log(f"Failed to export requirements: {e}", level='ERROR')
            return False

    def Upgradepip(self, venv_path: str = None):
        """Upgrade pip to latest version."""
        resolved = self._check_venv_path(venv_path)
        if not resolved:
            return False
        self._log(f"Upgrading pip in {resolved}...")
        success = self.venv.install_packages(
            venv_path=resolved,
            packages=['pip'],
            backend='pip',
            extra_args=['--upgrade']
        )
        if success:
            self._log("pip upgraded successfully")
        else:
            self._log("Failed to upgrade pip", level='ERROR')
        return success

    def Addtosyspath(self, venv_path: str = None):
        """
        Legacy API: if venv_path provided, add directly to sys.path.
        Toggle mode: no-op - toggle is only read by Createvenv to decide
        whether to set sequence import toggle for new venvs.
        Sequence import toggles are the source of truth for sys.path.
        """
        if venv_path:
            return self._add_to_syspath_direct(venv_path)
        # Toggle change - no action needed, just a preference for Createvenv

    def _add_to_syspath_direct(self, venv_path: str, index=None):
        """
        Directly add venv site-packages to sys.path and track in DAT.

        Args:
            venv_path: Path to the venv directory
            index: Where to insert in sys.path:
                   - None or 'end': append to end (default)
                   - 'start' or 0: insert at beginning
                   - int: insert at specific index
        """
        site_packages = self.venv.get_site_packages(venv_path)
        if not site_packages or not os.path.exists(site_packages):
            self._log(f"Error: site-packages not found: {site_packages}", level='ERROR')
            return False
        if site_packages in sys.path:
            self._log(f"Already in sys.path: {site_packages}")
            self._add_to_managed_dat(site_packages)  # Ensure tracked
            return True

        # Handle index placement like original
        if index == 'start' or index == 0:
            sys.path.insert(0, site_packages)
            self._add_to_managed_dat(site_packages)
            self._log(f"Added to sys.path[0]: {site_packages}")
        elif index is None or index == 'end' or (isinstance(index, int) and index >= len(sys.path)):
            sys.path.append(site_packages)
            self._add_to_managed_dat(site_packages)
            self._log(f"Added to sys.path (end): {site_packages}")
        elif isinstance(index, int):
            sys.path.insert(index, site_packages)
            self._add_to_managed_dat(site_packages)
            self._log(f"Added to sys.path[{index}]: {site_packages}")
        else:
            self._log(f"Invalid index: {index}. Must be None, 'start', 'end', or int.", level='ERROR')
            return False
        return True

    def _remove_from_syspath(self, venv_path: str):
        """Remove venv site-packages from sys.path and untrack from DAT."""
        site_packages = self.venv.get_site_packages(venv_path)
        if site_packages and site_packages in sys.path:
            sys.path.remove(site_packages)
            self._remove_from_managed_dat(site_packages)
            self._log(f"Removed from sys.path: {site_packages}")
            return True
        return False

    # ==================== LEGACY COMPAT METHODS ====================
    # These methods provide backwards compatibility with existing operators
    # that relied on the old PythonExternalLibExt API.

    def check_venv_exists(self):
        """
        Legacy API: Check if venv exists and return (python_exe, base_folder) tuple.

        Returns:
            tuple: (venv_python_path, base_folder) or (False, None) if not found
        """
        base_folder = self.ownerComp.par.Basefolder.eval()
        if not base_folder or base_folder == '':
            # Check selected env first
            selected = self.ownerComp.par.Selectedenv.eval()
            if selected and selected != 'custom' and os.path.exists(selected):
                # Selected is a venv path directly
                venv_path = selected
                base_folder = os.path.dirname(venv_path)
            else:
                if self.ownerComp.par.Showpopups.eval():
                    if not self.select_install_popup():
                        return False, None
                    base_folder = self.ownerComp.par.Basefolder.eval()
                else:
                    return False, None

        base_folder = tdu.expandPath(base_folder)

        # Check for venv python executable
        if is_mac():
            venv_python = os.path.join(base_folder, 'venv', 'bin', 'python')
            if not os.path.exists(venv_python):
                venv_python = os.path.join(base_folder, '.venv', 'bin', 'python')
                if not os.path.exists(venv_python):
                    venv_python = os.path.join(base_folder, 'venv', 'python')
        else:
            venv_python = os.path.join(base_folder, 'venv', 'Scripts', 'python.exe')
            if not os.path.exists(venv_python):
                venv_python = os.path.join(base_folder, '.venv', 'Scripts', 'python.exe')
                if not os.path.exists(venv_python):
                    venv_python = os.path.join(base_folder, 'venv', 'python.exe')

        if not os.path.exists(venv_python):
            self._log("Virtual environment Python executable not found.", level='WARNING')
            return False, None

        venv_python = os.path.normpath(venv_python)
        base_folder = os.path.normpath(base_folder)
        return venv_python, base_folder

    def get_venv_site_packages_path(self):
        """
        Legacy API: Get the site-packages path for the current venv.

        Returns:
            str: Path to site-packages, or None if not found
        """
        venv_python, base_folder = self.check_venv_exists()
        if not venv_python:
            return None

        if is_mac():
            venv_path = os.path.join(base_folder, 'venv', 'lib',
                                     f'python{sys.version_info.major}.{sys.version_info.minor}',
                                     'site-packages')
        else:
            venv_path = os.path.join(base_folder, 'venv', 'Lib', 'site-packages')

        if os.path.exists(venv_path):
            return venv_path
        else:
            self._log(f'Venv site-packages path does not exist: {venv_path}', level='WARNING')
            return None

    def list_installed_packages(self):
        """
        Legacy API: List installed packages and return as list of strings.

        Returns:
            list: Package strings in format "name==version"
        """
        venv_python, base_folder = self.check_venv_exists()
        if not venv_python:
            return []

        try:
            if is_mac():
                command = f"unset PYTHONPATH && {venv_python} -m pip list"
                process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, shell=True)
            else:
                process = subprocess.Popen([venv_python, '-m', 'pip', 'list'],
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            result, error = process.communicate()
            if process.returncode != 0:
                self._log(f"Error listing packages: {error.decode('utf-8')}", level='ERROR')
                return []

            result = result.decode('utf-8')
            # Return the list of packages, skipping the header rows
            return result.split('\n')[2:]
        except Exception as e:
            self._log(f"Exception listing packages: {e}", level='ERROR')
            return []

    def print_cuda_availability(self):
        """
        Legacy API: Print CUDA availability info.

        Returns:
            str: CUDA info string
        """
        venv_python, _ = self.check_venv_exists()
        if not venv_python:
            msg = "CUDA Availability: No virtual environment detected"
            print(msg)
            return msg

        cuda_check_script = '''
import sys
try:
    import torch
    print(f"PyTorch version: {torch.__version__}")
    print(f"CUDA available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"CUDA version: {torch.version.cuda}")
        print(f"Number of CUDA devices: {torch.cuda.device_count()}")
except ImportError:
    print("PyTorch is not installed")
except Exception as e:
    print(f"Error checking CUDA: {e}")
'''
        try:
            result = subprocess.run([venv_python, "-c", cuda_check_script],
                                   capture_output=True, text=True, check=True)
            cuda_info = result.stdout.strip()
            print(cuda_info)
            return cuda_info
        except subprocess.CalledProcessError as e:
            cuda_info = f"Error checking CUDA: {e.stderr.strip()}"
            print(cuda_info)
            return cuda_info

    def select_install_popup(self):
        """
        Legacy API: Show popup to select/create venv folder.

        Returns:
            bool: True if folder was selected and venv created, False otherwise
        """
        if hasattr(self, 'popup_is_open') and self.popup_is_open:
            return False

        self.popup_is_open = True
        message = """
        The Basefolder parameter is not set.
        Would you like to select or create a new folder?
        """
        buttons = ['Select/Create Folder', 'Cancel']
        choice = ui.messageBox('Virtual Environment Not Found', message, buttons=buttons)
        self.popup_is_open = False

        if choice == 0:
            blocked = self.ownerComp.par.Blockthread.eval()
            self.ownerComp.par.Blockthread = True
            created = self.Createvenv()
            self.ownerComp.par.Blockthread = blocked
            return created
        else:
            return False

    # ==================== MANAGED SYSPATHS DAT ====================

    def _get_managed_dat(self):
        """Get or create the managed_syspaths DAT."""
        dat = self.ownerComp.op('managed_syspaths')
        if dat is None:
            dat = self.ownerComp.create('textDAT', 'managed_syspaths')
            dat.text = ''
            dat.nodeX = 400
            dat.nodeY = -200
            dat.viewer = False
        return dat

    def _get_managed_paths(self) -> set:
        """Get set of paths we've added to sys.path."""
        dat = self._get_managed_dat()
        paths = set()
        for line in dat.text.strip().split('\n'):
            line = line.strip()
            if line:
                paths.add(line)
        return paths

    def _add_to_managed_dat(self, site_packages: str):
        """Add a site-packages path to the managed DAT."""
        dat = self._get_managed_dat()
        paths = self._get_managed_paths()
        if site_packages not in paths:
            paths.add(site_packages)
            dat.text = '\n'.join(sorted(paths))

    def _remove_from_managed_dat(self, site_packages: str):
        """Remove a site-packages path from the managed DAT."""
        dat = self._get_managed_dat()
        paths = self._get_managed_paths()
        if site_packages in paths:
            paths.discard(site_packages)
            dat.text = '\n'.join(sorted(paths))

    def onSequenceSizeChange(self):
        """
        Called externally when sequence size changes.
        Removes orphaned paths from sys.path if Seqremoval is 'auto'.
        Also refreshes the Selectedenv menu.
        """
        # Always refresh the menu when sequence changes
        self.Refreshenvmenu()

        if self.ownerComp.par.Seqremoval.eval() != 'auto':
            return

        # Get current sequence paths
        current_venv_paths = {e['path'] for e in self._get_sequence_entries()}

        # Get managed site-packages paths
        managed_paths = self._get_managed_paths()

        # Find orphaned paths (in managed but venv no longer in sequence)
        for site_pkg in list(managed_paths):
            # Reverse lookup: find venv path from site-packages
            # site_packages is like /path/to/venv/Lib/site-packages
            # venv_path is like /path/to/venv
            venv_path = None
            for vp in current_venv_paths:
                if self.venv.get_site_packages(vp) == site_pkg:
                    venv_path = vp
                    break

            if venv_path is None:
                # This site-packages' venv is no longer in sequence - remove it
                if site_pkg in sys.path:
                    sys.path.remove(site_pkg)
                    self._log(f"Auto-removed orphaned path: {site_pkg}")
                self._remove_from_managed_dat(site_pkg)

    def _find_sequence_index_for_path(self, venv_path: str):
        """Find sequence index for a given venv path."""
        entries = self._get_sequence_entries()
        for entry in entries:
            if entry['path'] == venv_path:
                return entry['index']
        return None

    def _set_sequence_import_for_path(self, venv_path: str, value: bool):
        """Set the sequence import toggle for a given venv path and trigger the handler."""
        seq_index = self._find_sequence_index_for_path(venv_path)
        if seq_index is not None:
            import_par = getattr(self.ownerComp.par, f'Venv{seq_index}import', None)
            if import_par is not None:
                import_par.val = value
                # Programmatic changes don't trigger extensionParExec, so call handler directly
                self.OnVenvImportChange(seq_index, value)

    def Openvenv(self, venv_path: str = None):
        """Open terminal with venv activated."""
        resolved = self._check_venv_path(venv_path)
        if not resolved:
            return False
        if not os.path.exists(resolved):
            self._log(f"Error: Venv not found at {resolved}", level='ERROR')
            return False
        self._log(f"Opening console for {resolved}...")
        self.venv.open_console(resolved)
        return True

    def RunPythonScript(self, script_content: str = None, venv_path: str = None, wait: bool = None, capture: bool = True):
        """Run a Python script in the venv."""
        resolved = self._check_venv_path(venv_path)
        if not resolved:
            return None
        if wait is None:
            wait = self.ownerComp.par.Blockthread.eval()
        return self.venv.run_script(
            venv_path=resolved,
            script=script_content,
            wait=wait,
            capture=capture
        )

    def RunShellCommand(self, command: str = None, venv_path: str = None, wait: bool = None, capture: bool = True):
        """Run a shell command in the venv context."""
        resolved = self._check_venv_path(venv_path)
        if not resolved:
            return None
        if wait is None:
            wait = self.ownerComp.par.Blockthread.eval()
        return self.venv.run_command(
            venv_path=resolved,
            command=command,
            wait=wait,
            capture=capture
        )

    # ==================== PRINT INFO METHODS ====================

    def Printsyspath(self):
        """Print sys.path to textport and logger."""
        venv_site_packages = self.venv.get_site_packages(self._resolve_venv_path()) if self._resolve_venv_path() else None
        venv_in_path = False
        venv_position = -1

        print("-" * 50)
        print("Current sys.path:")
        for i, p in enumerate(sys.path):
            print(f"  [{i}] {p}")
            self._log(f"sys.path[{i}]: {p}")
            if venv_site_packages and p == venv_site_packages:
                venv_in_path = True
                venv_position = i

        print(f"\nTotal paths: {len(sys.path)}")
        if venv_site_packages:
            if venv_in_path:
                print(f"Venv site-packages is at position {venv_position}")
                self._log(f"Venv site-packages at position {venv_position}")
            else:
                print("Venv site-packages is NOT in sys.path")
                self._log("Venv site-packages NOT in sys.path")
        print("-" * 50)
        self._log("sys.path printed to textport (Alt+T)")

    def Printpythoninfo(self):
        """Print comprehensive Python/venv info to textport and logger."""
        print("-" * 50)
        print("Python Environment Information")
        print("-" * 50)

        # Venv info
        if self._resolve_venv_path() and os.path.exists(self._resolve_venv_path()):
            venv_info = self.venv.check_venv(self._resolve_venv_path())
            print(f"\nVenv Path: {self._resolve_venv_path()}")
            print(f"Venv Python: {venv_info.get('python_exe', 'N/A')}")
            print(f"Venv Version: {venv_info.get('python_version', 'N/A')}")
            print(f"Site-packages: {venv_info.get('site_packages', 'N/A')}")
            self._log(f"Venv: {self._resolve_venv_path()}")
            self._log(f"Venv Python: {venv_info.get('python_exe', 'N/A')}")
            self._log(f"Venv Version: {venv_info.get('python_version', 'N/A')}")
        else:
            print("\nNo virtual environment found at Base Folder")
            self._log("No venv found")

        # System Python
        detected_python = self.ownerComp.par.Pythonexe.eval()
        print(f"\nSystem Python (detected): {detected_python}")
        self._log(f"System Python (detected): {detected_python}")
        print(f"TouchDesigner Python: {sys.executable}")
        self._log(f"TouchDesigner Python: {sys.executable}")
        print(f"TD Python Version: {sys.version}")
        self._log(f"TD Python Version: {sys.version}")

        # CUDA check
        print("\n--- CUDA Availability ---")
        self._print_cuda_info()

        # Installed packages
        if self._resolve_venv_path() and os.path.exists(self._resolve_venv_path()):
            print("\n--- Installed Packages ---")
            packages = self.venv.list_packages(self._resolve_venv_path(), self.ownerComp.par.Backend.eval())
            for pkg in packages:
                name = pkg.get('name', str(pkg))
                version = pkg.get('version', '')
                line = f"  {name}=={version}" if version else f"  {name}"
                print(line)
                self._log(line)
            print(f"\nTotal packages: {len(packages)}")
            self._log(f"Total packages: {len(packages)}")

        print("-" * 50)
        self._log("Python info printed to textport (Alt+T)")

    def _print_cuda_info(self):
        """Print CUDA availability by running a check in the venv."""
        if not self._resolve_venv_path() or not os.path.exists(self._resolve_venv_path()):
            print("No venv - cannot check CUDA")
            return

        cuda_script = """
import sys
try:
    import torch
    print(f"PyTorch version: {torch.__version__}")
    print(f"CUDA available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"CUDA version: {torch.version.cuda}")
        print(f"CUDA devices: {torch.cuda.device_count()}")
except ImportError:
    print("PyTorch not installed")
except Exception as e:
    print(f"Error: {e}")
"""
        result = self.venv.run_script(self._resolve_venv_path(), cuda_script, wait=True, capture=True)
        if result and result.get('success'):
            for line in result.get('stdout', '').strip().split('\n'):
                print(f"  {line}")
                self._log(line)
        else:
            print("  Could not check CUDA")
            self._log("CUDA check failed")

    def Resetop(self):
        """Reinitialize the operator."""
        self._log("Resetting operator...")
        self.ownerComp.par.Basefolder = ''
        self.ownerComp.par.Pippackage = ''
        self.ownerComp.par.Venvoptions = ''
        self.ownerComp.par.Selectedenv = 'custom'

        # Clear project config DAT
        if hasattr(self, 'project_config_dat') and self.project_config_dat:
            self.project_config_dat.text = '{}'

        # Clear sequence - set to 1 block and clear index 0
        venv_seq = getattr(self.ownerComp.seq, 'Venv', None)
        if venv_seq is not None:
            venv_seq.numBlocks = 1
            # Clear the first block's values
            if hasattr(self.ownerComp.par, 'Venv0name'):
                self.ownerComp.par.Venv0name = ''
            if hasattr(self.ownerComp.par, 'Venv0path'):
                self.ownerComp.par.Venv0path = ''
            if hasattr(self.ownerComp.par, 'Venv0import'):
                self.ownerComp.par.Venv0import = False
            if hasattr(self.ownerComp.par, 'Venv0status'):
                self.ownerComp.par.Venv0status = ''

        self.__init__(self.ownerComp)

    # ==================== REGISTRY SYSTEM ====================

    def _get_sequence_entries(self) -> list:
        """
        Read all Venv sequence entries from user-created sequence parameters.
        Returns list of dicts: [{index, name, path, import, status}, ...]
        """
        entries = []
        idx = 0
        while True:
            # Check if this sequence index exists
            name_par = getattr(self.ownerComp.par, f'Venv{idx}name', None)
            if name_par is None:
                break
            path_par = getattr(self.ownerComp.par, f'Venv{idx}path', None)
            import_par = getattr(self.ownerComp.par, f'Venv{idx}import', None)
            status_par = getattr(self.ownerComp.par, f'Venv{idx}status', None)

            entry = {
                'index': idx,
                'name': name_par.eval() if name_par else '',
                'path': path_par.eval() if path_par else '',
                'import': import_par.eval() if import_par else False,
                'status': status_par.eval() if status_par else ''
            }
            entries.append(entry)
            idx += 1
        return entries

    def _find_next_sequence_slot(self) -> int:
        """Find the next available sequence slot (first empty or end)."""
        entries = self._get_sequence_entries()
        # Find first entry with empty path
        for entry in entries:
            if not entry['path']:
                return entry['index']
        # Return count (next slot after last)
        return len(entries)

    def Refreshenvmenu(self):
        """Refresh Selectedenv menu from sequence parameters."""
        entries = self._get_sequence_entries()

        menu_names = ['custom']
        menu_labels = ['Custom / New Venv']

        for entry in entries:
            if entry['path']:
                # Use path as value, name or path as label
                menu_names.append(entry['path'])
                label = entry['name'] if entry['name'] else os.path.basename(entry['path'])
                menu_labels.append(label)

        self.ownerComp.par.Selectedenv.menuNames = menu_names
        self.ownerComp.par.Selectedenv.menuLabels = menu_labels
        self._log(f"Refreshed environment menu: {len(entries)} registered")

    def _import_sequence_venvs(self):
        """On init, add all venvs with import=True to sys.path.
        Validates paths exist before importing — handles cross-computer project transfers."""
        entries = self._get_sequence_entries()
        for entry in entries:
            if entry['import'] and entry['path']:
                if not os.path.exists(entry['path']):
                    self._log(f"Venv path not found (skipping import): {entry['path']}", level='WARNING')
                    self._update_sequence_status(entry['index'])
                    continue
                try:
                    self._add_to_syspath_direct(entry['path'])
                except Exception as e:
                    self._log(f"Failed to import venv {entry['path']}: {e}", level='ERROR')
                self._update_sequence_status(entry['index'])

    def _update_sequence_status(self, index: int):
        """Update status for a sequence entry (max 20 chars). Shows [tier] + version/state."""
        path_par = getattr(self.ownerComp.par, f'Venv{index}path', None)
        status_par = getattr(self.ownerComp.par, f'Venv{index}status', None)
        import_par = getattr(self.ownerComp.par, f'Venv{index}import', None)

        if not path_par or not status_par:
            return

        venv_path = path_par.eval()
        if not venv_path:
            status_par.val = ''
            return

        # Get tier label
        layer = self._get_entry_layer(index)
        parts = [f"[{layer}]"]

        # Check if imported
        if import_par and import_par.eval():
            site_pkg = self.venv.get_site_packages(venv_path)
            if site_pkg and site_pkg in sys.path:
                parts.append('✓')
            else:
                parts.append('○')

        # Get Python version or stale indicator
        if os.path.exists(venv_path):
            info = self.venv.check_venv(venv_path)
            ver = info.get('python_version', '')
            if ver:
                ver_clean = ver.replace('Python ', '')
                ver_parts = ver_clean.split('.')
                if len(ver_parts) >= 2:
                    parts.append(f"{ver_parts[0]}.{ver_parts[1]}")
                else:
                    parts.append(ver_clean)
        else:
            parts.append('STALE')

        status_par.val = ' '.join(parts)[:20]

    def Updateallstatus(self):
        """Update status for all sequence entries."""
        entries = self._get_sequence_entries()
        for entry in entries:
            self._update_sequence_status(entry['index'])
        self._log(f"Updated status for {len(entries)} environments")

    def _is_venv_in_sequence(self, venv_path: str) -> bool:
        """Check if a venv path is already registered in the sequence."""
        entries = self._get_sequence_entries()
        for entry in entries:
            if entry['path'] == venv_path:
                return True
        return False

    # ==================== CONFIG TIER METHODS ====================

    def _load_and_rebuild_registry(self):
        """Load merged venvs from all config tiers and rebuild sequence display."""
        project_venvs = self._read_project_venvs()
        merged = self.storage.get_merged_venvs(project_venvs)

        # Migrate basefolder if it's not in any tier yet
        self._maybe_migrate_basefolder(merged)

        # Re-merge after potential migration
        if self._basefolder_migrated:
            project_venvs = self._read_project_venvs()
            merged = self.storage.get_merged_venvs(project_venvs)
            self._basefolder_migrated = False

        # Rebuild sequence display from merged list
        self._rebuild_sequence_display(merged)

        # Update config path display
        self._update_config_path_display()

        # Log stale paths
        stale = [e for e in merged if e.get('stale')]
        if stale:
            names = ', '.join(e['name'] for e in stale)
            self._log(f"{len(stale)} venv(s) have stale paths: {names}", level='WARNING')

    def _rebuild_sequence_display(self, entries: list):
        """Rebuild sequence parameters from merged entry list (display only)."""
        venv_seq = getattr(self.ownerComp.seq, 'Venv', None)
        if venv_seq is None:
            return

        # Set sequence block count to match entries (min 1)
        target_blocks = max(1, len(entries))
        venv_seq.numBlocks = target_blocks

        # Clear entry layers tracker
        self._entry_layers = {}

        for i, entry in enumerate(entries):
            name_par = getattr(self.ownerComp.par, f'Venv{i}name', None)
            path_par = getattr(self.ownerComp.par, f'Venv{i}path', None)
            import_par = getattr(self.ownerComp.par, f'Venv{i}import', None)
            status_par = getattr(self.ownerComp.par, f'Venv{i}status', None)

            resolved = entry.get('resolved_path', entry.get('path', ''))

            if name_par is not None:
                name_par.val = entry.get('name', '')
            if path_par is not None:
                path_par.val = resolved
            if import_par is not None:
                import_par.val = entry.get('import', False)

            # Track layer for this index
            self._entry_layers[i] = entry.get('source_layer', 'user')

            # Build status with tier label
            if status_par is not None:
                layer = entry.get('source_layer', '')
                stale = entry.get('stale', False)
                if stale:
                    status_par.val = f"[{layer}] STALE"[:20]
                elif resolved and os.path.exists(resolved):
                    info = self.venv.check_venv(resolved)
                    ver = info.get('python_version', '')
                    if ver:
                        ver_short = ver.replace('Python ', '')
                        # Just major.minor
                        parts = ver_short.split('.')
                        if len(parts) >= 2:
                            ver_short = f"{parts[0]}.{parts[1]}"
                        status_par.val = f"[{layer}] {ver_short}"[:20]
                    else:
                        status_par.val = f"[{layer}]"[:20]
                else:
                    status_par.val = f"[{layer}]"[:20]

        # Clear any remaining slots if we shrunk
        if len(entries) == 0:
            for par_name in ('Venv0name', 'Venv0path', 'Venv0status'):
                par = getattr(self.ownerComp.par, par_name, None)
                if par is not None:
                    par.val = ''
            import_par = getattr(self.ownerComp.par, 'Venv0import', None)
            if import_par is not None:
                import_par.val = False

    def _maybe_migrate_basefolder(self, existing_merged: list):
        """Register Basefolder venv to user tier if it exists and isn't in any tier."""
        self._basefolder_migrated = False
        base = self.ownerComp.par.Basefolder.eval()
        if not base:
            return

        venv_path = os.path.join(base, 'venv')
        if not os.path.exists(venv_path):
            if os.path.exists(os.path.join(base, 'Scripts', 'python.exe')) or \
               os.path.exists(os.path.join(base, 'bin', 'python')):
                venv_path = base
            else:
                return

        # Check if this path already exists in merged
        all_paths = {e.get('resolved_path', e.get('path', '')) for e in existing_merged}
        if venv_path in all_paths:
            return

        # Register to user tier
        name = os.path.basename(base)
        config = {
            'path': venv_path,
            'import': self.ownerComp.par.Addtosyspath.eval(),
            'description': 'Basefolder venv'
        }
        self.storage.add_venv(name, config, 'user')
        self._log(f"Migrated Basefolder venv to user config: {venv_path}")
        self._basefolder_migrated = True

    def _maybe_migrate_from_legacy_sequence(self):
        """One-time migration from old sequence-based storage to JSON.
        Runs if JSON files are empty but sequence has entries."""
        # Check if any JSON entries already exist
        project_venvs = self._read_project_venvs()
        merged = self.storage.get_merged_venvs(project_venvs)
        if len(merged) > 0:
            return  # Already have config data, skip migration

        # Read legacy sequence entries
        entries = self._get_sequence_entries()
        valid_entries = [e for e in entries if e['path']]

        if not valid_entries:
            return

        self._log(f"Migrating {len(valid_entries)} sequence entries to user config JSON...")

        # Write all to user tier
        user_venvs = {}
        for entry in valid_entries:
            name = entry['name'] or os.path.basename(entry['path'])
            # Deduplicate names
            base_name = name
            counter = 1
            while name in user_venvs:
                name = f"{base_name}_{counter}"
                counter += 1
            user_venvs[name] = {
                'path': entry['path'],
                'import': entry['import'],
                'description': 'migrated from sequence'
            }

        self.storage.write_venvs(user_venvs, 'user')
        self._log(f"Migration complete: {len(user_venvs)} entries saved to user config")

    def _add_to_registry(self, name: str, venv_path: str, layer: str = None) -> bool:
        """Add a venv to the config tier and rebuild display."""
        if layer is None:
            layer = self.ownerComp.par.Configlocation.eval()

        config = {
            'path': venv_path,
            'import': False,
            'description': ''
        }

        if layer == 'project':
            project_venvs = self._read_project_venvs()
            project_venvs[name] = config
            self._write_project_venvs(project_venvs)
        elif layer == 'project_folder':
            config['path'] = self.storage.to_relative_path(venv_path)
            self.storage.add_venv(name, config, 'folder')
        else:  # 'user'
            self.storage.add_venv(name, config, 'user')

        # Rebuild display
        self._load_and_rebuild_registry()
        self.Refreshenvmenu()
        self._log(f"Added to registry [{layer}]: {name} -> {venv_path}")
        return True

    def _get_entry_layer(self, index: int) -> str:
        """Get the source tier for a sequence entry by index."""
        return self._entry_layers.get(index, 'user')

    # ==================== CONFIG PARAMETER CALLBACKS ====================

    def onParConfiglocation(self):
        """Update config path display when location selector changes."""
        self._update_config_path_display()

    def onParRefreshregistry(self):
        """Reload from all config tiers and rebuild sequence display."""
        self._load_and_rebuild_registry()
        self.Refreshenvmenu()
        self._import_sequence_venvs()
        self._log("Registry refreshed from config files")

    def onParEditconfigfile(self):
        """Open the active config JSON in system editor."""
        location = self.ownerComp.par.Configlocation.eval()
        if location == 'project':
            self._log("Project config is stored in operator DAT — edit directly in TD")
            return

        file_layer = 'user' if location == 'user' else 'folder'
        config_path = self.storage.get_effective_config_path(file_layer)

        if not config_path:
            self._log("Config path not available", level='ERROR')
            return

        # Ensure file exists before opening
        self.storage.ensure_dirs(file_layer)
        if not os.path.exists(config_path):
            self.storage.write_config({
                'version': '1.0',
                'venvs': {},
                'sidecar_overrides': {}
            }, file_layer)

        # Open with system default editor
        if sys.platform == 'win32':
            os.startfile(config_path)
        else:
            subprocess.Popen(['open', config_path])

    def _update_config_path_display(self):
        """Update the Configpath display parameter."""
        location = self.ownerComp.par.Configlocation.eval()
        if location == 'user':
            path = self.storage.get_user_config_path()
        elif location == 'project_folder':
            path = self.storage.get_folder_config_path()
        else:
            path = '(stored in operator DAT)'
        self.ownerComp.par.Configpath = path or '(not configured)'

    # ==================== SEQUENCE PARAMETER HANDLING ====================

    def sequence_update(self, par):
        """
        Handle sequence parameter changes. Call from parameter execute DAT.

        Args:
            par: The parameter that changed (from onValueChange or onPulse)
        """
        name = par.name

        # Check for Venv{N}import toggle changes
        if name.startswith('Venv') and name.endswith('import'):
            try:
                index = int(name[4:-6])  # Extract number from "Venv{N}import"
                self.OnVenvImportChange(index, par.eval())
            except ValueError:
                pass

        # Check for Venv{N}open pulse
        elif name.startswith('Venv') and name.endswith('open'):
            try:
                index = int(name[4:-4])  # Extract number from "Venv{N}open"
                self.OnVenvOpenPulse(index)
            except ValueError:
                pass

        # Check for Venv{N}path changes - refresh menu
        elif name.startswith('Venv') and name.endswith('path'):
            self.Refreshenvmenu()

    # ==================== SEQUENCE CALLBACKS ====================

    def OnVenvImportChange(self, index: int, value: bool):
        """Handle import toggle change for a sequence entry."""
        path_par = getattr(self.ownerComp.par, f'Venv{index}path', None)
        if not path_par:
            return

        venv_path = path_par.eval()
        if not venv_path:
            return

        if value:
            self._add_to_syspath_direct(venv_path)
        else:
            self._remove_from_syspath(venv_path)

        self._update_sequence_status(index)

    def OnVenvOpenPulse(self, index: int):
        """Handle open pulse for a sequence entry."""
        path_par = getattr(self.ownerComp.par, f'Venv{index}path', None)
        if not path_par:
            return

        venv_path = path_par.eval()
        if not venv_path or not os.path.exists(venv_path):
            self._log(f"Venv not found at: {venv_path}", level='ERROR')
            return

        self._log(f"Opening console for {venv_path}...")
        self.venv.open_console(venv_path)
