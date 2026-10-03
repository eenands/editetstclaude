"""FASE 33 — expressions do After Effects + espelhos Python com a MESMA matemática.

Compatíveis com os dois motores de expressão do AE (JavaScript e ExtendScript legado):
sem let/arrow functions, vetores via add()/mul(), resultado final explícito.
O ruído é uma soma de senoides (determinística e reproduzível dos dois lados),
não o noise() Perlin do AE, cuja implementação não é pública.
"""
import math

M = 'thisComp.layer("CTRL_MASTER")'


def k_expr(*sliders):
    return " * ".join(f'{M}.effect("{s}")(1)' for s in ("GLOBAL_INTENSITY",) + sliders)


NOISE_JS = ("function N(t, s) { return 0.6 * Math.sin(6.2832 * t + s) + 0.3 * Math.sin(6.2832 * 2.31 * t + 1.3 * s)"
            " + 0.1 * Math.sin(6.2832 * 4.7 * t + 2.1 * s); }")
PARSE_JS = ("function P(c, k, d) { var a = c.split(\";\"); for (var i = 0; i < a.length; i++) {"
            " var kv = a[i].split(\"=\"); if (kv[0] == k) return parseFloat(kv[1]); } return d; }")


def noise(t, s):
    return 0.6 * math.sin(6.2832 * t + s) + 0.3 * math.sin(6.2832 * 2.31 * t + 1.3 * s) + 0.1 * math.sin(6.2832 * 4.7 * t + 2.1 * s)


def shake_position_js():
    return f"""// CONDE: shake procedural (MARKERS_IMPACT) + ambiente (CTRL_CAMERA)
var g = {k_expr("SHAKE_INTENSITY")};
var amb = thisComp.layer("CTRL_CAMERA").effect("AMBIENT_SHAKE")(1);
var Mk = thisComp.layer("MARKERS_IMPACT").marker;
{PARSE_JS}
{NOISE_JS}
var ox = 0, oy = 0;
if (Mk.numKeys > 0) {{
  var i = Mk.nearestKey(time).index;
  if (Mk.key(i).time > time) i--;
  for (var j = i; j >= 1; j--) {{
    var k = Mk.key(j); var dt = time - k.time;
    if (dt > 2.0) break;
    var c = k.comment;
    var env = P(c, "amp", 0) * Math.exp(-P(c, "decay", 6) * dt);
    if (env < 0.01) continue;
    var fr = P(c, "freq", 12), sd = P(c, "seed", j), bias = P(c, "bias", 0), dir = P(c, "dir", 0) * Math.PI / 180;
    var nd = N(dt * fr, sd + 41.9);
    ox += env * ((1 - bias) * N(dt * fr, sd) + bias * Math.cos(dir) * nd);
    oy += env * ((1 - bias) * N(dt * fr, sd + 17.3) + bias * Math.sin(dir) * nd);
  }}
}}
ox += amb * N(time * 1.3, 3.1); oy += amb * N(time * 1.3, 7.7);
value.length > 2 ? add(value, [ox * g, oy * g, 0]) : add(value, [ox * g, oy * g]);"""


def shake_rotation_js():
    return f"""// CONDE: rotação do shake (parâmetro rot dos marcadores)
var g = {k_expr("SHAKE_INTENSITY")};
var Mk = thisComp.layer("MARKERS_IMPACT").marker;
{PARSE_JS}
{NOISE_JS}
var r = 0;
if (Mk.numKeys > 0) {{
  var i = Mk.nearestKey(time).index;
  if (Mk.key(i).time > time) i--;
  for (var j = i; j >= 1; j--) {{
    var k = Mk.key(j); var dt = time - k.time;
    if (dt > 2.0) break;
    var c = k.comment;
    var env = P(c, "rot", 0) * Math.exp(-P(c, "decay", 6) * dt);
    if (env < 0.001) continue;
    r += env * N(dt * P(c, "freq", 12) * 0.8, P(c, "seed", j) + 5.5);
  }}
}}
value + r * g;"""


def parse_comment(c):
    out = {}
    for part in c.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = float(v)
    return out


def shake_eval(markers, t, amb, g):
    """Espelho Python de shake_position_js/shake_rotation_js. markers: [(time_s, comment)] ordenados."""
    ox = oy = r = 0.0
    idx = [j for j, (mt, _) in enumerate(markers) if mt <= t]
    for j in reversed(idx):
        mt, c = markers[j]
        dt = t - mt
        if dt > 2.0:
            break
        p = parse_comment(c)
        dc, fr, sd = p.get("decay", 6), p.get("freq", 12), p.get("seed", j + 1)
        env = p.get("amp", 0) * math.exp(-dc * dt)
        if env >= 0.01:
            bias, d = p.get("bias", 0), p.get("dir", 0) * math.pi / 180
            nd = noise(dt * fr, sd + 41.9)
            ox += env * ((1 - bias) * noise(dt * fr, sd) + bias * math.cos(d) * nd)
            oy += env * ((1 - bias) * noise(dt * fr, sd + 17.3) + bias * math.sin(d) * nd)
        er = p.get("rot", 0) * math.exp(-dc * dt)
        if er >= 0.001:
            r += er * noise(dt * fr * 0.8, sd + 5.5)
    ox += amb * noise(t * 1.3, 3.1)
    oy += amb * noise(t * 1.3, 7.7)
    return ox * g, oy * g, r * g


def zoom_scale_js():
    return f"""// CONDE: punch (keys desta camada) x drift x zoom-cut x reação ao grave
var zi = {k_expr("ZOOM_INTENSITY")};
var cam = thisComp.layer("CTRL_CAMERA"), au = thisComp.layer("CTRL_AUDIO");
var punch = 100 + (value[0] - 100) * zi;
var react = au.effect("BASS")(1) * au.effect("GATE_BASS_TO_ZOOM")(1) * {k_expr("AUDIO_REACT_INTENSITY")} * 2.5;
var s = punch * (cam.effect("DRIFT_SCALE")(1) / 100) * (cam.effect("CUT_ZOOM")(1) / 100) * (1 + react / 100);
value.length > 2 ? [s, s, value[2]] : [s, s];"""


def zoom_eval(punch_val, zi, drift, cutzoom, bass, gate, react_k):
    punch = 100 + (punch_val - 100) * zi
    react = bass * gate * react_k * 2.5
    return punch * (drift / 100) * (cutzoom / 100) * (1 + react / 100)


def focus_js():
    return """// CONDE: centro do zoom segue CAMERA_FOCUS (sujeito rastreado)
var F = thisComp.layer("CAMERA_FOCUS");
var q = F.toComp(F.anchorPoint);
var r = hasParent ? parent.fromComp(q) : q;
[r[0], r[1]];"""


def scaled_js(*sliders, rest=0.0):
    """Propriedade com keys escalada pelos controladores (keys continuam editáveis no Graph Editor)."""
    if rest:
        return f"var k = {k_expr(*sliders)};\n{rest} + (value - {rest}) * k;"
    return f"value * {k_expr(*sliders)};"


def treble_glow_js():
    return f"""// CONDE: glow = keys de evento + agudos (somente onde o gate do agudo está aberto)
var au = thisComp.layer("CTRL_AUDIO");
var tr = au.effect("TREBLE")(1) * au.effect("GATE_TREBLE_TO_GLOW")(1) * {k_expr("AUDIO_REACT_INTENSITY")};
(value + tr * 0.6) * {k_expr("GLOW_INTENSITY")};"""


def bracket_follow_js(track_layer):
    return f"""// CONDE: segue o track do sujeito
var T = thisComp.layer("{track_layer}");
var q = T.toComp(T.anchorPoint);
var r = hasParent ? parent.fromComp(q) : q;
[r[0], r[1]];"""


def rgb_offset_js(sign):
    return (f'var px = comp("MASTER_EDIT").layer("CTRL_VFX").effect("RGB_SPLIT_PX")(1) * '
            f'comp("MASTER_EDIT").layer("CTRL_MASTER").effect("GLOBAL_INTENSITY")(1) * '
            f'comp("MASTER_EDIT").layer("CTRL_MASTER").effect("VFX_INTENSITY")(1);\n'
            f"value.length > 2 ? add(value, [{sign} * px, 0, 0]) : add(value, [{sign} * px, 0]);")
