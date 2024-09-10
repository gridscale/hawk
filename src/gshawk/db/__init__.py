import sqlite3
import datetime
import time
import sys
import os
from gshawk.vars import global_args

class DB:
    connection = None
    start_time = None
    def __init__(self, state_dir):
        if global_args.get('show_datasources', False) or global_args.get('show_log_context', False) or global_args.get('hawk-crypt', False):
            # We know, that in this case the show-datasources flag will exit and nothing will be processed.
            return

        print("Aquiring database lock...", file=sys.stderr)
        db = sqlite3.connect(database= "file:%s/feathers.db?mode=rwc&vfs=unix-excl" % state_dir, isolation_level=None, autocommit=True, check_same_thread=True, uri=True)
        db.execute("PRAGMA locking_mode = EXCLUSIVE;")
        DB.start_time = datetime.datetime.now().timestamp()
        db.execute("PRAGMA encoding = 'UTF-8';")
        db.execute("PRAGMA journal_mode = WAL;")
        db.execute("PRAGMA synchronous = NORMAL;")
        db.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        db.execute("CREATE TABLE IF NOT EXISTS feathers (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL, name TEXT NOT NULL, UNIQUE(path, name)) STRICT;")
        db.execute("CREATE TABLE IF NOT EXISTS files (source TEXT, filepath TEXT, modify_date REAL NOT NULL, flags INTEGER, feather INT NOT NULL, PRIMARY KEY (source, filepath, feather), FOREIGN KEY(feather) REFERENCES feathers(id) ON DELETE RESTRICT) STRICT;")
        db.commit()
        db.execute("PRAGMA optimize;")
        DB.connection = db
        print("ok", file=sys.stderr)

    @classmethod
    def record_file(self, relative_source_file, absolute_target_file, state, feather):
        if relative_source_file == '/dev/null':
            # Explicitly unrecorded.
            return
        flag = 0
        if state == "binary":
            flag = 1
        if state == "created":
            flag = 2
        if state == "changed":
            flag = 4
        if state == "up-to-date":
            flag = 8
        if state == "skipped":
            flag = 16

        DB.connection.execute("REPLACE INTO files VALUES(?, ?, ?, ?, ?);", (relative_source_file, absolute_target_file, datetime.datetime.now().timestamp(), flag, feather.db_id))
        DB.connection.commit()

    @classmethod
    def get_modify_time(self, absolute_target_file, feather):
        a = DB.connection.execute("SELECT modify_date FROM files WHERE filepath = ? AND feather = ?;", (absolute_target_file, feather.db_id))
        t = a.fetchone()
        if t is None:
            return 0
        return t[0]

    @classmethod
    def feather_id(self, feather):
        DB.connection.execute("INSERT INTO feathers (path, name) VALUES (?,  ?) ON CONFLICT DO NOTHING;", (feather.path, feather.name))
        DB.connection.commit()
        a = DB.connection.execute("SELECT id FROM feathers WHERE path = ? AND name = ?;", (feather.path, feather.name))
        t = a.fetchone()
        if t is None:
            raise Exception("could not save feather in DB.")
        return t[0]

    @classmethod
    def cleanup_files_and_state(self):
        DB.connection.commit()
        a = DB.connection.execute("SELECT files.filepath, files.flags, feathers.path, feathers.name FROM files LEFT JOIN feathers ON files.feather=feathers.id;")
        t = a.fetchone()
        print("currently known files:", file=sys.stderr)
        if t is None:
            print("  (none)", file=sys.stderr)
        while t is not None:
            filepath, flags, fpath, fname = t
            print(f"  {filepath}", file=sys.stderr)
            print(f"    feather: {fpath}", file=sys.stderr)
            t = a.fetchone()

        a = DB.connection.execute("SELECT filepath FROM files WHERE modify_date < ?;", (DB.start_time,))
        t = a.fetchone()
        print("known files no longer contained in feathers:", file=sys.stderr)
        if t is None:
            print("  (none)", file=sys.stderr)
        while t is not None:
            print(f"  {t[0]}", file=sys.stderr)
            t = a.fetchone()

        print("deleting files no longer contained in feathers...", file=sys.stderr)
        a = DB.connection.execute("SELECT source, filepath, feather FROM files WHERE modify_date < ?;", (DB.start_time,))
        t = a.fetchone()
        files_to_delete = []
        while t is not None:
            files_to_delete.append({'source': t[0], 'filepath': t[1], 'feather': t[2]})
            t = a.fetchone()
        
        deleted_count = 0
        kept_count = 0
        error_count = 0
        for file_info in files_to_delete:
            filepath = file_info['filepath']
            # Double check if there is another feather having this file. If so, do not delete.
            b = DB.connection.execute("SELECT COUNT(*) FROM files WHERE filepath = ? AND modify_date >= ?;", (filepath, DB.start_time))
            count = b.fetchone()[0]
            if count == 0:
                try:
                    os.remove(filepath)
                except FileNotFoundError:
                    pass
                except Exception as e:
                    print(f"  error deleting {filepath}: {e}", file=sys.stderr)
                    error_count += 1
                    continue
            else:
                print(f"  kept: {filepath} (still referenced by {count} active feather(s))", file=sys.stderr)
                kept_count += 1
                continue
            
            DB.connection.execute("DELETE FROM files WHERE source = ? AND filepath = ? AND feather = ?;", (file_info['source'], file_info['filepath'], file_info['feather']))
            print(f"  deleted: {filepath}", file=sys.stderr)
            deleted_count += 1
        print(f"summary: deleted={deleted_count}, kept={kept_count}, errors={error_count}", file=sys.stderr)
        DB.connection.commit()

        a = DB.connection.execute("SELECT filepath, COUNT(*) FROM files GROUP BY filepath HAVING COUNT(*) > 1;")
        t = a.fetchone()
        print("files duplicated in feathers:", file=sys.stderr)
        if t is None:
            print("  (none)", file=sys.stderr)
        while t is not None:
            filepath, count = t
            print(f"  {filepath} appears {count} times:", file=sys.stderr)
            b = DB.connection.execute("SELECT f.path, f.name FROM files JOIN feathers f ON files.feather=f.id WHERE files.filepath = ?;", (filepath,))
            dup = b.fetchone()
            while dup is not None:
                print(f"    - {dup[0]}:{dup[1]}", file=sys.stderr)
                dup = b.fetchone()
            t = a.fetchone()
