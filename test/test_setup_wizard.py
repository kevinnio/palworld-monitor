"""
Tests for src/setup_wizard.py: first-run interactive setup and PalServer
INI auto-detection, including LGSM's `serverfiles/` install layout.
"""

import os
from unittest.mock import MagicMock

import yaml

from src.setup_wizard import _default_config, _find_palserver_ini, interactive_setup


def _write_ini(path, content='OptionSettings=(Difficulty=None,AdminPassword="")'):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class TestFindPalserverIni:
    def test_finds_ini_directly_under_exe_dir(self, tmp_path):
        """Existing (non-LGSM) behavior: exe and ini share the same tree."""
        exe_path = str(tmp_path / "PalServer" / "PalServer.exe")
        ini_path = tmp_path / "PalServer" / "Pal" / "Saved" / "Config" / "LinuxServer" / "PalWorldSettings.ini"
        _write_ini(ini_path)

        result = _find_palserver_ini(exe_path)

        assert result == str(ini_path)

    def test_finds_ini_under_serverfiles_lgsm_layout(self, tmp_path):
        """LGSM case: exe is the instance script; game files live in serverfiles/."""
        exe_path = str(tmp_path / "pwserver" / "pwserver")
        ini_path = (
            tmp_path / "pwserver" / "serverfiles" / "Pal" / "Saved" / "Config"
            / "LinuxServer" / "PalWorldSettings.ini"
        )
        _write_ini(ini_path)

        result = _find_palserver_ini(exe_path)

        assert result == str(ini_path)

    def test_prefers_direct_root_over_serverfiles(self, tmp_path):
        """If both layouts somehow have an ini, the direct exe_dir one wins."""
        exe_path = str(tmp_path / "pwserver" / "pwserver")
        direct_ini = (
            tmp_path / "pwserver" / "Pal" / "Saved" / "Config" / "LinuxServer" / "PalWorldSettings.ini"
        )
        serverfiles_ini = (
            tmp_path / "pwserver" / "serverfiles" / "Pal" / "Saved" / "Config"
            / "LinuxServer" / "PalWorldSettings.ini"
        )
        _write_ini(direct_ini)
        _write_ini(serverfiles_ini)

        result = _find_palserver_ini(exe_path)

        assert result == str(direct_ini)

    def test_falls_back_to_default_ini_direct_root(self, tmp_path):
        exe_path = str(tmp_path / "PalServer" / "PalServer.exe")
        default_ini = tmp_path / "PalServer" / "DefaultPalWorldSettings.ini"
        _write_ini(default_ini, content="DEFAULT_CONTENT")

        result = _find_palserver_ini(exe_path)

        expected = tmp_path / "PalServer" / "Pal" / "Saved" / "Config" / "WindowsServer" / "PalWorldSettings.ini"
        assert result == str(expected)
        assert expected.read_text(encoding="utf-8") == "DEFAULT_CONTENT"

    def test_falls_back_to_default_ini_under_serverfiles(self, tmp_path):
        exe_path = str(tmp_path / "pwserver" / "pwserver")
        default_ini = tmp_path / "pwserver" / "serverfiles" / "DefaultPalWorldSettings.ini"
        _write_ini(default_ini, content="DEFAULT_CONTENT")

        result = _find_palserver_ini(exe_path)

        expected = (
            tmp_path / "pwserver" / "serverfiles" / "Pal" / "Saved" / "Config"
            / "WindowsServer" / "PalWorldSettings.ini"
        )
        assert result == str(expected)
        assert expected.read_text(encoding="utf-8") == "DEFAULT_CONTENT"

    def test_returns_none_when_nothing_found(self, tmp_path):
        exe_path = str(tmp_path / "nothing_here" / "pwserver")
        assert _find_palserver_ini(exe_path) is None


class TestDefaultConfig:
    def test_use_lgsm_defaults_false(self):
        config = _default_config("/path/to/exe", "adminpass", "webpass")
        assert config["palserver"]["useLGSM"] is False

    def test_use_lgsm_true_is_persisted(self):
        config = _default_config("/path/to/pwserver", "adminpass", "webpass", use_lgsm=True)
        assert config["palserver"]["useLGSM"] is True


class TestInteractiveSetupLGSM:
    def test_use_lgsm_prompts_for_lgsm_script_and_persists_flag(self, tmp_path, monkeypatch):
        exe_path = str(tmp_path / "pwserver" / "pwserver")
        settings_path = str(tmp_path / "settings.yaml")

        answers = iter([exe_path, "adminpass123", "webpass123"])
        mock_input = MagicMock(side_effect=lambda prompt: next(answers))
        monkeypatch.setattr("builtins.input", mock_input)

        interactive_setup(settings_path, detected_exe=None, use_lgsm=True)

        prompts = [call.args[0] for call in mock_input.call_args_list]
        assert "LGSM instance script" in prompts[0]

        with open(settings_path, "r") as f:
            saved = yaml.safe_load(f)
        assert saved["palserver"]["useLGSM"] is True
        assert saved["palserver"]["exePath"] == exe_path

    def test_quoted_exe_path_is_stored_unquoted(self, tmp_path, monkeypatch):
        """A path pasted with wrapping quotes must not keep them (regression:
        the quoted value failed validation and the app would not start)."""
        exe_path = str(tmp_path / "PalServer" / "PalServer.exe")
        settings_path = str(tmp_path / "settings.yaml")

        answers = iter([f'"{exe_path}"', "adminpass123", "webpass123"])
        mock_input = MagicMock(side_effect=lambda prompt: next(answers))
        monkeypatch.setattr("builtins.input", mock_input)

        interactive_setup(settings_path, detected_exe=None, use_lgsm=False)

        with open(settings_path, "r") as f:
            saved = yaml.safe_load(f)
        assert saved["palserver"]["exePath"] == exe_path

    def test_single_quoted_exe_path_is_stored_unquoted(self, tmp_path, monkeypatch):
        exe_path = str(tmp_path / "PalServer" / "PalServer.exe")
        settings_path = str(tmp_path / "settings.yaml")

        answers = iter([f"'{exe_path}'", "adminpass123", "webpass123"])
        mock_input = MagicMock(side_effect=lambda prompt: next(answers))
        monkeypatch.setattr("builtins.input", mock_input)

        interactive_setup(settings_path, detected_exe=None, use_lgsm=False)

        with open(settings_path, "r") as f:
            saved = yaml.safe_load(f)
        assert saved["palserver"]["exePath"] == exe_path

    def test_quoted_lgsm_script_is_stored_unquoted(self, tmp_path, monkeypatch):
        """The LGSM branch shares the same cleaning: quotes are stripped too."""
        script_path = str(tmp_path / "pwserver" / "pwserver")
        settings_path = str(tmp_path / "settings.yaml")

        answers = iter([f'"{script_path}"', "adminpass123", "webpass123"])
        mock_input = MagicMock(side_effect=lambda prompt: next(answers))
        monkeypatch.setattr("builtins.input", mock_input)

        interactive_setup(settings_path, detected_exe=None, use_lgsm=True)

        with open(settings_path, "r") as f:
            saved = yaml.safe_load(f)
        assert saved["palserver"]["exePath"] == script_path

    def test_default_prompts_for_palserver_exe(self, tmp_path, monkeypatch):
        exe_path = str(tmp_path / "PalServer" / "PalServer.exe")
        settings_path = str(tmp_path / "settings.yaml")

        answers = iter([exe_path, "adminpass123", "webpass123"])
        mock_input = MagicMock(side_effect=lambda prompt: next(answers))
        monkeypatch.setattr("builtins.input", mock_input)

        interactive_setup(settings_path, detected_exe=None, use_lgsm=False)

        prompts = [call.args[0] for call in mock_input.call_args_list]
        assert "PalServer.exe" in prompts[0]
        assert "LGSM" not in prompts[0]

        with open(settings_path, "r") as f:
            saved = yaml.safe_load(f)
        assert saved["palserver"]["useLGSM"] is False
