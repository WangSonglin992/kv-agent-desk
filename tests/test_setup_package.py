import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from tools import check_public_tree
from tools import stage_installer_payload


ROOT = Path(__file__).resolve().parents[1]


class InstallerDefinitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.definition = (ROOT / "installer" / "KV-Agent-Desk.iss").read_text(encoding="utf-8")
        cls.build_script = (ROOT / "Build-Installer.ps1").read_text(encoding="utf-8")
        cls.workflow = (ROOT / ".github" / "workflows" / "windows-release.yml").read_text(
            encoding="utf-8"
        )

    def test_setup_identity_location_privileges_and_tasks_are_frozen(self):
        self.assertIn("AppId={{8D8918EF-160F-4D1E-AE68-E168DB0AD2B9}", self.definition)
        self.assertIn("DefaultDirName={code:GetDefaultDir}", self.definition)
        self.assertIn("UsePreviousAppDir=yes", self.definition)
        self.assertIn("UsePreviousTasks=yes", self.definition)
        self.assertIn("Result := 'D:\\APP\\KV-Agent-Desk';", self.definition)
        self.assertIn("PrivilegesRequired=lowest", self.definition)
        self.assertIn("ArchitecturesAllowed=x64compatible", self.definition)
        self.assertIn("ArchitecturesInstallIn64BitMode=x64compatible", self.definition)
        self.assertIn("DisableDirPage=no", self.definition)
        self.assertIn("Flags: checkedonce", self.definition)
        self.assertIn("Name: \"desktopicon\"", self.definition)
        self.assertIn("Name: \"autostart\"", self.definition)
        self.assertIn("--state-dir \"\"{%USERPROFILE}\\.kv-agent-desk\"\"", self.definition)

    def test_ini_precedes_registry_and_invalid_metadata_aborts_setup(self):
        self.assertIn("GetIniString(InstallIniSection, 'InstallDir', '', IniPath)", self.definition)
        self.assertIn("GetIniString(InstallIniSection, 'DesktopShortcut', '', IniPath)", self.definition)
        self.assertIn("GetIniString(InstallIniSection, 'InstallerManaged', '', IniPath)", self.definition)
        self.assertIn("RegQueryStringValue(HKEY_CURRENT_USER, InstallRegistrySubkey, 'InstallDir'", self.definition)
        self.assertLess(
            self.definition.index("ReadInstallationIni(SavedInstallDir, SavedDesktopShortcut)"),
            self.definition.index("function GetDefaultDir"),
        )
        self.assertIn("procedure CurPageChanged(CurPageID: Integer)", self.definition)
        self.assertIn("function HasDirCommandLineOverride(): Boolean", self.definition)
        self.assertIn("WizardForm.DirEdit.Text := GetDefaultDir('')", self.definition)
        self.assertIn("Setup stopped to preserve that record", self.definition)

    def test_custom_absolute_install_paths_do_not_need_a_product_name_leaf(self):
        from agent_dashboard.install_location import _validate_install_dir

        custom = r"E:\Apps\My Agent Desk"
        self.assertEqual(str(_validate_install_dir(custom)), custom)
        self.assertNotIn("ExtractFileName(Value)", self.definition)

    def test_prepare_finish_and_uninstall_hooks_are_ordered_and_fail_closed(self):
        prepare = self.definition.index("function PrepareToInstall")
        finish = self.definition.index("procedure CurStepChanged")
        uninstall = self.definition.index("function InitializeUninstall")
        self.assertLess(prepare, finish)
        self.assertLess(finish, uninstall)
        self.assertIn("Flags: dontcopy", self.definition)
        self.assertIn("ExtractTemporaryFile('KV-Agent-Desk.exe')", self.definition)
        self.assertIn("Exec(TemporaryExe, '--installer-prepare'", self.definition)
        self.assertIn("--installer-finish --install-dir", self.definition)
        self.assertIn("--desktop-shortcut ", self.definition)
        self.assertIn("--auto-start ", self.definition)
        self.assertIn("'--installer-uninstall'", self.definition)
        self.assertIn("Setup stopped before replacing any app files", self.definition)
        self.assertNotIn("[UninstallDelete]", self.definition)
        self.assertNotIn("DelTree(", self.definition)
        self.assertNotIn("RemoveDir(", self.definition)

    def test_setup_payload_sources_are_explicit_and_exclude_private_state(self):
        self.assertIn("{#SetupPayload}\\KV-Agent-Desk.exe", self.definition)
        self.assertIn("{#SetupPayload}\\LICENSE", self.definition)
        self.assertIn("{#SetupPayload}\\THIRD_PARTY_NOTICES.md", self.definition)
        self.assertIn("{#SetupPayload}\\licenses\\*", self.definition)
        for forbidden in ("runtime\\", "settings.json", "subscriptions.json", "lab.png", "PLAN.md"):
            self.assertNotIn(forbidden, self.definition)

    def test_compiler_download_is_attested_and_signed_before_execution(self):
        self.assertIn("is-7_1_0/innosetup-7.1.0-x64.exe", self.build_script)
        self.assertIn("release verify-asset is-7_1_0", self.build_script)
        self.assertIn("Get-AuthenticodeSignature", self.build_script)
        self.assertIn("Pyrsys B\\.V\\.", self.build_script)
        verify = self.build_script.index("release verify-asset")
        signature = self.build_script.index("Get-AuthenticodeSignature")
        execute = self.build_script.index("Start-Process -FilePath $installerPath")
        self.assertLess(verify, execute)
        self.assertLess(signature, execute)
        self.assertIn("--define=AppVersion=$version", self.build_script)
        self.assertIn("SkipAppBuild is reserved for CI", self.build_script)

    def test_public_guard_allowlist_stays_narrow_for_installer_files(self):
        self.assertTrue(check_public_tree.allowed("installer/KV-Agent-Desk.iss"))
        self.assertFalse(check_public_tree.allowed("installer/private.txt"))
        self.assertFalse(check_public_tree.allowed("installer/nested/extra.iss"))
        self.assertTrue(check_public_tree.allowed("Build-Installer.ps1"))
        self.assertTrue(check_public_tree.allowed("AGENTS.md"))

    def test_workflow_pins_compiler_and_isolates_setup_reinstall_smoke(self):
        self.assertIn("is-7_1_0", self.workflow)
        self.assertIn("gh release verify-asset", self.workflow)
        self.assertRegex(self.workflow, r"GH_TOKEN:\s*\$\{\{\s*github\.token\s*\}\}")
        self.assertIn("Get-AuthenticodeSignature", self.workflow)
        self.assertIn("Build-Installer.ps1 -SkipAppBuild", self.workflow)
        self.assertIn("RUNNER_TEMP", self.workflow)
        self.assertIn("USERPROFILE", self.workflow)
        self.assertIn('"auto_start":false', self.workflow)
        self.assertIn('"ui_id":"synthetic-style"', self.workflow)
        self.assertIn('"schema_version":1,"id":"synthetic-style"', self.workflow)
        self.assertIn('"profiles":{"codex"', self.workflow)
        self.assertIn("/MERGETASKS=!desktopicon,!autostart", self.workflow)
        self.assertNotIn('"/TASKS="', self.workflow)
        self.assertIn('"My Agent Desk"', self.workflow)
        self.assertIn("[IO.Path]::GetFullPath($installDir).TrimEnd", self.workflow)
        self.assertIn("[string]::Equals($normalizedRegisteredLocation, $expectedLocation, [StringComparison]::OrdinalIgnoreCase)", self.workflow)
        self.assertIn("Expected synthetic path", self.workflow)
        self.assertNotIn("$uninstallEntry.InstallLocation -ne $installDir", self.workflow)
        self.assertIn("'/LOG=", self.workflow)
        self.assertIn("USERPROFILE_matches_synthetic", self.workflow)
        self.assertIn("synthetic_installation_ini_exists", self.workflow)
        self.assertIn("synthetic_uninstall_entry_exists", self.workflow)
        self.assertIn("kv-agent-installer-lifecycle-diagnostic.json", self.workflow)
        self.assertIn("traceback_functions", self.workflow)
        self.assertIn("Upload synthetic installer failure diagnostics", self.workflow)
        self.assertIn("if-no-files-found: ignore", self.workflow)
        self.assertNotIn("Write-Output $uninstallerLogText", self.workflow)
        self.assertIn("installation.ini", self.workflow)
        self.assertIn("KV-Agent-Desk-Setup.exe", self.workflow)
        self.assertIn("KV-Agent-Desk-Setup.exe.sha256", self.workflow)

    def test_setup_smoke_seeds_valid_user_state_with_autostart_off(self):
        from agent_dashboard.settings import AppSettings
        from agent_dashboard.subscriptions import SubscriptionProfiles
        from agent_dashboard.ui_profiles import UIProfiles

        def fixture(index):
            match = re.search(
                rf"WriteAllText\(\$syntheticFiles\[{index}\], '([^']*)'\)",
                self.workflow,
            )
            self.assertIsNotNone(match)
            return json.loads(match.group(1))

        settings = fixture(0)
        self.assertFalse(settings["auto_start"])
        self.assertEqual(settings["ui_id"], "synthetic-style")
        AppSettings(None).update(settings)

        subscriptions = fixture(1)
        self.assertEqual(subscriptions["version"], 1)
        self.assertEqual(set(subscriptions["profiles"]), {"codex", "zcode", "antigravity"})
        for profile in subscriptions["profiles"].values():
            SubscriptionProfiles.validate(profile)

        style = fixture(3)
        self.assertEqual(style["schema_version"], 1)
        self.assertEqual(style["id"], "synthetic-style")
        UIProfiles(None).import_profile(style)


class InstallerPayloadTests(unittest.TestCase):
    def test_stages_only_exe_public_notices_and_text_licenses(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "project"
            root.mkdir()
            (root / "LICENSE").write_bytes(b"synthetic project license\n")
            (root / "THIRD_PARTY_NOTICES.md").write_bytes(b"synthetic public notices\n")
            executable = root / "dist" / "KV-Agent-Desk.exe"
            executable.parent.mkdir()
            executable.write_bytes(b"synthetic executable")
            cpython_license = root / "python" / "LICENSE.txt"
            cpython_license.parent.mkdir()
            cpython_license.write_bytes(b"synthetic CPython license\n")
            dependency_license = root / "python" / "package" / "LICENSE"
            dependency_license.parent.mkdir(parents=True)
            dependency_license.write_bytes(b"synthetic dependency license\n")
            dependency_name = "licenses/python/synthetic-1.0/LICENSE"
            payload = Path(folder) / "payload"

            with (
                patch.object(stage_installer_payload, "_cpython_license", return_value=cpython_license),
                patch.object(
                    stage_installer_payload,
                    "_dependency_license_files",
                    return_value=[(dependency_license, dependency_name)],
                ),
            ):
                result, count = stage_installer_payload.stage_setup_payload(
                    executable, payload, project_root=root
                )

            self.assertEqual(result, payload.resolve())
            self.assertEqual(count, 1)
            files = {item.relative_to(payload).as_posix() for item in payload.rglob("*") if item.is_file()}
            self.assertEqual(
                files,
                {
                    "KV-Agent-Desk.exe",
                    "LICENSE",
                    "THIRD_PARTY_NOTICES.md",
                    "licenses/CPython/LICENSE.txt",
                    dependency_name,
                },
            )
            self.assertEqual((payload / "KV-Agent-Desk.exe").read_bytes(), b"synthetic executable")
            self.assertFalse((payload / "runtime").exists())
            self.assertFalse((payload / "assets").exists())
            self.assertFalse((payload / "settings.json").exists())
            self.assertFalse((payload / "subscriptions.json").exists())

    def test_existing_payload_destination_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root / "KV-Agent-Desk.exe"
            executable.write_bytes(b"synthetic executable")
            output = root / "payload"
            output.mkdir()
            marker = output / "keep.txt"
            marker.write_bytes(b"leave me alone")
            with self.assertRaises(stage_installer_payload.PackageError):
                stage_installer_payload.stage_setup_payload(
                    executable, output, project_root=root
                )
            self.assertEqual(marker.read_bytes(), b"leave me alone")


if __name__ == "__main__":
    unittest.main()
