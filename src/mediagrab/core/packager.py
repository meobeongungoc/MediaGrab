"""ZIP_STORED packaging with verification before optional source removal."""
from pathlib import Path
import zipfile
import uuid
import hashlib

from .errors import MediaGrabError
from .playlist import digest


def create_zip(folder: Path, files: list[Path], delete_source: bool = False) -> Path:
    folder = folder.absolute()
    if folder.is_symlink() or folder.is_junction() or not folder.is_dir():
        raise MediaGrabError('Invalid packaging folder.')
    files = sorted(set(files))
    if not files:
        raise MediaGrabError('No completed files are available for ZIP packaging.')
    for path in files:
        if path.is_symlink() or not path.is_file() or path.parent.resolve() != folder.resolve():
            raise MediaGrabError('Only regular files directly inside the playlist folder may be packaged.')
    if delete_source and set(folder.iterdir()) != set(files):
        raise MediaGrabError('The folder contains other or unfinished files. It will not be deleted. Use --zip without source deletion.')
    snapshot = {p.name: digest(p) for p in files}
    target = folder.with_suffix(folder.suffix + '.zip')
    number = 2
    while target.exists():
        target = folder.parent / f'{folder.name} ({number}).zip'
        number += 1
    temp = target.with_name(target.name + '.' + uuid.uuid4().hex + '.part')
    # Exclusive creation avoids overwriting a previous or concurrent archive.
    try:
        with temp.open('xb') as stream:
            with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                for path in files:
                    archive.write(path, path.name)
        with zipfile.ZipFile(temp) as archive:
            if archive.testzip() is not None or set(archive.namelist()) != set(snapshot):
                raise MediaGrabError('ZIP verification failed. The source folder is retained.')
            for name, checksum in snapshot.items():
                with archive.open(name) as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != checksum:
                        raise MediaGrabError('ZIP contents do not match the source files. The folder will not be deleted.')
        if any(not p.is_file() or p.is_symlink() or digest(p) != snapshot[p.name] for p in files):
            raise MediaGrabError('Files changed during ZIP creation. The source folder is retained.')
        # On Windows rename refuses to replace an existing target.
        temp.rename(target)
        if delete_source:
            if folder.is_symlink() or folder.is_junction() or set(folder.iterdir()) != set(files):
                raise MediaGrabError('ZIP created, but the source folder changed. It will not be deleted.')
            # Delete only the verified leaf files, never recursive deletion.
            for path in files:
                path.unlink()
            folder.rmdir()
        return target
    except (OSError, zipfile.BadZipFile) as error:
        raise MediaGrabError('Could not finish ZIP creation or cleanup. Check disk space and write permission; any completed ZIP is retained.') from None

    finally:
        if temp.exists():
            temp.unlink()
