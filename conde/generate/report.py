"""FASES 7/65/67 — timeline legível, relatório final e gráfico energia × eventos."""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

LANES = [  # (rótulo, categorias) — ordem fixa = ordem fixa das cores categóricas
    ("Corte / transição", ("CUT", "TRANSITION")),
    ("Impacto / shake", ("PUNCH", "SHAKE")),
    ("Luz / flash", ("FLASH",)),
    ("Tempo (rampa, freeze)", ("SPEED_RAMP", "FREEZE", "TIME_REMAP")),
    ("Texto / gráfico", ("TEXT", "GRAPHIC")),
    ("VFX (blur, distorção, glitch, partículas)", ("BLUR", "DISTORTION", "GLITCH", "VFX", "PARTICLE")),
]
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
INK, INK2, GRID, SURF, NEUTRAL = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb", "#d9d8d3"


def plot(tl, analysis, out_png):
    fps = tl["comp"]["fps"]
    n = tl["comp"]["duration_frames"]
    t = [i / fps for i in range(n)]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(13, 5.6), sharex=True, gridspec_kw={"height_ratios": [1.1, 1.4]})
    fig.patch.set_facecolor(SURF)
    for ax in (a1, a2):
        ax.set_facecolor(SURF)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            ax.spines[s].set_color(GRID)
        ax.tick_params(colors=INK2, labelsize=8)
    for z in tl["zones"]["breath"]:
        for ax in (a1, a2):
            ax.axvspan(z["start_time"], z["end_time"], color=NEUTRAL, alpha=0.45, lw=0)
    cuts = [e["audio_sync"]["frame"] / fps for e in tl["events"] if e["category"] in ("CUT", "TRANSITION")
            and e.get("recipe") != "MICRO_SYNC" and e.get("audio_sync")]
    for c in cuts:
        for ax in (a1, a2):
            ax.axvline(c, color=INK2, lw=0.6, alpha=0.5)
    a1.plot(t, tl["energy_per_frame"], color=SERIES[0], lw=2)
    a1.set_ylim(0, 1.05)
    a1.set_ylabel("energia 0–1", color=INK2, fontsize=9)
    a1.grid(axis="y", color=GRID, lw=0.6)
    a1.set_title("Mapa de energia, respiros (cinza), cortes (linhas) e eventos de edição", loc="left", color=INK, fontsize=11)
    if analysis.get("audio"):
        for m in analysis["audio"]["markers"]:
            if m["type"] in ("DROP", "ENTRY", "IMPACT"):
                a1.annotate(m["type"], (m["frame"] / fps, 1.0), fontsize=7.5, color=INK2, ha="center", va="bottom")
    for i, (label, cats) in enumerate(LANES):
        y = len(LANES) - 1 - i
        for e in tl["events"]:
            if e["category"] in cats:
                a2.broken_barh([(e["start_frame"] / fps, max(1.5, e["duration_frames"]) / fps)], (y - 0.32, 0.64),
                               facecolor=SERIES[i], edgecolor=SURF, lw=1)
    a2.set_yticks(range(len(LANES)))
    a2.set_yticklabels([l for l, _ in LANES][::-1], fontsize=8.5, color=INK)
    a2.set_xlabel("tempo (s)", color=INK2, fontsize=9)
    a2.set_xlim(0, n / fps)
    fig.tight_layout()
    fig.savefig(out_png, dpi=130, facecolor=SURF)
    plt.close(fig)


def _fmt_t(s):
    m, sec = divmod(s, 60)
    return f"{int(m):02d}:{sec:06.3f}"


def _event_block(e):
    p = e.get("params", {})
    lines = [f"### {e['id']} — {e['category']} · {e.get('recipe', '')}", "```text"]
    lines.append(f"{_fmt_t(e['start_time'])} → {_fmt_t(e['end_time'])}")
    lines.append(f"FRAME {e['start_frame']} → {e['end_frame']}  ({e['duration_frames']} frames)")
    if e.get("detector"):
        lines.append(f"DETECTOR:  {e['detector']}")
    lines.append("ACTION:    " + " + ".join(e.get("action", [])))
    for k, v in p.items():
        if k in ("displace_steps", "rgb_px_steps", "breakpoints", "motion_blur_shutter"):
            continue
        lines.append(f"  {k}: {v}")
    lines.append(f"EASING:    {e.get('easing', '—')}")
    s = e.get("audio_sync")
    if s:
        mo = s.get("measured_offset_frames")
        lines.append(f"AUDIO:     {s.get('type')} @ frame {s.get('frame')}" +
                     (f" (offset medido {mo:+d} f)" if isinstance(mo, int) else ""))
    lines.append(f"INTENSITY: {e.get('intensity')}" + (f"  tier={e['tier']}" if e.get("tier") else ""))
    if e.get("scores"):
        lines.append("SCORES:    " + ", ".join(f"{k}={v}" for k, v in e["scores"].items()))
    lines.append(f"DEPENDS:   {', '.join(e.get('dependencies', [])) or '—'}")
    lines.append(f"WHY:       {e['why']}")
    lines.append(f"EXPECTED:  {e.get('expected', '')}")
    if e.get("ab_test"):
        ab = e["ab_test"]
        lines.append(f"A/B:       A={ab['version_a']['kind']} ({ab['version_a']['total']}) vs "
                     f"B={ab['version_b']['kind']} ({ab['version_b']['total']}) → {ab['chosen']}")
    lines.append("SCRIPT:    gerado automaticamente (BUILD_*.jsx)")
    lines.append(f"VALIDATION preview: {e['validation']['preview']}")
    lines.append(f"VALIDATION AE:      {e['validation']['after_effects']}")
    lines.append("```")
    return "\n".join(lines)


def timeline_md(tl, path, title):
    ev = tl["events"]
    main = [e for e in ev if e["category"] not in ("COLOR", "AUDIO_SYNC", "CAMERA", "TRACKING")]
    rows = ["| ID | tempo | frames | categoria | receita | intensidade | áudio | preview |", "|---|---|---|---|---|---|---|---|"]
    for e in ev:
        s = e.get("audio_sync") or {}
        val = e["validation"]["preview"].split(":")[0]
        rows.append(f"| {e['id']} | {_fmt_t(e['start_time'])} | {e['start_frame']}–{e['end_frame']} | {e['category']} | "
                    f"{e.get('recipe', '')} | {e.get('intensity', '')} | {s.get('type', '—')} | {val} |")
    body = [f"# {title}", "",
            f"Comp {tl['comp']['width']}×{tl['comp']['height']} @ {tl['comp']['fps']:g} fps · "
            f"{tl['comp']['duration_frames']} frames · {len(ev)} eventos · modo **{tl['mode']}**", "",
            "Fonte de verdade: `timeline.json` (lido pelo gerador do After Effects e pelo preview).", "",
            "## Todos os eventos", "", *rows, "", "## Eventos detalhados (formato FASE 67)", ""]
    body += [_event_block(e) + "\n" for e in main]
    Path(path).write_text("\n".join(body), encoding="utf-8")


def report_md(path, ctx):
    env, an, tl, q, ver, ae, prev, timings = (ctx[k] for k in ("env", "analysis", "timeline", "quality", "verify", "ae",
                                                                  "preview", "timings"))
    v, a = an["media"]["video"], an["media"]["audio"]
    au = an.get("audio")
    L = []
    w = L.append
    w(f"# Relatório — {ctx['title']}")
    w("")
    if ctx.get("synthetic_note"):
        w(f"> **{ctx['synthetic_note']}**")
        w("")
    w("## 0. O que foi executado de fato (REGRA ZERO)")
    w("")
    w("| etapa | estado |")
    w("|---|---|")
    w(f"| Inspeção do ambiente | executada — {env['platform']}, {env['cpus']} CPUs, {env.get('ram_gb')} GB, GPU NVIDIA: {'sim' if env['gpu_nvidia'] else 'não'} |")
    w("| Análise de mídia/áudio/vídeo | executada (números abaixo são medidos) |")
    w("| Decisão + timeline JSON | executada |")
    w(f"| Script do After Effects | **gerado** ({ae['jsx_name']}); validado fora do AE: sintaxe ES3 {ae['es3']}, "
      f"mock do object model {ae['mock']}; {ae['expr']} |")
    w(f"| Execução no After Effects | **não executada** — {env['after_effects_note']} |")
    w(f"| Render | **preview OpenCV/FFmpeg** ({prev['size'][0]}×{prev['size'][1]}) — aproximação da comp, não o render do AE |")
    w("")
    w("## 1. Resumo e conceito visual")
    w("")
    w(ctx["concept"])
    w("")
    w("## 2. Especificações")
    w("")
    w("```text")
    w(f"Resolution: {v['width']}x{v['height']} (PAR {v['pixel_aspect']}, AR {v['aspect_ratio']})")
    w(f"FPS:        fonte {v['fps']:g} ({v['r_frame_rate']}{', VFR' if v['vfr'] else ''}) → comp {tl['comp']['fps']:g}")
    w(f"Duration:   {v['duration_s']:.3f} s = {an['n_frames']} frames (saída: {tl['comp']['duration_frames']} frames)")
    w(f"Codec:      {v['codec']} {v.get('profile') or ''} {v['pix_fmt']} {v['bit_depth']}-bit"
      f"{' HDR ' + v['hdr'] if v['hdr'] else ''}, {round((v['bit_rate'] or 0) / 1e6, 2)} Mb/s")
    if a:
        w(f"Audio:      {a['codec']} {a['sample_rate']} Hz, {a['channels']} canais ({a['channel_layout']}), "
          f"{a.get('integrated_lufs')} LUFS, LRA {a.get('lra_lu')} LU, true peak {a.get('true_peak_dbtp')} dBTP")
    w("```")
    for m in an["media"]["warnings"]:
        w(f"- ⚠ {m}")
    w("")
    if au:
        w("### Áudio (FASE 5)")
        w("")
        cnt = {}
        for m in au["markers"]:
            cnt[m["type"]] = cnt.get(m["type"], 0) + 1
        w(f"- Tempo: **{au['tempo']['bpm']} BPM** (periodicidade {au['tempo']['periodicity']}, confiança {au['tempo']['confidence']}); "
          f"downbeats com confiança {au['tempo']['downbeat_confidence']}")
        w(f"- Modo detectado: **{au['mode']['value']}** — " + "; ".join(au["mode"]["reasons"]))
        w(f"- Marcadores: " + ", ".join(f"{k} {v}" for k, v in sorted(cnt.items())))
        w(f"- Frequência dominante: {au['dominant_freq_hz']} Hz · pico RMS {au['peak_rms_db']} dB · limiar de silêncio {au['silence_threshold_db']} dB")
        w(f"- Seções: " + ", ".join(f"{s['time']}s (novelty {s['novelty']})" for s in au["sections"]))
        w("")
    w("### Shots (FASE 4)")
    w("")
    w("| shot | início–fim | duração | movimento | câmera | sujeito | cor | energia | potencial |")
    w("|---|---|---|---|---|---|---|---|---|")
    for s in an["shots"]:
        w(f"| {s['id']} | {_fmt_t(s['start_time'])}–{_fmt_t(s['end_time'])} | {s['duration_s']:.3f}s | {s['motion_level']} | "
          f"{s['camera']} | {s['subject']} | {s['dominant_color']['name']} {s['dominant_color']['hex']} | {s['energy']} | "
          f"{', '.join(s['editing_potential']['tags']) or '—'} |")
    w("")
    w(f"Cortes: {len(an['cuts'])} · flashes/transientes rejeitados como corte: {len(an['visual_flashes'])} · "
      f"análise densa adaptativa em {an['adaptive']['dense_frames']}/{an['n_frames']} frames "
      f"(strides {an['adaptive']['stride_histogram']}) · representação frame a frame em `frames.csv`.")
    w("")
    w("### Mapa de energia (FASE 6)")
    w("")
    w("![energia e eventos](energy_timeline.png)")
    w("")
    w("| t (s) | " + " | ".join(f"{x['time']:g}" for x in an["energy"]["summary_0_5s"][::2]) + " |")
    w("|---|" + "---|" * len(an["energy"]["summary_0_5s"][::2]))
    w("| energia | " + " | ".join(f"{x['energy']:.2f}" for x in an["energy"]["summary_0_5s"][::2]) + " |")
    w("")
    w("## 3. Timeline (eventos principais)")
    w("")
    w("| tempo | frame | evento | ação | intensidade | sync de áudio | preview |")
    w("|---|---|---|---|---|---|---|")
    for e in tl["events"]:
        if e["category"] in ("COLOR", "AUDIO_SYNC", "CAMERA", "TRACKING"):
            continue
        s = e.get("audio_sync") or {}
        w(f"| {_fmt_t(e['start_time'])} | {e['start_frame']} | {e['id']} {e['category']}/{e.get('recipe', '')} | "
          f"{' + '.join(e.get('action', []))} | {e.get('intensity')} | {s.get('type', '—')} @ {s.get('frame', '—')} | "
          f"{e['validation']['preview'].split(':')[0]} |")
    w("")
    w("Detalhe de cada evento (DETECTOR, PARAMETERS, EASING, WHY, A/B, VALIDATION): **TIMELINE.md**.")
    w("")
    b = tl["base_edit"]
    w("### Edição base (FASE 9/10)")
    w("")
    ca = b["cut_alignment"]
    w(f"- {ca['cuts']} cortes; a ≤1 frame de um golpe: {ca['within_1f_before']} antes → **{ca['within_1f_after']} depois** "
      f"da microedição (offset médio {ca['mean_abs_offset_before']} → {ca['mean_abs_offset_after']} frames).")
    for op in b["operations"]:
        w(f"- {op}")
    w("- Sem efeitos, a edição continua funcionando? Os cortes caem no golpe, as rampas são neutras "
      "(a sincronia volta na borda de cada janela) e os freezes devolvem o tempo — sim no nível de ritmo; "
      "o julgamento estético final exige assistir ao render do AE.")
    w("")
    w("## 4. Sistemas criados")
    w("")
    for line in ctx["systems"]:
        w(f"- {line}")
    w("")
    w("## 5. Automações")
    w("")
    for line in ctx["automations"]:
        w(f"- {line}")
    w("")
    w("## 6. Otimizações e profiling")
    w("")
    w("| etapa | tempo (s) |")
    w("|---|---|")
    for k, val in timings.items():
        w(f"| {k} | {val} |")
    w("")
    for line in ctx["optimizations"]:
        w(f"- {line}")
    w("")
    w("## 7. Qualidade (FASES 39–45, 57–59) e verificação frame a frame (FASE 40)")
    w("")
    rd = q["repetition"]
    w("- Repetição: " + ", ".join(f"{k} {v}" for k, v in rd.items()))
    vd = q["visual_density"]
    w(f"- Densidade visual: média {vd['per_second_mean']}/s, máx {vd['per_second_max']}/s; faixas {vd['band_share']}; "
      f"correlação com a energia r = {vd['corr_with_energy']}")
    w(f"- Respiros: {q['breath']['zones']} zonas, {q['breath']['share']:.0%} do tempo, densidade interna {q['breath']['density_inside']}/s")
    w(f"- Teste de pausa: maior trecho com efeitos empilhados {q['pause_test']['longest_stacked_fx_s']} s")
    na, ao = q["no_audio_test"], q["audio_only_test"]
    w(f"- Teste sem áudio: {na['visual_events']} eventos visuais, {na['on_beat_grid']:.0%} na grade de beats, "
      f"intervalo médio {na['ioi_mean_s']} s (CV {na['ioi_cv']})")
    w(f"- Teste só com áudio: {ao['represented_visually']}/{ao['strong_audio_events']} golpes fortes representados "
      f"({ao['coverage']:.0%})" + (f"; sem representação: frames {ao['missed_frames']}" if ao["missed_frames"] else ""))
    w(f"- Silhueta: " + ", ".join(f"{s['layer']} {s['max_overlap_with_subject']:.0%}" for s in q["silhouette"]))
    w(f"- Verificação no preview renderizado: **{ver['passed']}/{ver['events_checked']}** eventos aprovados; "
      f"frames pretos inesperados: {len(ver['unexpected_black_frames'])}; tiras de revisão em `frames/verify_*.jpg`.")
    for fl in ver["failed"]:
        w(f"  - ✘ {fl['id']} {fl['recipe']}: {fl['checks']}")
    for fl in ver.get("inconclusive", []):
        w(f"  - ? {fl['id']} {fl['recipe']}: {fl['checks']} — não mensurável na imagem (registrado, não contado como aprovado)")
    if q["issues"]:
        w("- Pendências apontadas pelo sistema de qualidade:")
        for i in q["issues"]:
            w(f"  - [{i['severity']}, fase {i['phase']}] {i['msg']}")
    w("")
    w("### Checklist FASE 64 (executado no mock; repetir no AE via BUILD_LOG)")
    w("")
    w("```text")
    w(ae["checklist"].strip() or "(indisponível)")
    w("```")
    w("")
    w("## 8. Problemas e limitações")
    w("")
    for line in ctx["problems"] + tl["warnings"]:
        w(f"- {line}")
    w("")
    w("## 9. Arquivos")
    w("")
    for line in ctx["files"]:
        w(f"- {line}")
    Path(path).write_text("\n".join(L), encoding="utf-8")
