# CONDE — edição procedural para After Effects

Pipeline que **analisa** um vídeo (frames, movimento, shots, áudio), **decide** a edição
(cortes no golpe, speed ramps, impactos, transições, texto, VFX, cor), **gera** um script
ExtendScript que monta o projeto inteiro no After Effects e **verifica** o resultado
num preview renderizado, frame a frame.

> **Estado honesto desta entrega.** O vídeo a editar **não foi fornecido**: o repositório
> estava vazio e não havia mídia no ambiente. Este ambiente também não tem After Effects
> (Linux, sem AE), então nenhum projeto `.aep` foi montado ou renderizado aqui.
> Tudo foi construído e validado num **clipe sintético com gabarito conhecido**
> (`tests/synth.py`), e o JSX foi validado fora do AE (sintaxe ES3, mock do object model
> e avaliação de todas as expressions). Veja `examples/synthetic/REPORT.md`.

## Usar com o seu vídeo

```bash
pip install -r requirements.txt            # numpy, scipy, opencv<5, pillow, matplotlib, pytest
(cd tests/js && npm install)                # opcional: acorn, para checar a sintaxe ES3 do JSX
python -m conde.run SEU_VIDEO.mp4 --out work/meu_video --debug-preview
```

Opções: `--fps 60` (comp a 60 fps; a fonte é interpolada com Pixel Motion e o relatório avisa),
`--mode music|dialogue` (diálogo corta silêncios e desliga as rampas), `--config estilo.json`
(sobrescreve `conde/config.py`: orçamentos, tiers, fontes, a copy real dos textos em `text.copy`).

Depois, no After Effects: **File › Scripts › Run Script File…** →
`work/meu_video/ae/BUILD_<PROJETO>_v001.jsx`. O script monta o projeto, salva
`<PROJETO>_v00N.aep` (sem sobrescrever versões) e grava `_BUILD_LOG.txt` com o checklist
da Fase 64 e qualquer falha de efeito ou expression **medida dentro do AE**.
Ferramentas: `ae/tools/toggle_heavy.jsx` (preview leve) e `ae/tools/profile_systems.jsx`
(custo de render por sistema).

## Arquitetura (Fase 50)

```text
VIDEO
 → conde/analysis/media.py      ffprobe + EBU R128 (LUFS, true peak)
 → conde/analysis/video.py      passada 1 (todos os frames): luma, cor, bordas, movimento afim LK+RANSAC
                                passada 2 (adaptativa 8/4/2/1): fluxo denso, saliência, rostos, ROI,
                                espaço negativo; tracking 2D do sujeito
 → conde/analysis/audio.py      SuperFlux, kick/snare/hat, tempo, beats (DP), downbeats, drop/riser/silêncio/seções
 → conde/analysis/shots.py      cortes (histograma + rejeição de flash), descrição de cada shot
 → conde/analysis/energy.py     curva de energia 0–1
 → conde/decision/engine.py     edição base + microedição, rampas, freeze, transições (A/B), impactos
   conde/decision/systems.py    câmera, zoom-cuts, luz, glitch, partículas, gráficos, texto, cor, áudio→dados
 → timeline.json                contrato único (Fase 51)
 → conde/generate/ae_jsx.py     BUILD_*.jsx = timeline embutida + ae/lib/conde_core.jsx
 → conde/render/preview.py      2º interpretador da timeline (OpenCV/FFmpeg) → PREVIEW.mp4
 → conde/render/verify.py       revisão frame a frame no preview renderizado (Fase 40)
 → conde/validate/checks.py     repetição, densidade, respiros, testes sem áudio/só áudio, silhueta, pausa
 → conde/generate/report.py     TIMELINE.md, REPORT.md, energy_timeline.png
```

## O que é verificado e como

| o quê | como | onde |
|---|---|---|
| detectores (cortes, flash ≠ corte, direção de câmera, BPM, kicks, snares, drop, riser, silêncio, tracking) | contra o gabarito do clipe sintético | `tests/test_audio.py`, `tests/test_video.py` |
| keys / time remap | curva Bezier do AE reimplementada; rampas neutras; monotonicidade | `tests/test_curves.py` |
| regras de edição (cortes no golpe, sem transição repetida, respiros limpos, vocabulário) | invariantes sobre a timeline | `tests/test_engine.py` |
| JSX | ES3 (acorn) + execução completa num mock do object model + todas as expressions avaliadas; câmera comparada numericamente com o preview (erro < 1e-6) | `tests/test_ae_build.py`, `tests/js/` |
| alinhamento real | preview renderizado: pico de luz, escala do punch e troca de shot no frame do golpe | `tests/test_preview.py` |

O mock reflete o conhecimento da API do AE, não o AE real: matchNames e índices de parâmetros
de efeitos nativos só ficam confirmados pelo `BUILD_LOG` gerado dentro do After Effects.

```bash
python -m pytest tests/ -q
```
