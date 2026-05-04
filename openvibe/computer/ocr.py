"""Screen-region OCR — extract visible text without sending to the LLM.

Backends (tried in order)
--------------------------
1. ``pytesseract`` — fastest, needs Tesseract binary installed on the OS.
2. macOS Vision framework — zero extra deps on macOS 11+, via Swift subprocess.
3. Windows WinRT OCR — zero extra deps on Windows 10+, via PowerShell.
4. Graceful failure with clear install hints.

Usage::

    from openvibe.computer.ocr import extract_text_from_region

    # region = (x, y, width, height) in logical screen pixels; None = full screen
    text = extract_text_from_region(region=(100, 200, 800, 400))
"""

from __future__ import annotations

import io
import platform
import subprocess
import tempfile
from pathlib import Path

_PLATFORM = platform.system()


def extract_text_from_region(
    region: tuple[int, int, int, int] | None = None,
) -> str:
    """Extract text from a screen region using OCR.

    Parameters
    ----------
    region:
        ``(x, y, width, height)`` in logical screen pixels.
        ``None`` to capture and OCR the full primary screen.

    Returns
    -------
    Extracted text string, or an informative error/install-hint string.
    """
    try:
        from openvibe.computer.capture import capture_screen
        png_bytes, w, h = capture_screen(region)
    except Exception as exc:
        return f"OCR error: could not capture screen: {exc}"

    # 1 — pytesseract (cross-platform, best quality)
    try:
        import pytesseract  # type: ignore[import-not-found]
        from PIL import Image  # type: ignore[import-not-found]
        img = Image.open(io.BytesIO(png_bytes))
        # Upscale small regions — Tesseract accuracy degrades below ~300 px wide
        if w < 300 and w > 0:
            factor = max(2, 300 // w)
            img = img.resize((w * factor, h * factor), Image.LANCZOS)
        # PSM 6 = assume a uniform block of text (good for UI regions)
        text = pytesseract.image_to_string(img, config="--psm 6")
        return text.strip() or "(no text detected)"
    except ImportError:
        pass
    except Exception as exc:
        return f"pytesseract error: {exc}"

    # 2 — macOS Vision framework (zero deps, macOS 11+)
    if _PLATFORM == "Darwin":
        try:
            return _macos_vision_ocr(png_bytes)
        except Exception:
            pass

    # 3 — Windows WinRT OCR (zero deps, Windows 10+)
    if _PLATFORM == "Windows":
        try:
            return _windows_winrt_ocr(png_bytes)
        except Exception:
            pass

    return (
        "OCR not available. Install pytesseract to enable text extraction:\n"
        "  macOS:   brew install tesseract && pip install pytesseract\n"
        "  Ubuntu:  sudo apt install tesseract-ocr && pip install pytesseract\n"
        "  Windows: choco install tesseract && pip install pytesseract\n\n"
        "(On macOS 11+ and Windows 10+ a fallback OCR engine is also tried "
        "automatically without any extra installs.)"
    )


# ---------------------------------------------------------------------------
# macOS Vision framework — Swift subprocess, zero extra deps
# ---------------------------------------------------------------------------


def _macos_vision_ocr(png_bytes: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(png_bytes)
        tmp_png = Path(f.name)

    swift_src = f"""
import Vision
import AppKit

let url = URL(fileURLWithPath: "{tmp_png}")
guard let img = NSImage(contentsOf: url),
      let cgImg = img.cgImage(forProposedRect: nil, context: nil, hints: nil)
else {{ exit(0) }}

let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.usesLanguageCorrection = true
let handler = VNImageRequestHandler(cgImage: cgImg, options: [:])
try? handler.perform([req])
let lines = (req.results ?? []).compactMap {{ $0.topCandidates(1).first?.string }}
print(lines.joined(separator: "\\n"))
"""
    with tempfile.NamedTemporaryFile(suffix=".swift", delete=False, mode="w") as sf:
        sf.write(swift_src)
        swift_path = Path(sf.name)

    try:
        r = subprocess.run(
            ["swift", str(swift_path)],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
        raise RuntimeError(f"Swift Vision OCR failed: {r.stderr.strip()}")
    finally:
        tmp_png.unlink(missing_ok=True)
        swift_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Windows WinRT OCR — PowerShell, zero extra deps on Windows 10+
# ---------------------------------------------------------------------------


def _windows_winrt_ocr(png_bytes: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(png_bytes)
        tmp_png = Path(f.name)

    # Use full absolute path and forward slashes for PowerShell
    tmp_ps = str(tmp_png).replace("\\", "/")

    ps_script = f"""
$null = [System.Runtime.InteropServices.WindowsRuntime.WindowsRuntimeSystemExtensions]
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType=WindowsRuntime] | Out-Null

$await = [System.WindowsRuntimeSystemExtensions].GetMethod('AsTask', [type[]]@([System.Type]))

$filePath = '{tmp_ps}'
$file = [Windows.Storage.StorageFile]::GetFileFromPathAsync($filePath).AsTask().Result
$stream = $file.OpenAsync([Windows.Storage.FileAccessMode]::Read).AsTask().Result
$decoder = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream).AsTask().Result
$bitmap = $decoder.GetSoftwareBitmapAsync().AsTask().Result
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
$result = $engine.RecognizeAsync($bitmap).AsTask().Result
$result.Lines | ForEach-Object {{ $_.Text }}
"""
    try:
        r = subprocess.run(
            ["powershell", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, timeout=25,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
        raise RuntimeError(f"WinRT OCR failed: {r.stderr.strip()}")
    finally:
        tmp_png.unlink(missing_ok=True)
