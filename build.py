#!/usr/bin/env python3
"""Build script for creating distributable executables using Flet's packaging."""

import platform
import shutil
import subprocess
import sys
from pathlib import Path


def get_platform_name() -> str:
    """Get the current platform name."""
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    elif system == "linux":
        return "linux"
    elif system == "windows":
        return "windows"
    return system


def build():
    """Build the application using flet pack."""
    print("=" * 60)
    print("Building LAN Transfer")
    print("=" * 60)

    # Ensure we're in the project root
    project_root = Path(__file__).parent
    src_dir = project_root / "src"
    dist_dir = project_root / "dist"
    build_dir = project_root / "build"

    # Clean previous builds
    print("\n[1/3] Cleaning previous builds...")
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    if build_dir.exists():
        shutil.rmtree(build_dir)
    
    # Also clean any .spec files
    for spec_file in project_root.glob("*.spec"):
        spec_file.unlink()

    # Build using flet pack
    print("\n[2/3] Building with flet pack...")
    
    platform_name = get_platform_name()
    print(f"  Target platform: {platform_name}")

    # Flet pack arguments - use flet CLI directly
    flet_cmd = shutil.which("flet")
    if not flet_cmd:
        # Try to find flet in the venv
        if platform.system() == "Windows":
            flet_cmd = str(project_root / ".venv" / "Scripts" / "flet.exe")
        else:
            flet_cmd = str(project_root / ".venv" / "bin" / "flet")
    
    args = [
        flet_cmd, "pack",
        str(src_dir / "lantransfer" / "__main__.py"),
        "--name", "LANTransfer",
        "--add-data", f"{src_dir / 'lantransfer'}:lantransfer",
    ]

    # Platform-specific options
    if platform.system() == "Darwin":
        # macOS: Creates .app bundle
        print("  Building macOS .app bundle...")
    elif platform.system() == "Linux":
        # Linux: Creates executable
        print("  Building Linux executable...")
    elif platform.system() == "Windows":
        # Windows: Creates .exe
        print("  Building Windows executable...")

    # Run flet pack
    result = subprocess.run(args, cwd=project_root, input=b"y\n")

    if result.returncode != 0:
        print("\n❌ Build failed!")
        sys.exit(1)

    # Report results
    print("\n[3/3] Finalizing...")
    
    if platform.system() == "Darwin":
        app_path = dist_dir / "LANTransfer.app"
        if app_path.exists():
            # Calculate app size
            total_size = sum(f.stat().st_size for f in app_path.rglob("*") if f.is_file())
            print(f"\n✅ Build successful!")
            print(f"   Output: {app_path}")
            print(f"   Size: {total_size / (1024*1024):.1f} MB")
            print(f"\n   To run: open {app_path}")
            print(f"   Or double-click LANTransfer.app in Finder")
        else:
            print(f"\n⚠️  App bundle not found at expected location")
            print(f"   Check {dist_dir} for output files")
    else:
        exe_path = dist_dir / "LANTransfer"
        if platform.system() == "Windows":
            exe_path = dist_dir / "LANTransfer.exe"
        
        if exe_path.exists():
            print(f"\n✅ Build successful!")
            print(f"   Output: {exe_path}")
            print(f"   Size: {exe_path.stat().st_size / (1024*1024):.1f} MB")
            print(f"\n   To run: {exe_path}")
        else:
            print(f"\n⚠️  Executable not found at expected location")
            print(f"   Check {dist_dir} for output files")


def main():
    """Main entry point."""
    build()


if __name__ == "__main__":
    main()
