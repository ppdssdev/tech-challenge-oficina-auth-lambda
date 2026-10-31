"""Build a reproducible contract bundle; this is NOT a deployable Lambda."""
import hashlib
import re
import sys
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version = sys.argv[1]
if not re.fullmatch(r"[0-9a-f]{40}", version):
    raise SystemExit("Expected full Git commit SHA")
out = root / "dist"
out.mkdir(exist_ok=True)
artifact = out / f"auth-contracts-{version}.zip"
with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as bundle:
    for path in sorted((root / "contracts").glob("*")):
        entry = zipfile.ZipInfo(str(path.relative_to(root)), (1980, 1, 1, 0, 0, 0))
        bundle.writestr(entry, path.read_bytes())
(out / f"{artifact.name}.sha256").write_text(
    f"{hashlib.sha256(artifact.read_bytes()).hexdigest()}  {artifact.name}\n")
print(artifact.name)
