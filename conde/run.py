"""CONDE — pipeline completo (FASE 70).

    python -m conde.run VIDEO --out work/meu_video [--config estilo.json] [--fps 60]
                        [--mode music|dialogue|auto] [--name PROJETO] [--preview-width 960]
                        [--no-preview] [--debug-preview]

Gera em --out: analysis.json, frames.csv, frames/*.jpg, timeline.json, ae/BUILD_*.jsx,
PREVIEW.mp4, TIMELINE.md, REPORT.md, energy_timeline.png, quality.json.
"""
import argparse
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from .analysis.pipeline import analyze
from .config import load
from .decision.engine import decide
from .env_inspect import inspect_environment
from .generate.ae_jsx import write_jsx
from .generate.report import plot, report_md, timeline_md
from .render.preview import render
from .render.verify import verify_preview
from .validate.checks import quality

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "tests" / "js"


def _node_checks(jsx, video, an, out, log):
    res = {"es3": "não verificada (node/acorn ausente)", "mock": "não executado", "expr": "não avaliadas", "checklist": ""}
    node = shutil.which("node")
    if not node:
        return res
    acorn = JS / "node_modules" / "acorn"
    if acorn.exists():
        r = subprocess.run([node, str(JS / "es3_check.js"), str(jsx)], capture_output=True, text=True,
                           env={"ACORN_PATH": str(acorn)})
        res["es3"] = "OK" if r.returncode == 0 else "FALHOU: " + (r.stderr or r.stdout)[:300]
    meta = json.dumps({"width": an["width"], "height": an["height"], "frameRate": an["fps"],
                       "duration": an["n_frames"] / an["fps"]})
    dump = out / "ae" / "mock_dump.json"
    r = subprocess.run([node, str(JS / "mock_ae.js"), str(jsx), str(video), str(dump), meta], capture_output=True, text=True)
    if r.returncode != 0:
        res["mock"] = "FALHOU: " + r.stderr[-400:]
        return res
    m = re.search(r"(\d+) camadas, (\d+) keys, (\d+) expressions, (\d+) falhas", r.stdout)
    res["mock"] = (f"{m.group(1)} camadas, {m.group(2)} keys, {m.group(3)} expressions, {m.group(4)} falhas" if m else "?")
    for f in (out / "ae").glob("*_BUILD_LOG.txt"):
        txt = f.read_text(encoding="utf-8")
        res["checklist"] = txt[txt.find("CHECKLIST"):].split("\n\n")[0]
        f.replace(out / "ae" / "MOCK_BUILD_LOG.txt")
    shutil.rmtree(out / "ae" / "renders", ignore_errors=True)
    samples = out / "ae" / "expr_samples.json"
    h = subprocess.run([node, str(JS / "expr_harness.js"), str(dump), str(samples), "3"], capture_output=True, text=True)
    res["expr"] = h.stdout.strip().splitlines()[0] if h.stdout else "erro"
    dump.unlink(missing_ok=True)
    samples.unlink(missing_ok=True)
    log(f"[49] JSX: ES3 {res['es3']} | mock {res['mock']} | {res['expr']}")
    return res


def _concept(an, tl):
    au = an.get("audio")
    secs = ", ".join(f"{s['time']:g}s" for s in (au["sections"] if au else []))
    used = sorted({e.get("recipe") for e in tl["events"] if e["category"] in ("PUNCH", "FLASH", "BLUR", "DISTORTION", "VFX", "TRANSITION")})
    acc = "#%02x%02x%02x" % tuple(int(c * 255) for c in tl["accent_color"])
    return (f"Edição **{tl['mode']}** guiada pelo áudio"
            + (f" ({au['tempo']['bpm']} BPM, seções em {secs})" if au else "")
            + f": {len(an['shots'])} shots, cortes alinhados ao golpe, energia em arco (respiros → entradas → pico). "
            f"A câmera é um rig de nulls que reage ao áudio só onde a seção pede; impactos escalam por tiers "
            f"(small/medium/large) com vocabulário rotativo ({', '.join(used)}); transições derivam do movimento "
            f"do plano; texto ocupa o espaço negativo medido; uma única cor de acento ({acc}, derivada do material) "
            f"une brackets, linhas, anéis e partículas. Cada efeito tem um porquê registrado na timeline.")


def main(argv=None):
    ap = argparse.ArgumentParser(description="CONDE — edição procedural para After Effects")
    ap.add_argument("video")
    ap.add_argument("--out", required=True)
    ap.add_argument("--config")
    ap.add_argument("--fps", type=float)
    ap.add_argument("--mode", choices=["auto", "music", "dialogue"])
    ap.add_argument("--name")
    ap.add_argument("--preview-width", type=int, default=960)
    ap.add_argument("--no-preview", action="store_true")
    ap.add_argument("--debug-preview", action="store_true")
    ap.add_argument("--synthetic-note")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    log = print
    timings = {}
    T0 = time.time()

    t = time.time()
    env = inspect_environment()
    (out / "environment.json").write_text(json.dumps(env, indent=1, ensure_ascii=False))
    timings["0_ambiente"] = round(time.time() - t, 2)
    log(f"[0] ambiente: python {env['python']}, {env['cpus']} CPUs, AE: {'sim' if env['after_effects'] else 'não'}")

    t = time.time()
    an = analyze(args.video, out, log=log)
    timings["1-6_analise"] = round(time.time() - t, 2)
    for k, v in an["timings_s"].items():
        timings[f"   {k}"] = v

    ov = {}
    if args.fps:
        ov["comp_fps"] = args.fps
    if args.mode:
        ov["mode"] = args.mode
    if args.name:
        ov["project_name"] = args.name
    cfg = load(args.config, ov)
    t = time.time()
    tl = decide(an, cfg)
    timings["7-56_decisao"] = round(time.time() - t, 2)
    log(f"[7] timeline: {len(tl['events'])} eventos, {len(tl['layers'])} camadas, modo {tl['mode']}, "
        f"checks de build: {len(tl['build_checks'])}")

    prev, ver = {"size": [0, 0], "seconds": 0}, {"events_checked": 0, "passed": 0, "failed": [], "unexpected_black_frames": [], "strips": []}
    if not args.no_preview:
        t = time.time()
        prev = render(tl, args.video, out / "PREVIEW.mp4", width=args.preview_width, debug=False, log=log)
        timings["63_preview_render"] = round(time.time() - t, 2)
        ver = verify_preview(tl, prev, an, out / "frames")
        log(f"[40] verificação no preview: {ver['passed']}/{ver['events_checked']} eventos OK")
        if args.debug_preview:
            t = time.time()
            render(tl, args.video, out / "PREVIEW_debug.mp4", width=args.preview_width, debug=True, log=log)
            timings["63_preview_debug"] = round(time.time() - t, 2)
        prev.pop("metrics", None)
    q = quality(tl, an)
    log(f"[39] qualidade: {len(q['issues'])} pendência(s)")
    (out / "quality.json").write_text(json.dumps({"quality": q, "verify": ver}, indent=1, ensure_ascii=False, default=str))
    (out / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False))

    t = time.time()
    jsx, _ = write_jsx(tl, out / "ae", args.video)
    ae = _node_checks(jsx, args.video, an, out, log)
    ae["jsx_name"] = jsx.name
    timings["49_jsx+validacao"] = round(time.time() - t, 2)

    plot(tl, an, out / "energy_timeline.png")
    timeline_md(tl, out / "TIMELINE.md", f"Timeline — {Path(args.video).name}")
    timings["total"] = round(time.time() - T0, 2)
    n_layers = len(tl["layers"])
    ctx = {
        "title": Path(args.video).name, "env": env, "analysis": an, "timeline": tl, "quality": q, "verify": ver, "ae": ae,
        "preview": prev, "timings": timings, "synthetic_note": args.synthetic_note, "concept": _concept(an, tl),
        "systems": [
            "Câmera: CAMERA_MASTER › POSITION (X/Y separados) › ROTATION › ZOOM (centro = CAMERA_FOCUS no sujeito) › SHAKE.",
            "Shake procedural por marcadores (amp, freq, decay, rot, direção, bias) + shake ambiente por faixa de energia.",
            "Impactos: tiers small/medium/large, 7 receitas, orçamento por faixa, sem repetir receita seguida.",
            "Transições derivadas do movimento (whip na direção do conteúdo, zoom-through em entradas/drops, flash cut, glitch) com teste A/B.",
            "Tempo: micro-sync de cortes (±2 frames), speed ramps neutras que levam o hit visual ao golpe, freeze com release.",
            "Texto: placeholders no espaço negativo (grid 4×3), Range Selector por caractere/palavra, placa de legibilidade quando falta espaço.",
            "Gráficos: brackets presos ao track 2D do sujeito, anéis de impacto, linhas de acento — mesma cor e espessura.",
            "VFX: flash tingido pela cena, glow que acompanha a luz real, blur direcional, distorção curta, RGB split, partículas físicas.",
            "Cor: correção por shot (Exposure por canal, linear) ABAIXO do look (contraste, vibrance, vinheta).",
            "Áudio → dados: graves/médios/agudos/amplitude assados por frame; gates alternam UM canal reativo por seção.",
            f"Controladores: CTRL_MASTER com {len(tl['layers'][0]['effects'])} sliders de intensidade; CTRL_CAMERA, CTRL_AUDIO, CTRL_VFX.",
            f"Organização: pastas 00_MASTER…11_EXPORT, {n_layers} camadas nomeadas, labels por grupo, comentário com a função de cada camada.",
        ],
        "automations": [
            "Análise (Python/FFmpeg/OpenCV): ffprobe+EBU R128, onsets SuperFlux, beat tracking por programação dinâmica, "
            "cortes por histograma com rejeição de flash, movimento afim LK+RANSAC, passada adaptativa, tracking do sujeito.",
            "Decisão: motor que converte análise em eventos com keys Bezier (speed/influence) e expressions.",
            "Geração: BUILD_*.jsx autocontido que monta o projeto inteiro, salva com versão incremental e escreve BUILD_LOG + checklist.",
            "Validação sem AE: sintaxe ES3, mock do object model, avaliação de todas as expressions e comparação numérica com o preview.",
            "Preview: segundo interpretador da timeline com verificação automática frame a frame dos eventos.",
            "Ferramentas para rodar no AE: ae/tools/profile_systems.jsx (FASE 38) e ae/tools/toggle_heavy.jsx (FASE 37).",
        ],
        "optimizations": [
            "Passada 2 adaptativa: análise densa só onde importa "
            f"({an['adaptive']['dense_frames']}/{an['n_frames']} frames); fluxo denso e rostos em meia resolução "
            "(profiling apontou Haar + Farneback como 76% do tempo: 17,3 s → 5,5 s no clipe de teste).",
            "No AE: efeitos caros (distorção, glitch, RGB split, partículas) em camadas cortadas no evento — custo zero fora dele.",
            "Dados de áudio por frame aplicados com setValuesAtTimes (lote), não key a key.",
            "Profiling de render no AE não pôde ser medido aqui (sem AE): ae/tools/profile_systems.jsx renderiza janelas "
            "críticas desligando cada sistema e grava o custo de cada um em CSV.",
        ],
        "problems": [
            "After Effects indisponível neste ambiente: o projeto .aep NÃO foi construído nem renderizado aqui; "
            "o JSX foi validado por mock (que reflete o conhecimento da API, não o AE real). Rode o BUILD_*.jsx e leia o BUILD_LOG.",
            "Índices de parâmetros de alguns efeitos nativos (Exposure, Glow, Turbulent Displace, Shift Channels, Drop Shadow, "
            "CC Force Motion Blur) seguem a ordem documentada/conhecida, mas precisam de confirmação no BUILD_LOG.",
            "Rotoscopia não automatizada (exige Roto Brush/IA de segmentação): parallax real de foreground/background não foi feito; "
            "a profundidade 2.5D vem só da hierarquia de câmera e dos gráficos.",
            "3D Camera Tracker e planar tracking não são scriptáveis de forma confiável: o tracking é 2D (LK) calculado no pipeline.",
            "Textos são placeholders (sem transcrição/roteiro): forneça `text.copy` no config para o motor posicionar a copy real.",
            "Preview é aproximação (Pixel Motion → mistura de frames; Glow/Turbulent/Lens → equivalentes OpenCV; fonte DejaVu).",
            "Detecção de voz é heurística (sem modelo de ML); confianças de classificação são margens heurísticas.",
        ],
        "files": ["`analysis.json` — análise completa (mídia, áudio, shots, amostras densas, track, energia)",
                  "`frames.csv` — representação temporal FRAME 000000…N", "`frames/` — contact sheets, frames críticos e tiras de verificação",
                  "`timeline.json` — timeline machine-readable (FASE 51)", "`TIMELINE.md` — timeline legível (FASE 7/67)",
                  f"`ae/{jsx.name}` — script do After Effects", "`ae/MOCK_BUILD_LOG.txt` — log do build no mock",
                  "`PREVIEW.mp4` — render de preview", "`energy_timeline.png`", "`quality.json`", "`environment.json`"],
    }
    report_md(out / "REPORT.md", ctx)
    log(f"[65] relatório: {out / 'REPORT.md'} (total {timings['total']} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
