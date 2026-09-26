import os
from pathlib import Path
import subprocess


def test_deploy_prod_prepares_dev_workspace_before_rsync(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = root / "tools" / "deploy-prod.sh"

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ssh_log = tmp_path / "ssh.log"
    rsync_log = tmp_path / "rsync.log"

    ssh = fake_bin / "ssh"
    ssh.write_text(
        "#!/bin/bash\n"
        f"printf '%s\\n' \"$*\" >> {ssh_log!s}\n"
        "case \"$*\" in\n"
        "  *\"sha256sum '/usr/local/sbin/solartrigger-release-update'\"*) "
        "sha256sum \"$SOLARTRIGGER_DEPLOY_SRC/install/solartrigger-release-update\" "
        "| cut -d ' ' -f1; exit 0 ;;\n"
        "  *\"grep -q\"*\"dev-prepare)\"*) exit 0 ;;\n"
        "  *\"readlink -f\"*) "
        "printf '%s\\n' '/home/airone/solartrigger/dev-active'; exit 0 ;;\n"
        "  *) cat >/dev/null 2>/dev/null || true; exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    ssh.chmod(0o755)

    rsync = fake_bin / "rsync"
    rsync.write_text(
        "#!/bin/bash\n"
        f"printf '%s\\n' \"$*\" >> {rsync_log!s}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    rsync.chmod(0o755)

    scp = fake_bin / "scp"
    scp.write_text("#!/bin/bash\nexit 99\n", encoding="utf-8")
    scp.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SOLARTRIGGER_DEPLOY_SRC"] = str(root)
    env["SOLARTRIGGER_DEPLOY_HOST"] = "airone@trigger1"

    result = subprocess.run(
        ["bash", str(script)],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    calls = ssh_log.read_text(encoding="utf-8")
    assert "dev-prepare" in calls
    assert "readlink -f '/home/airone/solar-eclipse-trigger-prod'" in calls
    assert rsync_log.is_file()
    assert "/home/airone/solartrigger/dev-active/backend/" in rsync_log.read_text(
        encoding="utf-8"
    )


def test_deploy_prod_dry_run_does_not_prepare_or_bootstrap(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = root / "tools" / "deploy-prod.sh"

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ssh_log = tmp_path / "ssh.log"

    ssh = fake_bin / "ssh"
    ssh.write_text(
        "#!/bin/bash\n"
        f"printf '%s\\n' \"$*\" >> {ssh_log!s}\n"
        "case \"$*\" in\n"
        "  *\"test -d '/home/airone/solartrigger/dev-active'\"*) exit 1 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    ssh.chmod(0o755)

    scp = fake_bin / "scp"
    scp.write_text("#!/bin/bash\nexit 99\n", encoding="utf-8")
    scp.chmod(0o755)

    rsync = fake_bin / "rsync"
    rsync.write_text("#!/bin/bash\nexit 99\n", encoding="utf-8")
    rsync.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SOLARTRIGGER_DEPLOY_SRC"] = str(root)

    result = subprocess.run(
        ["bash", str(script), "--dry-run"],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Would run: sudo -n" in result.stdout
    assert "dry-run stops before rsync" in result.stdout
    assert "sudo install" not in ssh_log.read_text(encoding="utf-8")


def test_deploy_prod_protects_and_repairs_camera_persistent_links():
    root = Path(__file__).resolve().parents[1]
    script = (root / "tools" / "deploy-prod.sh").read_text(encoding="utf-8")

    assert "--exclude='camera_characterization'" in script
    assert "--exclude='camera_profiles'" in script
    assert "--exclude='camera_timing'" in script
    assert "--exclude='camera_characterization/'" not in script
    assert "--exclude='camera_profiles/'" not in script
    assert "--exclude='camera_timing/'" not in script

    assert "ensure_camera_persistent_links" in script
    assert "ln -sfn" in script
    assert "/home/airone/solartrigger/var/generated" in script
