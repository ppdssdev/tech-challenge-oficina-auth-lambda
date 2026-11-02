"""Create a non-deployable ZIP fixture for Terraform mock-provider tests only."""
from pathlib import Path
from zipfile import ZipFile

root = Path(__file__).resolve().parents[1]
with ZipFile(root / 'infra/terraform/tests/fixture.zip', 'w') as bundle:
    bundle.writestr('OFFLINE-TEST-ONLY', 'Not a deployable Lambda artifact')
