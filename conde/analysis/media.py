"""FASE 1 — autópsia de metadados (ffprobe + EBU R128)."""
from fractions import Fraction

from ..io_ffmpeg import ffprobe, loudness, parse_rate


def _rotation(vs):
    for sd in vs.get("side_data_list", []) or []:
        if "rotation" in sd:
            return int(round(float(sd["rotation"])))
    r = (vs.get("tags") or {}).get("rotate")
    return int(r) if r else 0


def analyze_media(path):
    info = ffprobe(path)
    fmt = info.get("format", {})
    vstreams = [s for s in info["streams"] if s["codec_type"] == "video"
                and not (s.get("disposition") or {}).get("attached_pic")]
    astreams = [s for s in info["streams"] if s["codec_type"] == "audio"]
    if not vstreams:
        raise ValueError(f"sem stream de vídeo em {path}")
    vs = vstreams[0]
    r_rate = parse_rate(vs.get("r_frame_rate"))
    avg_rate = parse_rate(vs.get("avg_frame_rate"))
    fps = avg_rate or r_rate
    # preferimos r_frame_rate quando é "redondo" (29.97/30/60...) e próximo de avg
    if r_rate and avg_rate and abs(float(r_rate) - float(avg_rate)) / float(r_rate) < 0.002:
        fps = r_rate
    vfr = bool(r_rate and avg_rate and abs(float(r_rate) - float(avg_rate)) / float(r_rate) >= 0.002)
    rot = _rotation(vs)
    w, h = int(vs["width"]), int(vs["height"])
    dw, dh = (h, w) if rot % 180 else (w, h)
    duration = float(vs.get("duration") or fmt.get("duration") or 0)
    sar = vs.get("sample_aspect_ratio", "1:1")
    par = Fraction(*map(int, sar.split(":"))) if sar and sar not in ("0:1", "N/A") else Fraction(1)
    transfer = vs.get("color_transfer")
    hdr = {"arib-std-b67": "HLG", "smpte2084": "PQ"}.get(transfer)
    video = {
        "codec": vs.get("codec_name"), "profile": vs.get("profile"),
        "width": dw, "height": dh, "coded_width": w, "coded_height": h,
        "rotation": rot, "pixel_aspect": float(par),
        "aspect_ratio": round(dw * float(par) / dh, 4),
        "pix_fmt": vs.get("pix_fmt"),
        "bit_depth": int(vs.get("bits_per_raw_sample") or (10 if "10" in (vs.get("pix_fmt") or "") else 8)),
        "color_space": vs.get("color_space"), "color_transfer": transfer,
        "color_primaries": vs.get("color_primaries"), "hdr": hdr,
        "fps": float(fps), "fps_num": fps.numerator, "fps_den": fps.denominator,
        "r_frame_rate": vs.get("r_frame_rate"), "avg_frame_rate": vs.get("avg_frame_rate"),
        "vfr": vfr, "duration_s": duration,
        "nb_frames_header": int(vs["nb_frames"]) if vs.get("nb_frames", "").isdigit() else None,
        "duration_frames_est": int(round(duration * float(fps))),
        "bit_rate": int(vs["bit_rate"]) if vs.get("bit_rate", "").isdigit() else None,
    }
    audio = None
    if astreams:
        a = astreams[0]
        audio = {
            "codec": a.get("codec_name"), "sample_rate": int(a.get("sample_rate", 0)),
            "channels": int(a.get("channels", 0)), "channel_layout": a.get("channel_layout"),
            "duration_s": float(a.get("duration") or duration),
            "bit_rate": int(a["bit_rate"]) if a.get("bit_rate", "").isdigit() else None,
        }
        audio.update(loudness(path))
    warnings = []
    if vfr:
        warnings.append("Frame rate variável (VFR): análise usa grade CFR; no AE, conferir "
                        "Interpret Footage > Conform to frame rate.")
    if hdr:
        warnings.append(f"Material HDR ({hdr}): definir working space/ACES ou converter para "
                        "Rec.709 antes do grade; a correção automática assume SDR.")
    if rot:
        warnings.append(f"Metadado de rotação {rot}°: análise usa orientação de exibição.")
    if not audio:
        warnings.append("Sem áudio: decisões rítmicas usarão apenas sinais visuais.")
    return {
        "path": str(path),
        "container": fmt.get("format_name"), "size_bytes": int(fmt.get("size", 0)),
        "container_duration_s": float(fmt.get("duration", 0) or 0),
        "video": video, "audio": audio, "warnings": warnings,
    }
