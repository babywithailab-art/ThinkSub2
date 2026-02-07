import pathlib
from datetime import datetime


def iter_license_files(root: pathlib.Path):
    patterns = [
        "LICENSE*",
        "COPYING*",
        "NOTICE*",
        "COPYRIGHT*",
    ]
    for pattern in patterns:
        for path in root.rglob(pattern):
            if path.is_file():
                yield path


def main():
    project_root = pathlib.Path(__file__).resolve().parents[1]
    dist_root = project_root / "dist" / "ThinkSub2"
    internal_root = dist_root / "_internal"
    output_path = dist_root / "THIRD_PARTY_NOTICES.txt"

    if not internal_root.exists():
        raise SystemExit(f"Missing dist folder: {internal_root}")

    license_files = sorted(
        {p for p in iter_license_files(internal_root)},
        key=lambda p: str(p).lower(),
    )

    header = [
        "THIRD-PARTY NOTICES",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Source: {internal_root}",
        "",
        "This file aggregates third-party license texts bundled in the distribution.",
        "Licenses for packages missing from the bundle should be added separately.",
        "",
    ]

    with output_path.open("w", encoding="utf-8") as handle:
        handle.write("\n".join(header))
        for path in license_files:
            rel = path.relative_to(dist_root)
            handle.write("\n" + "=" * 80 + "\n")
            handle.write(f"{rel}\n")
            handle.write("-" * 80 + "\n")
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = path.read_text(encoding="utf-8", errors="replace")
            handle.write(content.strip() + "\n")

    print(f"Wrote {output_path} ({len(license_files)} files)")


if __name__ == "__main__":
    main()
