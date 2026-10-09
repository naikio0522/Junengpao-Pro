import os
import sys
import json
import hmac
import socket
import threading
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.routes import router
from .api.accounts import router as account_router
from .api.standalone_variants import router as standalone_variant_router, standalone_variant_service
from .api.subtitles import router as subtitle_router
from .api.billing import router as billing_router
from .api.watermark_removal import router as watermark_removal_router, watermark_removal_service
from .api.link_watermark import router as link_watermark_router, link_watermark_service

# 确保工作目录正确，以便找到 ffmpeg
if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))

app = FastAPI(
    title="巨能跑pro版 API",
    description="巨能跑pro版短视频矩阵自动化混剪后端 API",
    version="0.1.9"
)

app.add_middleware(
    CORSMiddleware,
    # Packaged Electron uses a file:// renderer (Origin: null). A sandboxed
    # website can spoof that origin, so the per-launch API token below is the
    # actual authorization boundary; CORS is defence in depth.
    allow_origins=["null", "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["Content-Type", "Accept", "Authorization", "X-VideoMatrix-Token"],
)


@app.middleware("http")
async def require_desktop_api_token(request: Request, call_next):
    # Electron injects a fresh 256-bit token into its own backend process.
    # Manual developer/TestClient runs may omit the variable, but packaged
    # desktop traffic cannot access any API endpoint without the token.
    expected = os.environ.get("VIDEOMATRIX_API_TOKEN", "")
    if expected and request.url.path.startswith("/api/") and request.method != "OPTIONS":
        supplied = request.headers.get("X-VideoMatrix-Token", "")
        if not hmac.compare_digest(supplied, expected):
            return JSONResponse(status_code=403, content={"detail": "本机接口访问未授权"})
    return await call_next(request)

app.include_router(router, prefix="/api")
app.include_router(account_router, prefix="/api")
app.include_router(standalone_variant_router, prefix="/api")
app.include_router(subtitle_router, prefix="/api")
app.include_router(billing_router, prefix="/api")
app.include_router(watermark_removal_router, prefix="/api")
app.include_router(link_watermark_router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok", "version": app.version,
            "instance": os.environ.get("VIDEOMATRIX_INSTANCE", "")}


def main() -> None:
    """Entry point used by the PyInstaller binary.

    Accepts the same --host / --port arguments uvicorn does so the Electron
    main process can invoke the bundled exe identically across platforms.
    """
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(prog="videomatrix-backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    # Keep the socket open: port=0 is allocated atomically by the OS, with no
    # find-free-port/close/rebind race against another desktop application.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind((args.host, args.port))
    listener.listen(128)
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, log_level="info"))
    if os.environ.get("VIDEOMATRIX_INSTANCE"):
        print("VIDEOMATRIX_READY " + json.dumps({
            "port": listener.getsockname()[1], "version": app.version,
            "instance": os.environ["VIDEOMATRIX_INSTANCE"],
        }), flush=True)

        def watch_parent():
            # Electron owns stdin. EOF also handles an abnormal desktop exit.
            try:
                descriptor = sys.stdin.fileno()
                if os.name == 'nt':
                    # Poll the anonymous pipe without a pending blocking read.
                    # This allows native speech inference in request threads to
                    # progress while Electron keeps the pipe open and idle.
                    import ctypes
                    import msvcrt
                    from ctypes import wintypes

                    peek = ctypes.WinDLL('kernel32', use_last_error=True).PeekNamedPipe
                    peek.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                                     ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                                     ctypes.POINTER(wintypes.DWORD)]
                    peek.restype = wintypes.BOOL
                    pipe = wintypes.HANDLE(msvcrt.get_osfhandle(descriptor))
                    available = wintypes.DWORD()
                    while peek(pipe, None, 0, None, ctypes.byref(available), None):
                        if available.value:
                            if not os.read(descriptor, min(available.value, 4096)):
                                break
                        else:
                            time.sleep(0.2)
                else:
                    while os.read(descriptor, 1):
                        pass
            finally:
                from .services.task_service import task_service
                task_service.stop_all_tasks()
                standalone_variant_service.stop_all()
                watermark_removal_service.stop_all()
                link_watermark_service.stop_all()
                server.should_exit = True

        threading.Thread(target=watch_parent, daemon=True).start()
    try:
        server.run(sockets=[listener])
    finally:
        standalone_variant_service.stop_all()
        watermark_removal_service.stop_all()
        link_watermark_service.stop_all()
        listener.close()


if __name__ == "__main__":
    main()
