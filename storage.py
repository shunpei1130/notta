"""Offline/online SQLite snapshot backup; restoration always creates a new file."""
import argparse,sqlite3
from contextlib import closing
from pathlib import Path
def snapshot(source,destination):
 source=Path(source).resolve();destination=Path(destination).resolve()
 if not source.is_file():raise ValueError('元DBがありません。')
 if destination.exists():raise ValueError('既存ファイルは上書きしません。別の保存先を指定してください。')
 destination.parent.mkdir(parents=True,exist_ok=True)
 with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(destination)) as dst:
  if src.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('元DBの整合性検査に失敗しました。原本を保持して中止します。')
  src.backup(dst)
  if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('保存後のDB整合性検査に失敗しました。')
 return str(destination)
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('operation',choices=['backup','restore']);ap.add_argument('source');ap.add_argument('destination');a=ap.parse_args()
 print(snapshot(a.source,a.destination))
