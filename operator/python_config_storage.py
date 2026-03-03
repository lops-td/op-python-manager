"""
Python Config Storage Module

Handles config path resolution and file I/O for Python venv configurations.
Supports three config tiers (priority order, highest first):
- Project config: DAT in operator (persists with .toe file)
- Folder config: {project.folder}/.lops/python_config.json (portable, relative paths)
- User config: {AppData}/ChatTD/python_config.json (machine-specific, absolute paths)

Mirrors the pattern from mcp_config_storage.py.
"""

import json
import os
import sys
from typing import Dict, List, Optional, Any


class PythonConfigStorage:
    """
    Handles config path resolution and file I/O for Python venv configs.

    Three tiers:
    1. User config - global, machine-level (lowest priority)
    2. Folder config - per-project-folder, portable relative paths
    3. Project config - per-.toe file, stored in DAT (highest priority)

    Priority for deduplication: project > folder > user
    """

    CONFIG_FILE = 'python_config.json'

    def __init__(self, owner_comp, logger, chattd_ref):
        self.ownerComp = owner_comp
        self.logger = logger
        self.ChatTD = chattd_ref

    def _log(self, message, level='INFO'):
        if self.logger:
            if callable(self.logger):
                self.logger(message, level)
            elif hasattr(self.logger, 'log'):
                self.logger.log(message, 'PythonConfig', level)
        else:
            print(f"[PythonConfig:{level}] {message}")

    # =========================================================================
    # PATH RESOLUTION
    # =========================================================================

    def get_user_config_path(self) -> Optional[str]:
        """
        Get user config file path.

        Returns:
            Path to {AppData}/ChatTD/python_config.json or None
        """
        try:
            if sys.platform == 'win32':
                appdata = os.getenv('APPDATA')
                if not appdata:
                    return None
                base_dir = os.path.join(appdata, 'ChatTD')
            elif sys.platform == 'darwin':
                base_dir = os.path.expanduser('~/Library/Application Support/ChatTD')
            else:
                base_dir = os.path.expanduser('~/.config/ChatTD')

            return os.path.join(base_dir, self.CONFIG_FILE)
        except Exception as e:
            self._log(f"Error resolving user config path: {e}", 'ERROR')
            return None

    def get_folder_config_path(self) -> Optional[str]:
        """
        Get folder-level config file path.

        Returns:
            Path to {project.folder}/.lops/python_config.json or None
        """
        try:
            proj_folder = project.folder  # type: ignore  # TD global
            if not proj_folder or not os.path.isdir(proj_folder):
                return None
            return os.path.join(proj_folder, '.lops', self.CONFIG_FILE)
        except Exception as e:
            self._log(f"Error getting project folder: {e}", 'DEBUG')
            return None

    def get_effective_config_path(self, layer: str = None) -> Optional[str]:
        """
        Get config path based on layer or parameter setting.

        Args:
            layer: 'user', 'folder', or None to use Configlocation parameter
                   Note: 'project' layer uses DAT storage, not file path

        Returns:
            Path to config file or None
        """
        if layer == 'user':
            return self.get_user_config_path()
        elif layer == 'folder':
            return self.get_folder_config_path()
        elif layer is None:
            try:
                location = self.ownerComp.par.Configlocation.eval()
                if location == 'user':
                    return self.get_user_config_path()
                elif location == 'project_folder':
                    return self.get_folder_config_path()
            except AttributeError:
                return self.get_user_config_path()
        return None

    # =========================================================================
    # DIRECTORY MANAGEMENT
    # =========================================================================

    def ensure_dirs(self, layer: str) -> bool:
        """
        Create parent directories for config file if they don't exist.

        Args:
            layer: 'user' or 'folder'

        Returns:
            True if directories exist/created
        """
        config_path = self.get_effective_config_path(layer)
        if not config_path:
            self._log(f"Cannot create dirs: {layer} path not available", 'WARNING')
            return False

        try:
            parent = os.path.dirname(config_path)
            os.makedirs(parent, exist_ok=True)
            return True
        except Exception as e:
            self._log(f"Error creating directories: {e}", 'ERROR')
            return False

    # =========================================================================
    # CONFIG FILE I/O
    # =========================================================================

    def read_config(self, layer: str) -> Dict:
        """
        Read python_config.json from specified layer.

        Args:
            layer: 'user' or 'folder'

        Returns:
            Full config dict, or empty dict if file doesn't exist
        """
        config_path = self.get_effective_config_path(layer)
        if not config_path or not os.path.exists(config_path):
            return {}

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError as e:
            self._log(f"Invalid JSON in {config_path}: {e}", 'ERROR')
            return {}
        except Exception as e:
            self._log(f"Error reading config from {layer}: {e}", 'ERROR')
            return {}

    def write_config(self, data: Dict, layer: str) -> bool:
        """
        Write python_config.json to specified layer.

        Args:
            data: Full config dict
            layer: 'user' or 'folder'

        Returns:
            True if write succeeded
        """
        config_path = self.get_effective_config_path(layer)
        if not config_path:
            self._log(f"Cannot write config: {layer} path not available", 'ERROR')
            return False

        if not self.ensure_dirs(layer):
            return False

        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            return True
        except Exception as e:
            self._log(f"Error writing config to {layer}: {e}", 'ERROR')
            return False

    # =========================================================================
    # VENV CRUD
    # =========================================================================

    def read_venvs(self, layer: str) -> Dict[str, Dict]:
        """
        Read venv entries from specified layer.

        Args:
            layer: 'user' or 'folder'

        Returns:
            Dict of name -> venv config
        """
        config = self.read_config(layer)
        venvs = config.get('venvs', {})
        return venvs if isinstance(venvs, dict) else {}

    def write_venvs(self, venvs: Dict[str, Dict], layer: str) -> bool:
        """
        Write venv entries to specified layer (preserves other config fields).

        Args:
            venvs: Dict of name -> venv config
            layer: 'user' or 'folder'

        Returns:
            True if write succeeded
        """
        config = self.read_config(layer)
        config['version'] = '1.0'
        config['venvs'] = venvs
        if 'sidecar_overrides' not in config:
            config['sidecar_overrides'] = {}
        return self.write_config(config, layer)

    def add_venv(self, name: str, config: Dict, layer: str = 'user') -> bool:
        """
        Add a single venv entry to a file-based layer.

        Args:
            name: Venv name (unique within layer)
            config: Venv config dict with 'path', 'import', 'description'
            layer: 'user' or 'folder'

        Returns:
            True if added successfully
        """
        if layer not in ('user', 'folder'):
            self._log(f"Invalid layer for file storage: {layer}", 'ERROR')
            return False

        venvs = self.read_venvs(layer)
        venvs[name] = config
        success = self.write_venvs(venvs, layer)
        if success:
            self._log(f"Added venv '{name}' to {layer} config")
        return success

    def remove_venv(self, name: str, layer: str = 'user') -> bool:
        """
        Remove a single venv entry from a file-based layer.

        Args:
            name: Venv name
            layer: 'user' or 'folder'

        Returns:
            True if removed successfully
        """
        if layer not in ('user', 'folder'):
            self._log(f"Invalid layer for file storage: {layer}", 'ERROR')
            return False

        venvs = self.read_venvs(layer)
        if name not in venvs:
            self._log(f"Venv '{name}' not found in {layer} config", 'WARNING')
            return False

        del venvs[name]
        success = self.write_venvs(venvs, layer)
        if success:
            self._log(f"Removed venv '{name}' from {layer} config")
        return success

    # =========================================================================
    # MERGE
    # =========================================================================

    def get_merged_venvs(self, project_venvs: Dict[str, Dict] = None) -> List[Dict]:
        """
        Merge venvs from all tiers. Priority: project > folder > user.

        Each entry gets 'source_layer', 'resolved_path', and 'stale' added.

        Args:
            project_venvs: Optional dict of project-level venvs (from DAT storage)

        Returns:
            List of venv configs with metadata added
        """
        merged = {}

        # Tier 1: User config (lowest priority)
        user_venvs = self.read_venvs('user')
        for name, config in user_venvs.items():
            resolved = config.get('path', '')
            merged[name] = {
                **config,
                'name': name,
                'source_layer': 'user',
                'resolved_path': resolved,
            }

        # Tier 2: Folder config (overrides user)
        folder_venvs = self.read_venvs('folder')
        for name, config in folder_venvs.items():
            raw_path = config.get('path', '')
            resolved = self.resolve_path(raw_path, 'folder')
            merged[name] = {
                **config,
                'name': name,
                'source_layer': 'folder',
                'resolved_path': resolved,
            }

        # Tier 3: Project config from DAT (overrides folder)
        if project_venvs:
            for name, config in project_venvs.items():
                resolved = config.get('path', '')
                merged[name] = {
                    **config,
                    'name': name,
                    'source_layer': 'project',
                    'resolved_path': resolved,
                }

        # Check stale paths
        result = list(merged.values())
        for entry in result:
            resolved = entry.get('resolved_path', '')
            entry['stale'] = bool(resolved) and not os.path.exists(resolved)

        return result

    # =========================================================================
    # PATH UTILITIES
    # =========================================================================

    def resolve_path(self, path: str, layer: str) -> str:
        """
        Resolve a path based on its layer.
        Folder-layer paths starting with './' or '.\\' are resolved against project.folder.

        Args:
            path: Raw path from config
            layer: 'user', 'folder', or 'project'

        Returns:
            Resolved absolute path
        """
        if not path:
            return ''

        if layer == 'folder' and (path.startswith('./') or path.startswith('.\\')):
            try:
                proj_folder = project.folder  # type: ignore
                if proj_folder:
                    return os.path.normpath(os.path.join(proj_folder, path))
            except Exception:
                pass

        return path

    def to_relative_path(self, absolute_path: str) -> str:
        """
        Convert absolute path to relative if it's inside project.folder.

        Args:
            absolute_path: Absolute path to convert

        Returns:
            Relative path (starting with ./) if inside project.folder,
            otherwise returns the original absolute path
        """
        if not absolute_path:
            return ''

        try:
            proj_folder = project.folder  # type: ignore
            if not proj_folder:
                return absolute_path

            norm_path = os.path.normpath(absolute_path)
            norm_proj = os.path.normpath(proj_folder)

            if norm_path.startswith(norm_proj):
                rel = os.path.relpath(norm_path, norm_proj)
                return './' + rel.replace('\\', '/')
        except Exception:
            pass

        return absolute_path

    # =========================================================================
    # LAYER UTILITIES
    # =========================================================================

    def get_venv_layer(self, venv_name: str,
                       project_venvs: Dict[str, Dict] = None) -> Optional[str]:
        """
        Determine which layer owns a venv entry.

        Args:
            venv_name: Name of the venv
            project_venvs: Optional project-level venvs (from DAT)

        Returns:
            'project', 'folder', 'user', or None
        """
        if project_venvs and venv_name in project_venvs:
            return 'project'

        folder_venvs = self.read_venvs('folder')
        if venv_name in folder_venvs:
            return 'folder'

        user_venvs = self.read_venvs('user')
        if venv_name in user_venvs:
            return 'user'

        return None

    def get_config_info(self, project_venvs: Dict[str, Dict] = None) -> Dict[str, Any]:
        """
        Get information about current config state.

        Returns:
            Dict with config paths and venv counts per layer
        """
        user_path = self.get_user_config_path()
        folder_path = self.get_folder_config_path()

        return {
            'user_path': user_path,
            'folder_path': folder_path,
            'user_venv_count': len(self.read_venvs('user')),
            'folder_venv_count': len(self.read_venvs('folder')),
            'project_venv_count': len(project_venvs) if project_venvs else 0,
            'user_exists': bool(user_path and os.path.exists(user_path)),
            'folder_exists': bool(folder_path and os.path.exists(folder_path)),
        }
