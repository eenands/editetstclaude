"""FASE 0 — inventário do que existe de fato no ambiente (nada é presumido)."""
import importlib
import os
import platform
import shutil
import subprocess


def _ver(cmd):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except Exception:
        return None


def inspect_environment():
    env = {
        "platform": platform.platform(), "cpus": os.cpu_count(),
        "python": platform.python_version(),
        "ffmpeg": _ver(["ffmpeg", "-hide_banner", "-version"]) if shutil.which("ffmpeg") else None,
        "ffprobe": bool(shutil.which("ffprobe")),
        "node": _ver(["node", "--version"]) if shutil.which("node") else None,
        "libs": {},
        "ffmpeg_filters": {},
    }
    for mod in ("numpy", "scipy", "cv2", "PIL", "matplotlib"):
        try:
            m = importlib.import_module(mod)
            env["libs"][mod] = getattr(m, "__version__", "ok")
        except Exception:
            env["libs"][mod] = None
    if env["ffmpeg"]:
        flt = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        for f in ("scdet", "ebur128", "silencedetect", "minterpolate", "signalstats"):
            env["ffmpeg_filters"][f] = f" {f} " in flt
    try:
        mem = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        env["ram_gb"] = round(mem / 2 ** 30, 1)
    except (ValueError, OSError):
        env["ram_gb"] = None
    env["gpu_nvidia"] = bool(shutil.which("nvidia-smi"))
    # After Effects só existe em Windows/macOS; aerender é o renderizador de linha de comando.
    ae_bins = [b for b in ("aerender", "AfterFX", "AfterFX.exe") if shutil.which(b)]
    mac_ae = [p for p in ("/Applications",) if os.path.isdir(p)
              and any(n.startswith("Adobe After Effects") for n in os.listdir(p))]
    env["after_effects"] = bool(ae_bins or mac_ae)
    env["after_effects_note"] = (
        "After Effects encontrado." if env["after_effects"] else
        "After Effects NÃO disponível neste ambiente: o JSX é gerado e validado "
        "(sintaxe ES3 + mock do object model), mas não executado no AE.")
    return env
