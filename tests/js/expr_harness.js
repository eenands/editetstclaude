// Avalia TODAS as expressions da comp construída (dump do mock) num contexto que
// imita o motor JavaScript do After Effects: thisComp.layer().effect()(1), marker,
// toComp/fromComp pela cadeia de parentesco, add/mul/sub, inPoint, value.
// Arrays NÃO têm aritmética de operador (como no motor JS do AE): [a,b]*k vira NaN
// e é acusado. Saída: erros + valores amostrados para comparação com os espelhos Python.
"use strict";
const fs = require("fs");

const LINEAR = 6612, BEZIER = 6613, HOLD = 6614;

function evalKeys(keys, t, dim) {
  const get = (k) => (dim === undefined ? k.v : k.v[dim]);
  if (t <= keys[0].t) return get(keys[0]);
  if (t >= keys[keys.length - 1].t) return get(keys[keys.length - 1]);
  let i = 0;
  while (i < keys.length - 1 && keys[i + 1].t <= t) i++;
  const k0 = keys[i], k1 = keys[i + 1];
  const v0 = get(k0), v1 = get(k1), dt = k1.t - k0.t;
  if (k0.outInterp === HOLD) return v0;
  const slope = (v1 - v0) / dt;
  if (k0.outInterp === LINEAR && k1.inInterp === LINEAR) return v0 + slope * (t - k0.t);
  const d = dim === undefined ? 0 : Math.min(dim, (k0.outEase || [[0]]).length - 1);
  const [so, oi] = k0.outInterp === LINEAR || !k0.outEase ? [slope, 100 / 3] : k0.outEase[Math.min(d, k0.outEase.length - 1)];
  const di = dim === undefined ? 0 : Math.min(dim, (k1.inEase || [[0]]).length - 1);
  const [si, ii] = k1.inInterp === LINEAR || !k1.inEase ? [slope, 100 / 3] : k1.inEase[Math.min(di, k1.inEase.length - 1)];
  const x = [k0.t, k0.t + oi / 100 * dt, k1.t - ii / 100 * dt, k1.t];
  const y = [v0, v0 + so * oi / 100 * dt, v1 - si * ii / 100 * dt, v1];
  const B = (p, u) => (1 - u) ** 3 * p[0] + 3 * (1 - u) ** 2 * u * p[1] + 3 * (1 - u) * u * u * p[2] + u ** 3 * p[3];
  let lo = 0, hi = 1;
  for (let n = 0; n < 40; n++) { const u = (lo + hi) / 2; if (B(x, u) < t) lo = u; else hi = u; }
  return B(y, (lo + hi) / 2);
}

function propValue(p, t) {
  if (!p) return undefined;
  if (p.keys && p.keys.length && p.kind !== "marker") {
    const v0 = p.keys[0].v;
    if (Array.isArray(v0)) return v0.map((_, d) => evalKeys(p.keys, t, d));
    return evalKeys(p.keys, t);
  }
  return p.value;
}

function build(dump) {
  const comps = {};
  for (const [cname, c] of Object.entries(dump.comps)) {
    const byName = {};
    c.layers.forEach((L) => { byName[L.name] = L; });
    comps[cname] = { c, byName };
  }
  return comps;
}

const ERR = [];
let nEval = 0;

function makeEnv(comps, cname, t, depth) {
  const C = comps[cname];
  const findProp = (L, match) => Object.values(L.props).find((p) => p.match === match);
  function tval(L, match) {
    const p = findProp(L, match);
    if (match === "ADBE Position" && findProp(L, "ADBE Position_0") && findProp(L, "ADBE Position_0").keys.length) {
      return [propValue(findProp(L, "ADBE Position_0"), t), propValue(findProp(L, "ADBE Position_1"), t)];
    }
    if (p && p.expression && depth < 4) return evalExpr(comps, cname, L, p, t, depth + 1);
    return propValue(p, t);
  }
  function matrix(L) {
    const a = tval(L, "ADBE Anchor Point"), p = tval(L, "ADBE Position"), s = tval(L, "ADBE Scale"), r = tval(L, "ADBE Rotate Z") || 0;
    const sx = s[0] / 100, sy = s[1] / 100, c = Math.cos(r * Math.PI / 180), sn = Math.sin(r * Math.PI / 180);
    // M = T(p) R S T(-a)
    return [c * sx, -sn * sy, p[0] - (c * sx * a[0] - sn * sy * a[1]), sn * sx, c * sy, p[1] - (sn * sx * a[0] + c * sy * a[1])];
  }
  const mulM = (A, B) => [A[0] * B[0] + A[1] * B[3], A[0] * B[1] + A[1] * B[4], A[0] * B[2] + A[1] * B[5] + A[2],
    A[3] * B[0] + A[4] * B[3], A[3] * B[1] + A[4] * B[4], A[3] * B[2] + A[4] * B[5] + A[5]];
  function chain(L) { let M = matrix(L); for (let q = L.parent ? C.byName[L.parent] : null; q; q = q.parent ? C.byName[q.parent] : null) M = mulM(matrix(q), M); return M; }
  const apply = (M, v) => [M[0] * v[0] + M[1] * v[1] + M[2], M[3] * v[0] + M[4] * v[1] + M[5]];
  function inv(M) { const det = M[0] * M[4] - M[1] * M[3]; const a = M[4] / det, b = -M[1] / det, d = -M[3] / det, e = M[0] / det; return [a, b, -(a * M[2] + b * M[5]), d, e, -(d * M[2] + e * M[5])]; }
  function layerObj(L) {
    if (!L) throw new Error("layer inexistente");
    const o = {
      name: L.name, inPoint: L.in, outPoint: L.out,
      effect(n) {
        const ps = Object.entries(L.props).filter(([k]) => k.startsWith("ADBE Effect Parade/" + n + "/"));
        if (!ps.length) throw new Error(`efeito '${n}' não existe em ${L.name}`);
        return (i) => { const pp = ps[i - 1]; if (!pp) throw new Error(`parâmetro ${i} de ${n}`); return propValue(pp[1], t); };
      },
      get marker() {
        const mp = Object.values(L.props).find((p) => p.match === "ADBE Marker");
        const ks = mp ? mp.keys : [];
        const key = (i) => { const k = ks[i - 1]; if (!k) throw new Error("marker key " + i); return { time: k.t, comment: k.v.comment, duration: k.v.duration, index: i }; };
        return { numKeys: ks.length, key, nearestKey(tt) { let b = 0; ks.forEach((k, i) => { if (Math.abs(k.t - tt) < Math.abs(ks[b].t - tt)) b = i; }); return key(b + 1); } };
      },
      get anchorPoint() { return tval(L, "ADBE Anchor Point").slice(0, 2); },
      get transform() { return { position: tval(L, "ADBE Position"), anchorPoint: tval(L, "ADBE Anchor Point") }; },
      toComp(v) { return apply(chain(L), v); },
      fromComp(v) { return apply(inv(chain(L)), v); },
    };
    return o;
  }
  const thisComp = { layer: (n) => layerObj(C.byName[n] || (() => { throw new Error(`thisComp.layer("${n}") não existe`); })()), frameDuration: 1 / C.c.fps };
  const compFn = (n) => { if (!comps[n]) throw new Error(`comp("${n}") não existe`); const E = makeEnv(comps, n, t, depth); return E.thisComp; };
  return { thisComp, compFn, layerObj, chain };
}

function vec(a, b, f) {
  if (Array.isArray(a) && Array.isArray(b)) return a.map((x, i) => f(x, b[i]));
  throw new Error("add/sub precisam de dois arrays");
}

function evalExpr(comps, cname, L, p, t, depth = 0) {
  const env = makeEnv(comps, cname, t, depth);
  const C = comps[cname];
  const value = propValue(p, t);
  const add = (a, b) => vec(a, b, (x, y) => x + y);
  const sub = (a, b) => vec(a, b, (x, y) => x - y);
  const mul = (a, k) => (Array.isArray(a) ? a.map((x) => x * k) : a * k);
  const parent = L.parent ? env.layerObj(C.byName[L.parent]) : null;
  const fn = new Function("time", "value", "thisComp", "comp", "add", "sub", "mul", "hasParent", "parent", "inPoint", "thisLayer", "__src",
    "return eval(__src);");
  const v = fn(t, value, env.thisComp, env.compFn, add, sub, mul, !!L.parent, parent, L.in, env.layerObj(L), p.expression);
  nEval++;
  return v;
}

function main() {
  const [dumpPath, outPath, step] = process.argv.slice(2);
  const dump = JSON.parse(fs.readFileSync(dumpPath, "utf8"));
  const comps = build(dump);
  const master = comps.MASTER_EDIT.c;
  const st = parseInt(step || "3", 10);
  const samples = {};
  for (const [cname, C] of Object.entries(comps)) {
    for (const L of C.c.layers) {
      for (const [path, p] of Object.entries(L.props)) {
        if (!p.expression) continue;
        const key = `${cname}:${L.name}:${path}`;
        const series = [];
        // frames inteiros: t = f / fps é o MESMO double que o Python usa no espelho
        for (let f = Math.ceil(Math.max(0, L.in) * C.c.fps - 1e-9); f / C.c.fps < Math.min(L.out, C.c.duration); f += st) {
          const t = f / C.c.fps;
          try {
            const v = evalExpr(comps, cname, L, p, t);
            const arr = Array.isArray(v) ? v : [v];
            const wantDims = Array.isArray(p.value) || (p.keys.length && Array.isArray(p.keys[0].v)) ? (p.keys.length ? p.keys[0].v.length : p.value.length) : 1;
            if (arr.some((x) => typeof x !== "number" || !isFinite(x))) throw new Error("resultado não numérico: " + JSON.stringify(v));
            if (arr.length !== wantDims && !(wantDims === 3 && arr.length === 2)) throw new Error(`dimensão ${arr.length} != ${wantDims}`);
            series.push([f, arr]);
          } catch (e) {
            ERR.push(`${key} @ t=${t.toFixed(3)}: ${e.message}`);
            break;
          }
        }
        samples[key] = series;
      }
    }
  }
  fs.writeFileSync(outPath, JSON.stringify({ errors: ERR, evaluations: nEval, samples }));
  console.log(`expressions avaliadas: ${Object.keys(samples).length} propriedades, ${nEval} avaliações, ${ERR.length} erros`);
  ERR.slice(0, 20).forEach((e) => console.log("  ERRO " + e));
  process.exit(ERR.length ? 1 : 0);
}

main();
