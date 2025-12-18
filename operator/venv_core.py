"""
VenvCore - Stateless virtual environment operations

All methods take venv_path as first argument. No global state.
Can be tested outside TouchDesigner.

Backends:
- 'uv': Uses uv venv, uv pip (default, faster)
- 'pip': Uses python -m venv, python -m pip (fallback)
"""

import subprocess
import os
import sys
import json
import shutil
import platform
from typing import Optional, List, Dict, Any, Union


def is_mac():
    """Check if running on macOS."""
    return sys.platform == 'darwin'


def is_windows():
    """Check if running on Windows."""
    return sys.platform == 'win32'


def get_short_path_name(long_path: str) -> str:
    """
    Convert a Windows path with spaces to 8.3 short format.
    Returns short path if on Windows and path has spaces, otherwise returns original.
    """
    if not is_windows() or ' ' not in long_path:
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


class VenvCore:
    """
    Stateless virtual environment operations.

    All methods take venv_path as first argument.
    Default backend is 'uv' with automatic fallback to 'pip' if uv unavailable.
    """

    def __init__(self, logger=None, default_backend: str = 'uv'):
        """
        Initialize VenvCore.

        Args:
            logger: Optional logger with .log(message, category, level) method
            default_backend: 'uv' or 'pip' (default: 'uv')
        """
        self.logger = logger
        self._default_backend = default_backend
        self._uv_available = None  # Cached after first check

    def _log(self, message: str, level: str = 'INFO'):
        """Log message if logger available."""
        if self.logger:
            # Support both callable (function) and object with .log() method
            if callable(self.logger):
                self.logger(message, level)
            elif hasattr(self.logger, 'log'):
                self.logger.log(message, 'VenvCore', level)
        else:
            print(f"[VenvCore:{level}] {message}")

    # ==================== UV Availability ====================

    def check_uv_available(self) -> bool:
        """
        Check if UV is installed and accessible.
        Result is cached after first call.
        """
        if self._uv_available is not None:
            return self._uv_available

        try:
            creationflags = subprocess.CREATE_NO_WINDOW if is_windows() else 0
            result = subprocess.run(
                ['uv', '--version'],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=creationflags
            )
            if result.returncode == 0:
                version = result.stdout.strip()
                self._log(f'UV found: {version}', 'INFO')
                self._uv_available = True
                return True
        except FileNotFoundError:
            self._log('UV not found in PATH', 'WARNING')
        except Exception as e:
            self._log(f'Error checking UV: {e}', 'WARNING')

        self._uv_available = False
        return False

    def _resolve_backend(self, backend: Optional[str]) -> str:
        """
        Resolve which backend to use.

        Args:
            backend: 'uv', 'pip', or None (use default)

        Returns:
            'uv' or 'pip'
        """
        if backend is None:
            backend = self._default_backend

        if backend == 'uv':
            if not self.check_uv_available():
                self._log('UV requested but not available, falling back to pip', 'WARNING')
                return 'pip'

        return backend

    # ==================== Path Utilities ====================

    def get_python_exe(self, venv_path: str) -> Optional[str]:
        """
        Get Python executable path for venv.

        Args:
            venv_path: Path to virtual environment

        Returns:
            Path to python executable, or None if not found
        """
        if is_windows():
            python_exe = os.path.join(venv_path, 'Scripts', 'python.exe')
        else:
            python_exe = os.path.join(venv_path, 'bin', 'python')

        return python_exe if os.path.exists(python_exe) else None

    def get_site_packages(self, venv_path: str) -> Optional[str]:
        """
        Get site-packages path for venv.

        Args:
            venv_path: Path to virtual environment

        Returns:
            Path to site-packages, or None if not found
        """
        if is_windows():
            site_packages = os.path.join(venv_path, 'Lib', 'site-packages')
        else:
            # Need to find the python version directory
            lib_path = os.path.join(venv_path, 'lib')
            if os.path.exists(lib_path):
                for item in os.listdir(lib_path):
                    if item.startswith('python'):
                        site_packages = os.path.join(lib_path, item, 'site-packages')
                        if os.path.exists(site_packages):
                            return site_packages
            return None

        return site_packages if os.path.exists(site_packages) else None

    def get_activate_script(self, venv_path: str) -> Optional[str]:
        """
        Get activation script path for venv.

        Args:
            venv_path: Path to virtual environment

        Returns:
            Path to activate script, or None if not found
        """
        if is_windows():
            activate = os.path.join(venv_path, 'Scripts', 'activate.bat')
        else:
            activate = os.path.join(venv_path, 'bin', 'activate')

        return activate if os.path.exists(activate) else None

    # ==================== Venv Lifecycle ====================

    def check_venv(self, venv_path: str) -> Dict[str, Any]:
        """
        Check venv status and return info dict.

        Args:
            venv_path: Path to virtual environment

        Returns:
            {
                'exists': bool,
                'python_exe': str or None,
                'python_version': str or None,
                'site_packages': str or None,
                'activate_script': str or None
            }
        """
        result = {
            'exists': False,
            'python_exe': None,
            'python_version': None,
            'site_packages': None,
            'activate_script': None
        }

        if not venv_path or not os.path.isdir(venv_path):
            return result

        result['exists'] = True
        result['python_exe'] = self.get_python_exe(venv_path)
        result['site_packages'] = self.get_site_packages(venv_path)
        result['activate_script'] = self.get_activate_script(venv_path)

        # Get Python version
        if result['python_exe']:
            try:
                creationflags = subprocess.CREATE_NO_WINDOW if is_windows() else 0
                proc = subprocess.run(
                    [result['python_exe'], '--version'],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    creationflags=creationflags
                )
                if proc.returncode == 0:
                    result['python_version'] = proc.stdout.strip()
            except Exception:
                pass

        return result

    def create_venv(self, venv_path: str, backend: Optional[str] = None,
                    python_version: Optional[str] = None,
                    system_python: Optional[str] = None,
                    extra_args: Optional[List[str]] = None) -> bool:
        """
        Create virtual environment at specified path.

        Args:
            venv_path: Full path for new venv
            backend: 'uv', 'pip', or None (use default)
            python_version: For UV - version like '3.11', '3.12'
            system_python: For pip - path to system Python executable
            extra_args: Additional args for UV (e.g., ['--system-site-packages', '--seed', '--relocatable'])

        Returns:
            True if created successfully
        """
        backend = self._resolve_backend(backend)

        # Check if already exists
        if os.path.exists(venv_path):
            self._log(f'Venv already exists: {venv_path}', 'INFO')
            return True

        # Ensure parent directory exists
        parent_dir = os.path.dirname(venv_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir)
            self._log(f'Created parent directory: {parent_dir}', 'INFO')

        try:
            if backend == 'uv':
                return self._create_venv_uv(venv_path, python_version, extra_args)
            else:
                return self._create_venv_pip(venv_path, system_python)
        except Exception as e:
            self._log(f'Failed to create venv: {e}', 'ERROR')
            return False

    def _create_venv_uv(self, venv_path: str, python_version: Optional[str], extra_args: Optional[List[str]] = None) -> bool:
        """Create venv using UV."""
        cmd = ['uv', 'venv', venv_path]
        if python_version:
            cmd.extend(['--python', python_version])
        if extra_args:
            cmd.extend(extra_args)

        self._log(f'Creating venv with UV: {" ".join(cmd)}', 'INFO')

        creationflags = subprocess.CREATE_NO_WINDOW if is_windows() else 0
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            creationflags=creationflags
        )

        if result.returncode == 0:
            self._log(f'UV venv created at: {venv_path}', 'INFO')
            return True
        else:
            self._log(f'UV venv creation failed: {result.stderr}', 'ERROR')
            return False

    def _create_venv_pip(self, venv_path: str, system_python: Optional[str]) -> bool:
        """Create venv using python -m venv."""
        python_exe = system_python or sys.executable
        python_exe = get_short_path_name(python_exe)
        venv_path = get_short_path_name(venv_path)

        cmd = [python_exe, '-m', 'venv', venv_path]

        self._log(f'Creating venv with pip: {" ".join(cmd)}', 'INFO')

        creationflags = subprocess.CREATE_NO_WINDOW if is_windows() else 0

        if is_mac():
            # Unset PYTHONPATH on Mac
            env = os.environ.copy()
            env.pop('PYTHONPATH', None)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
        else:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, creationflags=creationflags)

        if result.returncode == 0:
            self._log(f'Venv created at: {venv_path}', 'INFO')
            return True
        else:
            self._log(f'Venv creation failed: {result.stderr}', 'ERROR')
            return False

    def delete_venv(self, venv_path: str) -> bool:
        """
        Delete virtual environment directory.

        Args:
            venv_path: Path to virtual environment

        Returns:
            True if deleted successfully
        """
        if not os.path.exists(venv_path):
            self._log(f'Venv does not exist: {venv_path}', 'INFO')
            return True

        try:
            shutil.rmtree(venv_path)
            self._log(f'Deleted venv: {venv_path}', 'INFO')
            return True
        except Exception as e:
            self._log(f'Failed to delete venv: {e}', 'ERROR')
            return False

    # ==================== Package Management ====================

    def install_packages(self, venv_path: str, packages: Union[str, List[str]],
                        backend: Optional[str] = None,
                        index_url: Optional[str] = None,
                        extra_args: Optional[List[str]] = None,
                        wait: bool = True,
                        show_console: bool = False,
                        timeout: int = 600) -> bool:
        """
        Install packages in venv.

        Args:
            venv_path: Path to virtual environment
            packages: Package name(s) - str or list
            backend: 'uv', 'pip', or None (use default)
            index_url: PyTorch CUDA index, etc.
            extra_args: ['--upgrade'], ['--no-deps'], etc.
            wait: Block until complete
            show_console: Show cmd window (Windows only)
            timeout: Timeout in seconds (default 600 = 10 min)

        Returns:
            True if installed successfully
        """
        backend = self._resolve_backend(backend)
        python_exe = self.get_python_exe(venv_path)

        if not python_exe:
            self._log(f'Venv python not found: {venv_path}', 'ERROR')
            return False

        # Normalize packages to list
        if isinstance(packages, str):
            packages = packages.split() if ' ' in packages else [packages]

        try:
            if backend == 'uv':
                return self._install_packages_uv(python_exe, packages, index_url, extra_args, wait, show_console, timeout)
            else:
                return self._install_packages_pip(python_exe, packages, index_url, extra_args, wait, show_console, timeout)
        except Exception as e:
            self._log(f'Package install failed: {e}', 'ERROR')
            return False

    def _install_packages_uv(self, python_exe: str, packages: List[str],
                             index_url: Optional[str], extra_args: Optional[List[str]],
                             wait: bool, show_console: bool, timeout: int) -> bool:
        """Install packages using UV."""
        cmd = ['uv', 'pip', 'install', '--python', python_exe]

        if index_url:
            cmd.extend(['--index-url', index_url])

        if extra_args:
            cmd.extend(extra_args)

        cmd.extend(packages)

        self._log(f'Installing with UV: {packages}', 'INFO')

        return self._run_install_command(cmd, wait, show_console, timeout)

    def _install_packages_pip(self, python_exe: str, packages: List[str],
                              index_url: Optional[str], extra_args: Optional[List[str]],
                              wait: bool, show_console: bool, timeout: int) -> bool:
        """Install packages using pip."""
        python_exe = get_short_path_name(python_exe)
        cmd = [python_exe, '-m', 'pip', 'install']

        if index_url:
            cmd.extend(['--index-url', index_url])

        if extra_args:
            cmd.extend(extra_args)

        cmd.extend(packages)

        self._log(f'Installing with pip: {packages}', 'INFO')

        return self._run_install_command(cmd, wait, show_console, timeout)

    def _run_install_command(self, cmd: List[str], wait: bool, show_console: bool, timeout: int) -> bool:
        """Run an install command."""
        if is_windows():
            if show_console:
                if wait:
                    process = subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
                    process.wait()
                    return process.returncode == 0
                else:
                    subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
                    return True
            else:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                if result.returncode != 0:
                    self._log(f'Install error: {result.stderr[:500]}', 'ERROR')
                return result.returncode == 0
        else:
            # Mac/Linux
            env = os.environ.copy()
            env.pop('PYTHONPATH', None)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
            if result.returncode != 0:
                self._log(f'Install error: {result.stderr[:500]}', 'ERROR')
            return result.returncode == 0

    def uninstall_packages(self, venv_path: str, packages: Union[str, List[str]],
                          backend: Optional[str] = None) -> bool:
        """
        Uninstall packages from venv.

        Args:
            venv_path: Path to virtual environment
            packages: Package name(s) - str or list
            backend: 'uv', 'pip', or None (use default)

        Returns:
            True if uninstalled successfully
        """
        backend = self._resolve_backend(backend)
        python_exe = self.get_python_exe(venv_path)

        if not python_exe:
            self._log(f'Venv python not found: {venv_path}', 'ERROR')
            return False

        if isinstance(packages, str):
            packages = [packages]

        try:
            if backend == 'uv':
                cmd = ['uv', 'pip', 'uninstall', '--python', python_exe] + packages
            else:
                python_exe = get_short_path_name(python_exe)
                cmd = [python_exe, '-m', 'pip', 'uninstall', '-y'] + packages

            creationflags = subprocess.CREATE_NO_WINDOW if is_windows() else 0
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, creationflags=creationflags)

            if result.returncode == 0:
                self._log(f'Uninstalled: {packages}', 'INFO')
                return True
            else:
                self._log(f'Uninstall failed: {result.stderr}', 'ERROR')
                return False
        except Exception as e:
            self._log(f'Uninstall error: {e}', 'ERROR')
            return False

    def list_packages(self, venv_path: str, backend: Optional[str] = None) -> List[Dict[str, str]]:
        """
        List installed packages in venv.

        Args:
            venv_path: Path to virtual environment
            backend: 'uv', 'pip', or None (use default)

        Returns:
            [{'name': 'torch', 'version': '2.9.0'}, ...]
        """
        backend = self._resolve_backend(backend)
        python_exe = self.get_python_exe(venv_path)

        if not python_exe:
            return []

        try:
            if backend == 'uv':
                cmd = ['uv', 'pip', 'list', '--python', python_exe, '--format', 'json']
            else:
                python_exe = get_short_path_name(python_exe)
                cmd = [python_exe, '-m', 'pip', 'list', '--format', 'json']

            creationflags = subprocess.CREATE_NO_WINDOW if is_windows() else 0
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, creationflags=creationflags)

            if result.returncode == 0:
                packages = json.loads(result.stdout)
                return [{'name': p['name'], 'version': p['version']} for p in packages]
        except Exception as e:
            self._log(f'List packages error: {e}', 'ERROR')

        return []

    # ==================== Script/Command Execution ====================

    def run_script(self, venv_path: str, script: str,
                   wait: bool = True, capture: bool = True,
                   cwd: Optional[str] = None, timeout: int = 60) -> Dict[str, Any]:
        """
        Run Python script in venv.

        Args:
            venv_path: Path to virtual environment
            script: Python code as string
            wait: Block until complete
            capture: Capture stdout/stderr
            cwd: Working directory
            timeout: Timeout in seconds

        Returns:
            {
                'success': bool,
                'returncode': int,
                'stdout': str (if capture),
                'stderr': str (if capture)
            }
        """
        python_exe = self.get_python_exe(venv_path)

        if not python_exe:
            return {'success': False, 'returncode': -1, 'stdout': '', 'stderr': 'Venv python not found'}

        python_exe = get_short_path_name(python_exe)
        cmd = [python_exe, '-c', script]

        try:
            if is_windows():
                creationflags = subprocess.CREATE_NO_WINDOW
                result = subprocess.run(
                    cmd,
                    capture_output=capture,
                    text=True,
                    cwd=cwd,
                    timeout=timeout,
                    creationflags=creationflags
                )
            else:
                env = os.environ.copy()
                env.pop('PYTHONPATH', None)
                result = subprocess.run(
                    cmd,
                    capture_output=capture,
                    text=True,
                    cwd=cwd,
                    timeout=timeout,
                    env=env
                )

            return {
                'success': result.returncode == 0,
                'returncode': result.returncode,
                'stdout': result.stdout if capture else '',
                'stderr': result.stderr if capture else ''
            }
        except subprocess.TimeoutExpired:
            return {'success': False, 'returncode': -1, 'stdout': '', 'stderr': f'Timeout after {timeout}s'}
        except Exception as e:
            return {'success': False, 'returncode': -1, 'stdout': '', 'stderr': str(e)}

    def run_command(self, venv_path: str, command: str,
                    wait: bool = True, capture: bool = True,
                    cwd: Optional[str] = None, interactive: bool = False) -> Dict[str, Any]:
        """
        Run shell command with venv activated.

        Args:
            venv_path: Path to virtual environment
            command: Shell command to run
            wait: Block until complete
            capture: Capture stdout/stderr (ignored if interactive)
            cwd: Working directory
            interactive: Open new terminal window

        Returns:
            {
                'success': bool,
                'returncode': int,
                'stdout': str (if capture and not interactive),
                'stderr': str (if capture and not interactive)
            }
        """
        activate_script = self.get_activate_script(venv_path)

        if not activate_script:
            return {'success': False, 'returncode': -1, 'stdout': '', 'stderr': 'Activate script not found'}

        try:
            if is_windows():
                if interactive:
                    full_cmd = f'start cmd /k "{activate_script} && {command}"'
                    os.system(full_cmd)
                    return {'success': True, 'returncode': 0, 'stdout': '', 'stderr': ''}
                else:
                    full_cmd = f'cmd /c "{activate_script} && {command}"'
                    if wait:
                        result = subprocess.run(
                            full_cmd,
                            shell=True,
                            capture_output=capture,
                            text=True,
                            cwd=cwd,
                            creationflags=subprocess.CREATE_NO_WINDOW
                        )
                        return {
                            'success': result.returncode == 0,
                            'returncode': result.returncode,
                            'stdout': result.stdout if capture else '',
                            'stderr': result.stderr if capture else ''
                        }
                    else:
                        subprocess.Popen(full_cmd, shell=True, cwd=cwd, creationflags=subprocess.CREATE_NO_WINDOW)
                        return {'success': True, 'returncode': 0, 'stdout': '', 'stderr': ''}
            else:
                # Mac/Linux
                full_cmd = f'source {activate_script} && {command}'
                env = os.environ.copy()
                env.pop('PYTHONPATH', None)

                if interactive:
                    # Open Terminal on Mac
                    apple_script = f'''
                    tell application "Terminal"
                        do script "{full_cmd}"
                    end tell
                    '''
                    subprocess.Popen(['osascript', '-e', apple_script])
                    return {'success': True, 'returncode': 0, 'stdout': '', 'stderr': ''}
                else:
                    result = subprocess.run(
                        full_cmd,
                        shell=True,
                        capture_output=capture,
                        text=True,
                        cwd=cwd,
                        env=env
                    )
                    return {
                        'success': result.returncode == 0,
                        'returncode': result.returncode,
                        'stdout': result.stdout if capture else '',
                        'stderr': result.stderr if capture else ''
                    }
        except Exception as e:
            return {'success': False, 'returncode': -1, 'stdout': '', 'stderr': str(e)}

    def open_console(self, venv_path: str, cwd: Optional[str] = None) -> bool:
        """
        Open interactive console with venv activated.

        Args:
            venv_path: Path to virtual environment
            cwd: Working directory (defaults to venv parent)

        Returns:
            True if console opened
        """
        activate_script = self.get_activate_script(venv_path)

        if not activate_script:
            self._log('Activate script not found', 'ERROR')
            return False

        cwd = cwd or os.path.dirname(venv_path)

        # Check if pip exists - UV venvs (without --seed) don't have pip
        if is_windows():
            pip_path = os.path.join(venv_path, 'Scripts', 'pip.exe')
        else:
            pip_path = os.path.join(venv_path, 'bin', 'pip')

        is_uv_venv = not os.path.exists(pip_path)

        try:
            if is_windows():
                if is_uv_venv:
                    help_msg = "echo. && echo [Python Env Manager] Use 'uv pip' instead of 'pip' in this environment && echo."
                    subprocess.Popen(
                        f'cmd.exe /k "{activate_script} && {help_msg}"',
                        cwd=cwd,
                        creationflags=subprocess.CREATE_NEW_CONSOLE
                    )
                else:
                    subprocess.Popen(
                        f'cmd.exe /k "{activate_script}"',
                        cwd=cwd,
                        creationflags=subprocess.CREATE_NEW_CONSOLE
                    )
            else:
                if is_uv_venv:
                    apple_script = f'''
                    tell application "Terminal"
                        do script "source {activate_script} && echo '' && echo '[Python Env Manager] Use uv pip instead of pip in this environment' && echo ''"
                    end tell
                    '''
                else:
                    apple_script = f'''
                    tell application "Terminal"
                        do script "source {activate_script}"
                    end tell
                    '''
                subprocess.Popen(['osascript', '-e', apple_script])

            self._log(f'Opened console for venv: {venv_path}', 'INFO')
            return True
        except Exception as e:
            self._log(f'Failed to open console: {e}', 'ERROR')
            return False
