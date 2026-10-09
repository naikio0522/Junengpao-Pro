# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the VideoMatrix backend.

Run with:
    pyinstaller backend/videomatrix-backend.spec

Outputs a self-contained executable under `dist/` that bundles:
- the FastAPI / uvicorn server (no system Python required)
- ffmpeg.exe / ffprobe.exe (or the Mac equivalents) sitting next to it,
  resolved at runtime via _MEIPASS by `app/core/ffmpeg.py:_find_tool`.

The CI workflow downloads platform-appropriate ffmpeg static builds into
`backend/ffmpeg/` before invoking this spec; locally you can drop binaries
in the same folder for a Mac/Linux dev build.
"""

import os
import sys
from importlib.metadata import distribution, PackageNotFoundError
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files
from huggingface_hub.constants import HF_HUB_CACHE

block_cipher = None

BACKEND_DIR = Path(os.path.abspath(SPECPATH))
FFMPEG_DIR = BACKEND_DIR / 'ffmpeg'
FONT_ASSET_DIR = BACKEND_DIR / 'assets' / 'fonts'
FONT_FILE = FONT_ASSET_DIR / 'files' / 'SourceHanSansSC-Regular.otf'
FONT_LICENSE = FONT_ASSET_DIR / 'SourceHanSans-LICENSE.txt'
FONT_README = FONT_ASSET_DIR / 'README.md'
BRAND_WATERMARK = BACKEND_DIR / 'assets' / 'brand' / 'junxiaobai-watermark.png'

# Collect ffmpeg / ffprobe regardless of which platform we are building on.
# Each entry is (source_path_on_disk, target_subdir_inside_bundle).
binaries = []
for name in ('ffmpeg', 'ffmpeg.exe', 'ffprobe', 'ffprobe.exe'):
    candidate = FFMPEG_DIR / name
    if candidate.exists():
        binaries.append((str(candidate), '.'))

datas = []
# faster-whisper loads Silero VAD by path at runtime; static import analysis
# does not discover this ONNX resource.
datas += collect_data_files('faster_whisper', includes=['assets/*.onnx'])
if FONT_FILE.exists():
    datas.append((str(FONT_FILE), 'fonts'))
if not BRAND_WATERMARK.is_file():
    raise RuntimeError('The required free-tier brand watermark is missing')
datas.append((str(BRAND_WATERMARK), 'brand'))
for notice in (FONT_LICENSE, FONT_README):
    if notice.exists():
        datas.append((str(notice), 'licenses/source-han-sans'))
model_notice = BACKEND_DIR / 'assets' / 'licenses' / 'speech-model-origin.txt'
if model_notice.exists():
    datas.append((str(model_notice), 'licenses/speech-model'))
for package_name in ('faster-whisper', 'av', 'onnxruntime', 'huggingface-hub', 'yt-dlp'):
    try:
        package = distribution(package_name)
    except PackageNotFoundError:
        continue
    for package_file in package.files or ():
        if Path(str(package_file)).name.upper() in {'LICENSE', 'LICENSE.TXT', 'COPYING', 'NOTICE'}:
            license_path = Path(package.locate_file(package_file))
            if license_path.is_file():
                datas.append((str(license_path), f'licenses/{package_name}'))

# The optional offline ASR also works on a PC without a pre-existing user
# model cache. The model is resolved by logical name at runtime, never by a
# build-machine path stored in the executable.
model_revision = 'ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66'
model_snapshot = (Path(HF_HUB_CACHE) / 'models--Systran--faster-whisper-base'
                  / 'snapshots' / model_revision)
if not (model_snapshot / 'model.bin').is_file():
    raise RuntimeError('Pinned offline faster-whisper base model missing; package would be unable to transcribe new videos. Download Systran/faster-whisper-base at revision ' + model_revision + ' before building.')
for model_file in model_snapshot.iterdir():
    if model_file.is_file():
        datas.append((str(model_file), 'speech_model/base'))
print('Bundling offline speech model: base')

# Hidden imports — uvicorn dynamically loads these and PyInstaller's static
# analysis would otherwise miss them.
hiddenimports = [
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.protocols.websockets.websockets_impl',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'uvicorn.lifespan.off',
    'fastapi',
    'pydantic',
    'pydantic.deprecated.decorator',
    'aiofiles',
    'app.api.routes',
    'app.api.accounts',
    'app.api.billing',
    'app.api.subtitles',
    'app.api.watermark_removal',
    'app.api.link_watermark',
    'app.api.standalone_variants',
    'app.core.ffmpeg',
    'app.core.video_matrix',
    'app.core.speech_transcript',
    'faster_whisper',
    'ctranslate2',
    'av',
    'app.core.semantic_selection',
    'app.core.video_variant',
    'app.core.timeline',
    'app.core.video_cover',
    'app.core.brand_watermark',
    'app.core.watermark_removal',
    'app.core.link_watermark',
    'yt_dlp.extractor.tiktok',
    'yt_dlp.extractor.xiaohongshu',
    'yt_dlp.networking._urllib',
    'app.services.task_service',
    'app.services.account_service',
    'app.services.remote_account_service',
    'app.models.schemas',
]

a = Analysis(
    ['run_backend.py'],
    pathex=[str(BACKEND_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='videomatrix-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
