"""Usage: python scripts/audit_model_parameters.py /path/to/checkpoint"""

import sys
from pathlib import Path

from datamate.adapters.submission_writer import json_line
from datamate.model_audit import audit_checkpoint

if __name__ == "__main__":
    print(json_line(audit_checkpoint(Path(sys.argv[1]))))
