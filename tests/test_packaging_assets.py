from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_requirements_include_desktop_and_packaging_dependencies():
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()

    for package in ["pyside6", "keyring", "pyinstaller"]:
        assert package in text


def test_desktop_build_script_supports_exe_and_optional_msi_without_keys():
    spec = (ROOT / "build_desktop.spec").read_text(encoding="utf-8")
    script = (ROOT / "scripts" / "build_desktop.ps1").read_text(encoding="utf-8")
    wxs = (ROOT / "packaging" / "BuildingDeformationChecker.wxs").read_text(encoding="utf-8")

    for token in ['"python"', '"-m"', '"PyInstaller"', '"build_desktop.spec"']:
        assert token in script
    assert "BuildMsi" in script
    assert "WixToolPath" in script
    assert "Invoke-Native" in script
    assert "$LASTEXITCODE" in script
    assert "wix.exe" in script
    assert "BuildingDeformationChecker.msi" in script
    assert "Copy-Item" in script
    assert "--version 4.0.6" in script
    assert "acceptEula" not in script
    assert "BuildingDeformationChecker.exe" in wxs
    assert 'Scope="perUser"' in wxs
    assert 'StandardDirectory Id="LocalAppDataFolder"' in wxs
    assert "city_safety_iot.ico" in spec
    assert "city_safety_iot.ico" in wxs
    assert "ARPPRODUCTICON" in wxs
    assert "sk-" not in script
    assert "PADDLE_OCR_TOKEN" not in script
    assert "sk-" not in wxs
