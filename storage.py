"""Offline/online SQLite snapshot backup; restoration always creates a new file."""
import argparse,sqlite3
from contextlib import closing
from pathlib import Path
def snapshot(source,destination):
 source=Path(source).resolve();destination=Path(destination).resolve()
 if not source.is_file():raise ValueError('元DBがありません。')
 if destination.exists():raise ValueError('既存ファイルは上書きしません。別の保存先を指定してください。')
 destination.parent.mkdir(parents=True,exist_ok=True)
 try:
  with destination.open('xb'):pass
 except FileExistsError:raise ValueError('Destination was concurrently created; choose a new path')from None
 try:
  with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(destination)) as dst:
   if src.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or src.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Source integrity failed; original preserved')
   src.backup(dst)
   if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or dst.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Backup integrity failed')
 except Exception:
  destination.unlink(missing_ok=True)
  raise
 return str(destination)
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('operation',choices=['backup','restore']);ap.add_argument('source');ap.add_argument('destination');a=ap.parse_args()
 print(snapshot(a.source,a.destination))
