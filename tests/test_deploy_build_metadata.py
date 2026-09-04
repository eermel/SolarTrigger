from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SCRIPT = (
    ROOT
    / "tools"
    / "deploy-prod.sh"
).read_text(encoding="utf-8")


def test_deploy_writes_build_metadata():
    assert '"$SRC/VERSION"' in SCRIPT

    assert (
        'BUILD_COMMIT="$(git -C "$SRC" rev-parse HEAD)"'
        in SCRIPT
    )

    assert "BUILD_COMMIT" in SCRIPT

    assert (
        'ssh "$DST_HOST"'
        in SCRIPT
    )

    assert (
        "cat > '$DST/BUILD_COMMIT'"
        in SCRIPT
    )
