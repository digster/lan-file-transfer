#!/usr/bin/env python3
"""Build script for creating single-file executables using PyInstaller."""

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


def get_executable_name() -> str:
    """Get the executable name for the current platform."""
    base_name = "lantransfer"
    if platform.system() == "Windows":
        return f"{base_name}.exe"
    return base_name


def build():
    """Build the application using PyInstaller."""
    print("=" * 60)
    print("Building LAN Transfer")
    print("=" * 60)

    # Ensure we're in the project root
    project_root = Path(__file__).parent
    src_dir = project_root / "src"
    dist_dir = project_root / "dist"
    build_dir = project_root / "build"

    # Clean previous builds
    print("\n[1/4] Cleaning previous builds...")
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    if build_dir.exists():
        shutil.rmtree(build_dir)

    # Ensure pyinstaller is available
    print("\n[2/4] Checking PyInstaller...")
    try:
        import PyInstaller
        print(f"  PyInstaller version: {PyInstaller.__version__}")
    except ImportError:
        print("  PyInstaller not found, installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller>=6.0.0"])

    # Build the executable
    print("\n[3/4] Building executable...")

    # PyInstaller arguments
    args = [
        sys.executable,
        "-m", "PyInstaller",
        "--onefile",  # Single file output
        "--windowed",  # No console window (GUI app)
        "--name", "lantransfer",
        "--clean",
        # Add Flet hidden imports
        "--hidden-import", "flet",
        "--hidden-import", "flet_core",
        "--hidden-import", "flet_runtime",
        "--hidden-import", "zeroconf",
        "--hidden-import", "aiohttp",
        "--hidden-import", "aiofiles",
        # Collect all flet data files
        "--collect-all", "flet",
        "--collect-all", "flet_runtime",
        # Add source directory to path
        "--paths", str(src_dir),
        # Entry point
        str(src_dir / "lantransfer" / "__main__.py"),
    ]

    # Platform-specific options
    if platform.system() == "Darwin":
        # macOS specific - create app bundle
        args.extend([
            "--osx-bundle-identifier", "com.lantransfer.app",
        ])
    elif platform.system() == "Linux":
        # Linux specific
        pass

    # Run PyInstaller
    result = subprocess.run(args, cwd=project_root)

    if result.returncode != 0:
        print("\n❌ Build failed!")
        sys.exit(1)

    # Rename output with platform suffix
    print("\n[4/4] Finalizing...")
    platform_name = get_platform_name()
    exe_name = get_executable_name()

    original_path = dist_dir / exe_name
    final_name = f"lantransfer-{platform_name}"
    if platform.system() == "Windows":
        final_name += ".exe"
    
    final_path = dist_dir / final_name

    if original_path.exists():
        original_path.rename(final_path)
        print(f"\n✅ Build successful!")
        print(f"   Output: {final_path}")
        print(f"   Size: {final_path.stat().st_size / (1024*1024):.1f} MB")
    else:
        print("\n❌ Output file not found!")
        sys.exit(1)


def main():
    """Main entry point."""
    build()


if __name__ == "__main__":
    main()


