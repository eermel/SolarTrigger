import os
from pathlib import Path
import subprocess


def test_legacy_deploy_refuses_versioned_target_before_rsync(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = root / "tools" / "deploy-prod.sh"

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    ssh = fake_bin / "ssh"
    ssh.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' '/home/airone/solartrigger/releases/prod-test'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    ssh.chmod(0o755)

    marker = tmp_path / "rsync-called"
    rsync = fake_bin / "rsync"
    rsync.write_text(
        "#!/bin/sh\n"
        f"touch {marker!s}\n"
        "exit 99\n",
        encoding="utf-8",
    )
    rsync.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        ["bash", str(script)],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "refusing legacy rsync deployment" in result.stderr
    assert "scripts/build_release_package.py" in result.stderr
    assert not marker.exists(), "rsync must never run against a versioned target"
