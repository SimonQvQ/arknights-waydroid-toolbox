#!/usr/bin/python3
from pathlib import Path
import tarfile,hashlib,re
root=Path(__file__).resolve().parents[1]
pack=root/'packaging'
bundle=root/'build/linux/x64/release/bundle'
if not (bundle/'arknights_waydroid_toolbox').is_file():raise SystemExit('Build the Flutter Linux release first')
archive=pack/'toolbox-bundle.tar.gz'
with tarfile.open(archive,'w:gz',compresslevel=1) as tar:
 tar.add(bundle,arcname='bundle')
 tar.add(root/'backend/toolbox.py',arcname='toolbox.py')
 tar.add(root/'LICENSE',arcname='LICENSE')
 for name in ['arknights-toolbox','arknights-toolbox.desktop','arknights-toolbox.svg']:
  tar.add(pack/name,arcname='packaging/'+name)
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
p=pack/'PKGBUILD';p.write_text(re.sub(r"sha256sums=\('[^']*'\)","sha256sums=('"+digest+"')",p.read_text()))
print(archive,digest)
