"""Write the checked-out Git revision into the Docker image at build time."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
git=ROOT/".git"
if git.is_file():
 text=git.read_text().strip()
 git=ROOT/text.split(":",1)[1].strip() if text.startswith("gitdir:") else git

head=(git/"HEAD").read_text().strip()
if head.startswith("ref: "):
 ref=head[5:]
 ref_file=git/ref
 if ref_file.exists():
  revision=ref_file.read_text().strip()
 else:
  revision=next((line.split()[0] for line in (git/"packed-refs").read_text().splitlines() if line and not line.startswith(("#","^")) and line.split()[-1]==ref),"")
else:
 revision=head

(ROOT/"BUILD_VERSION").write_text((revision[:7] if revision else "unknown")+"\n")
