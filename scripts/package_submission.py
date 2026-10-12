"""Package the current source tree, excluding local secrets and generated files."""
import hashlib
import os
import subprocess
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "submission"
PREFIX = "rainy-cafe"
EXCLUDED_DIRS = {
    ".git", ".venv", "node_modules", ".cache", "__pycache__", ".pytest_cache",
    "dist", "build", "output", "test-results", "playwright-report", "tmp",
}
REQUIRED = {
    "README.md", "AI_USAGE.md", "docs/DELIVERY.md", ".env.example",
    "package.json", "package-lock.json", "pyproject.toml", "uv.lock",
    ".python-version", ".npmrc", "scripts/start.mjs",
    "scripts/package_submission.py", "apps/server/app/main.py",
    "apps/web/src/App.tsx", "apps/web/public/assets/mira.json",
    "apps/web/public/assets/mira.svg", "apps/web/public/assets/cafe.svg",
    "apps/web/public/assets/photo.svg", "THIRD_PARTY_NOTICES.md",
}


def permitted(name: str) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Invalid repository path: {name}")
    if any(part.lower() in EXCLUDED_DIRS for part in path.parts):
        return False
    filename = path.name.lower()
    if filename.startswith(".env") and name != ".env.example":
        return False
    return path.suffix.lower() not in {".pem", ".key", ".pfx", ".p12", ".log", ".pyc"}


def source_files() -> list[str]:
    # Use Git's file selection but read the working tree, not HEAD. This includes
    # current edits and new non-ignored files without installing dependencies.
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT, capture_output=True, check=True,
    )
    names = sorted({os.fsdecode(item) for item in result.stdout.split(b"\0") if item})
    files = []
    for name in names:
        if not permitted(name):
            continue
        path = ROOT / name
        if not path.resolve().is_relative_to(ROOT) or path.is_symlink():
            raise ValueError(f"Refusing external or symbolic-link file: {name}")
        if path.is_file():
            files.append(name)
    missing = REQUIRED - set(files)
    if missing:
        raise ValueError(f"Required delivery files missing: {', '.join(sorted(missing))}")
    audio = [name for name in files if name.startswith("apps/web/public/assets/audio/") and name.endswith(".wav")]
    if len(audio) != 28:
        raise ValueError(f"Expected 28 Mock WAV assets, found {len(audio)}")
    return files


def main() -> None:
    files = source_files()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    archive = OUTPUT / "rainy-cafe-source.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED, compresslevel=9) as package:
        for name in files:
            package.write(ROOT / name, f"{PREFIX}/{name}")
    with ZipFile(archive) as package:
        if package.testzip() is not None:
            raise ValueError("Archive integrity check failed")
        if len(package.namelist()) != len(files):
            raise ValueError("Archive file count mismatch")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = OUTPUT / "rainy-cafe-source.sha256"
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"Source package: {archive}")
    print(f"Files: {len(files)}; bytes: {archive.stat().st_size}")
    print(f"SHA-256: {digest}")
    print("Excluded: .git, private environment files, dependencies, caches and generated outputs")


if __name__ == "__main__":
    main()
