// Mock do object model do After Effects para executar o JSX gerado FORA do AE.
//
// O que ele prova: o script roda até o fim sem exceções de lógica, chama a API com
// dimensões/eases/enums coerentes, usa só matchNames do catálogo abaixo e produz a
// estrutura esperada (camadas, keys, expressions, marcadores, parentesco).
// O que ele NÃO prova: que o AE real aceita cada matchName/índice de parâmetro —
// o catálogo reflete o conhecimento do autor da API, não o AE. O BUILD_LOG gerado
// dentro do AE é a verificação definitiva.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ENUM = {
  KeyframeInterpolationType: { LINEAR: 6612, BEZIER: 6613, HOLD: 6614 },
  BlendingMode: { NORMAL: 5212, ADD: 5220, SCREEN: 5219 },
  MaskMode: { ADD: 6813, SUBTRACT: 6814 },
  ParagraphJustification: { LEFT_JUSTIFY: 7413, RIGHT_JUSTIFY: 7414, CENTER_JUSTIFY: 7415 },
  FrameBlendingType: { NO_FRAME_BLEND: 4012, FRAME_MIX: 4013, PIXEL_MOTION: 4014 },
  PropertyValueType: { NO_VALUE: 6412, ThreeD_SPATIAL: 6413, ThreeD: 6414, TwoD_SPATIAL: 6415, TwoD: 6416,
                       OneD: 6417, COLOR: 6418, CUSTOM_VALUE: 6419, MARKER: 6420, LAYER_INDEX: 6421,
                       MASK_INDEX: 6422, SHAPE: 6423, TEXT_DOCUMENT: 6424 },
};
const PVT = ENUM.PropertyValueType;

// catálogo de efeitos: matchName -> lista de parâmetros [tipo] (índice 1..n)
const EFFECTS = {
  "ADBE Slider Control": ["1"],
  "ADBE Tile": ["2s", "1", "1", "1", "1", "1", "1", "1"],
  "ADBE Motion Blur": ["1", "1"],
  "ADBE Tint": ["c", "c", "1"],
  "ADBE Glo2": ["1", "1", "1", "1", "1", "1", "c", "c", "1", "1", "1"],
  "ADBE Optics Compensation": ["1", "1", "1", "2s", "1", "1"],
  "ADBE Turbulent Displace": ["1", "1", "1", "2s", "1", "1", "1", "1", "1", "1", "1"],
  "ADBE Noise": ["1", "1", "1"],
  "ADBE Exposure2": ["1", "1", "1", "1", "1", "1", "1", "1", "1", "1", "1", "1", "1", "1"],
  "ADBE Brightness & Contrast 2": ["1", "1", "1"],
  "ADBE Vibrance": ["1", "1"],
  "CC Force Motion Blur": ["1", "1", "1", "1"],
  "ADBE Shift Channels": ["1", "1", "1", "1"],
  "ADBE Drop Shadow": ["c", "1", "1", "1", "1", "1"],
};
const SHAPE_ITEMS = {
  "ADBE Vector Shape - Ellipse": { "ADBE Vector Ellipse Size": "2", "ADBE Vector Ellipse Position": "2s" },
  "ADBE Vector Shape - Rect": { "ADBE Vector Rect Size": "2", "ADBE Vector Rect Position": "2s", "ADBE Vector Rect Roundness": "1" },
  "ADBE Vector Shape - Group": { "ADBE Vector Shape": "shape" },
  "ADBE Vector Graphic - Stroke": { "ADBE Vector Stroke Color": "c", "ADBE Vector Stroke Width": "1",
                                    "ADBE Vector Stroke Opacity": "1", "ADBE Vector Stroke Line Cap": "1" },
  "ADBE Vector Graphic - Fill": { "ADBE Vector Fill Color": "c", "ADBE Vector Fill Opacity": "1" },
  "ADBE Vector Filter - Trim": { "ADBE Vector Trim Start": "1", "ADBE Vector Trim End": "1", "ADBE Vector Trim Offset": "1" },
};
const TEXT_ANIM_PROPS = { "ADBE Text Position 3D": "3", "ADBE Text Opacity": "1", "ADBE Text Blur": "2",
                          "ADBE Text Scale 3D": "3", "ADBE Text Tracking Amount": "1", "ADBE Text Rotation": "1" };

const LOG = [];
function fail(msg) { throw new Error(msg); }

class KeyframeEase {
  constructor(speed, influence) {
    if (typeof speed !== "number" || !isFinite(speed)) fail("KeyframeEase speed inválido: " + speed);
    if (!(influence >= 0.1 && influence <= 100)) fail("KeyframeEase influence fora de [0.1,100]: " + influence);
    this.speed = speed; this.influence = influence;
  }
}

class Prop {
  // kind: "1" | "2" | "2s" | "3" | "3s" | "c" | "shape" | "marker" | "text" | "group"
  constructor(matchName, kind, parent, opts = {}) {
    this.matchName = matchName; this.name = opts.name || matchName; this.kind = kind; this.parentProperty = parent;
    this.children = []; this.addable = opts.addable || null; this.factory = opts.factory || null;
    this.keys = []; this._expr = ""; this.value = opts.value !== undefined ? opts.value : defaultValue(kind);
    this.isSpatial = kind === "2s" || kind === "3s";
    this.propertyValueType = { "1": PVT.OneD, "2": PVT.TwoD, "2s": PVT.TwoD_SPATIAL, "3": PVT.ThreeD, "3s": PVT.ThreeD_SPATIAL,
                               "c": PVT.COLOR, "shape": PVT.SHAPE, "marker": PVT.MARKER, "text": PVT.TEXT_DOCUMENT,
                               "group": PVT.NO_VALUE }[kind];
    this.easeDims = opts.easeDims || ({ "1": 1, "2": 2, "2s": 1, "3": 3, "3s": 1, "c": 1 }[kind] || 1);
    this.enabled = true;
  }
  get numProperties() { return this.children.length; }
  get numKeys() { return this.keys.length; }
  property(ref) {
    if (typeof ref === "number") return this.children[ref - 1] || null;
    let c = this.children.find(ch => ch.name === ref || ch.matchName === ref);
    if (c) return c;
    if (this.factory) { c = this.factory(ref, this); if (c) { this.children.push(c); return c; } }
    return null;
  }
  addProperty(m) {
    if (!this.addable || !this.addable(m)) fail(`addProperty('${m}') não permitido em '${this.matchName}'`);
    const c = this.makeChild(m);
    this.children.push(c);
    return c;
  }
  _check(v) {
    const k = this.kind;
    if (k === "group") fail(`'${this.name}' é grupo, não tem valor`);
    if (k === "1") { if (typeof v !== "number" || !isFinite(v)) fail(`'${this.name}' espera número, recebeu ${JSON.stringify(v)}`); return; }
    if (k === "c") { if (!Array.isArray(v) || v.length !== 4) fail(`'${this.name}' (cor) espera [r,g,b,a], recebeu ${JSON.stringify(v)}`); return; }
    if (k === "shape") { if (!(v instanceof Shape)) fail(`'${this.name}' espera Shape`); return; }
    if (k === "marker") { if (!(v instanceof MarkerValue)) fail(`'${this.name}' espera MarkerValue`); return; }
    if (k === "text") return;
    const n = parseInt(k, 10);
    if (!Array.isArray(v) || !(v.length === n || (n === 2 && v.length === 3 && k === "2s") || (n === 3 && v.length === 2 && this.allow2)))
      fail(`'${this.name}' espera ${n} dimensões, recebeu ${JSON.stringify(v)}`);
    if (v.some(x => typeof x !== "number" || !isFinite(x))) fail(`'${this.name}' valor não numérico ${JSON.stringify(v)}`);
  }
  setValue(v) { if (this.keys.length) fail(`setValue em '${this.name}' com keys`); this._check(v); this.value = v; }
  setValueAtTime(t, v) {
    if (this.timeGate && !this.timeGate()) fail(`keys em '${this.name}' sem habilitar (time remap)`);
    this._check(v);
    if (!isFinite(t)) fail("tempo inválido");
    const i = this.keys.findIndex(k => Math.abs(k.t - t) < 1e-9);
    const key = { t, v, inInterp: ENUM.KeyframeInterpolationType.LINEAR, outInterp: ENUM.KeyframeInterpolationType.LINEAR,
                  inEase: null, outEase: null };
    if (i >= 0) this.keys[i] = key; else { this.keys.push(key); this.keys.sort((a, b) => a.t - b.t); }
  }
  setValuesAtTimes(ts, vs) {
    if (ts.length !== vs.length) fail("setValuesAtTimes: tamanhos diferentes");
    for (let i = 0; i < ts.length; i++) this.setValueAtTime(ts[i], vs[i]);
  }
  nearestKeyIndex(t) {
    if (!this.keys.length) fail("nearestKeyIndex sem keys");
    let best = 0; this.keys.forEach((k, i) => { if (Math.abs(k.t - t) < Math.abs(this.keys[best].t - t)) best = i; });
    return best + 1;
  }
  _key(i) { const k = this.keys[i - 1]; if (!k) fail(`key ${i} inexistente em '${this.name}'`); return k; }
  keyInTemporalEase(i) { this._key(i); return Array.from({ length: this.easeDims }, () => new KeyframeEase(0, 33.33)); }
  setTemporalEaseAtKey(i, ein, eout) {
    const k = this._key(i);
    for (const arr of [ein, eout]) {
      if (!Array.isArray(arr) || arr.length !== this.easeDims) fail(`'${this.name}': esperava ${this.easeDims} eases, recebeu ${arr && arr.length}`);
      if (!arr.every(e => e instanceof KeyframeEase)) fail("ease não é KeyframeEase");
    }
    k.inEase = ein; k.outEase = eout;
  }
  setInterpolationTypeAtKey(i, a, b) {
    const ok = Object.values(ENUM.KeyframeInterpolationType);
    if (!ok.includes(a) || !ok.includes(b === undefined ? a : b)) fail("tipo de interpolação inválido");
    const k = this._key(i); k.inInterp = a; k.outInterp = b === undefined ? a : b;
  }
  setSpatialAutoBezierAtKey(i) { this._key(i); if (!this.isSpatial) fail("não espacial"); }
  setSpatialTangentsAtKey(i, a, b) { this._key(i); if (!this.isSpatial) fail("não espacial"); if (a.length !== b.length) fail("tangentes"); }
  removeKey(i) { this._key(i); this.keys.splice(i - 1, 1); }
  get expression() { return this._expr; }
  set expression(s) { if (this.kind === "group") fail("expression em grupo"); if (typeof s !== "string") fail("expression não é string"); this._expr = s; }
  get expressionError() { return ""; }
  set dimensionsSeparated(v) {
    if (this.matchName !== "ADBE Position") fail("dimensionsSeparated só em Position");
    this._sep = !!v;
    const tg = this.parentProperty;
    tg.property("ADBE Position_0").value = this.value[0];
    tg.property("ADBE Position_1").value = this.value[1];
  }
  get dimensionsSeparated() { return !!this._sep; }
  makeChild(m) { return this.childMaker ? this.childMaker(m, this) : fail("sem fábrica para " + m); }
}

function defaultValue(kind) {
  return { "1": 0, "2": [0, 0], "2s": [0, 0], "3": [0, 0, 0], "3s": [0, 0, 0], "c": [1, 1, 1, 1] }[kind];
}

function group(match, parent, opts) { return new Prop(match, "group", parent, opts); }

function effectProp(match, parent) {
  const spec = EFFECTS[match];
  if (!spec) fail(`efeito desconhecido no catálogo: '${match}'`);
  const fx = group(match, parent);
  spec.forEach((k, i) => {
    const p = new Prop(`${match}-${String(i + 1).padStart(4, "0")}`, k === "2s" ? "2s" : (k === "c" ? "c" : "1"), fx);
    fx.children.push(p);
  });
  fx.children.push(group("ADBE Effect Built In Params", fx));
  return fx;
}

function transformGroup(parent, layer) {
  const g = group("ADBE Transform Group", parent);
  const add = (m, k, v, o = {}) => { const p = new Prop(m, k, g, Object.assign({ value: v }, o)); g.children.push(p); return p; };
  add("ADBE Anchor Point", "3s", [0, 0, 0]).allow2 = true;
  const pos = add("ADBE Position", "3s", [0, 0, 0]); pos.allow2 = true;
  add("ADBE Position_0", "1", 0); add("ADBE Position_1", "1", 0); add("ADBE Position_2", "1", 0);
  const sc = add("ADBE Scale", "3", [100, 100, 100]); sc.allow2 = true;
  add("ADBE Rotate Z", "1", 0); add("ADBE Opacity", "1", 100);
  return g;
}

function textProps(layer, text) {
  const tp = group("ADBE Text Properties", layer);
  tp.children.push(new Prop("ADBE Text Document", "text", tp, { value: new TextDocument(text) }));
  const anims = group("ADBE Text Animators", tp, { addable: m => m === "ADBE Text Animator" });
  anims.childMaker = (m, par) => {
    const an = group("ADBE Text Animator", par);
    const props = group("ADBE Text Animator Properties", an, { addable: x => !!TEXT_ANIM_PROPS[x] });
    props.childMaker = (x, pp) => new Prop(x, TEXT_ANIM_PROPS[x], pp);
    const sels = group("ADBE Text Selectors", an, { addable: x => x === "ADBE Text Selector" });
    sels.childMaker = (x, pp) => {
      const s = group("ADBE Text Selector", pp);
      ["ADBE Text Percent Start", "ADBE Text Percent End", "ADBE Text Percent Offset"].forEach((n, i) =>
        s.children.push(new Prop(n, "1", s, { value: i === 1 ? 100 : 0 })));
      const adv = group("ADBE Text Range Advanced", s);
      ["ADBE Text Range Units", "ADBE Text Range Type2", "ADBE Text Selector Mode", "ADBE Text Range Shape",
       "ADBE Text Selector Smoothness", "ADBE Text Levels Max Ease", "ADBE Text Levels Min Ease"].forEach(n =>
        adv.children.push(new Prop(n, "1", adv)));
      s.children.push(adv);
      return s;
    };
    an.children.push(props, sels);
    return an;
  };
  tp.children.push(anims);
  return tp;
}

function shapeRoot(layer) {
  const root = group("ADBE Root Vectors Group", layer, { addable: m => m === "ADBE Vector Group" });
  root.childMaker = (m, par) => {
    const g = group("ADBE Vector Group", par);
    const vecs = group("ADBE Vectors Group", g, { addable: x => !!SHAPE_ITEMS[x] });
    vecs.childMaker = (x, pp) => {
      const it = group(x, pp);
      for (const [pm, k] of Object.entries(SHAPE_ITEMS[x])) it.children.push(new Prop(pm, k, it, { value: pm.endsWith("Trim End") ? 100 : undefined }));
      return it;
    };
    const tg = group("ADBE Vector Transform Group", g);
    [["ADBE Vector Anchor", "2s"], ["ADBE Vector Position", "2s"], ["ADBE Vector Scale", "2"], ["ADBE Vector Rotation", "1"],
     ["ADBE Vector Group Opacity", "1"]].forEach(([n, k]) => tg.children.push(new Prop(n, k, tg, { value: n === "ADBE Vector Scale" ? [100, 100] : (n === "ADBE Vector Group Opacity" ? 100 : undefined) })));
    g.children.push(vecs, tg);
    return g;
  };
  return root;
}

class TextDocument {
  constructor(t) { this.text = t; this.fontSize = 36; this.font = "ArialMT"; this.fillColor = [1, 1, 1]; this.applyFill = true;
    this.applyStroke = false; this.tracking = 0; this.leading = 0; this.autoLeading = true; this.justification = ENUM.ParagraphJustification.LEFT_JUSTIFY; }
  resetCharStyle() {} resetParagraphStyle() {}
}
class Shape { constructor() { this.vertices = []; this.inTangents = []; this.outTangents = []; this.closed = true; } }
class MarkerValue { constructor(c) { this.comment = c; this.duration = 0; } }

class Layer {
  constructor(comp, kind, source) {
    this.containingComp = comp; this.kind = kind; this.source = source || null; this.name = kind;
    this.inPoint = 0; this.outPoint = comp.duration; this._parent = null; this._tre = false;
    this.adjustmentLayer = false; this.guideLayer = false; this.motionBlur = false; this.blendingMode = ENUM.BlendingMode.NORMAL;
    this.enabled = true; this.audioEnabled = !!(source && source.hasAudio); this.label = 0; this.comment = "";
    this.hasAudio = !!(source && source.hasAudio); this.frameBlendingType = ENUM.FrameBlendingType.NO_FRAME_BLEND;
    this.groups = [transformGroup(this, this), (() => { const g = group("ADBE Effect Parade", this, { addable: m => !!EFFECTS[m] || fail(`efeito desconhecido no catálogo: '${m}'`) }); g.childMaker = effectProp; return g; })(),
      new Prop("ADBE Marker", "marker", this), (() => { const g = group("ADBE Mask Parade", this, { addable: m => m === "ADBE Mask Atom" }); g.childMaker = (m, p) => { const a = group(m, p); a.children.push(new Prop("ADBE Mask Shape", "shape", a), new Prop("ADBE Mask Feather", "2", a), new Prop("ADBE Mask Opacity", "1", a, { value: 100 })); a.maskMode = ENUM.MaskMode.ADD; return a; }; return g; })()];
    const tr = new Prop("ADBE Time Remapping", "1", this); tr.timeGate = () => this._tre; this.groups.push(tr);
    if (kind === "text") this.groups.push(textProps(this, source));
    if (kind === "shape") this.groups.push(shapeRoot(this));
    this.groups.find(g => g.matchName === "ADBE Marker").setValueAtTime = function (t, v) { Prop.prototype.setValueAtTime.call(this, t, v); };
  }
  property(ref) { const g = this.groups.find(x => x.matchName === ref || x.name === ref); if (!g) return null; return g; }
  get parent() { return this._parent; }
  set parent(p) { this.setParentWithJump(p); }
  setParentWithJump(p) {
    if (p === this) fail("parent em si mesmo");
    for (let q = p; q; q = q._parent) if (q === this) fail("ciclo de parentesco: " + this.name);
    if (p && p.containingComp !== this.containingComp) fail("parent de outra comp");
    this._parent = p;
  }
  get timeRemapEnabled() { return this._tre; }
  set timeRemapEnabled(v) {
    if (!(this.source && (this.source.typeName === "Footage" || this.source.typeName === "Composition"))) fail("time remap só em AV layer");
    this._tre = !!v;
    if (v) { const tr = this.property("ADBE Time Remapping"); tr.keys = []; tr.setValueAtTime(0, 0); tr.setValueAtTime(this.source.duration - 0.001, this.source.duration - 0.001); }
  }
}

class Items {
  constructor(project) { this.project = project; this.list = []; }
  get length() { return this.list.length; }
  addFolder(name) { const f = new FolderItem(name); this.list.push(f); this.sync(); return f; }
  addComp(name, w, h, par, dur, fps) {
    if (!(w >= 4 && h >= 4 && w <= 30000 && h <= 30000)) fail("tamanho de comp inválido");
    if (!(dur > 0 && fps >= 1 && fps <= 999)) fail(`duração/fps inválidos: ${dur} ${fps}`);
    const c = new CompItem(name, w, h, par, dur, fps); this.list.push(c); this.sync(); return c;
  }
  sync() { this.list.forEach((it, i) => { this[i + 1] = it; }); }
}
class FolderItem { constructor(n) { this.name = n; this.parentFolder = null; this.typeName = "Folder"; } }
class FootageItem {
  constructor(file, meta) { Object.assign(this, { name: path.basename(file.fsName), typeName: "Footage", footageMissing: !fs.existsSync(file.fsName),
    hasAudio: true, parentFolder: null, mainSource: { conformFrameRate: 0 } }, meta); }
}
class CompItem {
  constructor(name, w, h, par, dur, fps) {
    Object.assign(this, { name, width: w, height: h, pixelAspect: par, duration: dur, frameRate: fps, typeName: "Composition",
      motionBlur: false, shutterAngle: 180, frameBlending: false, parentFolder: null, hasAudio: true, _layers: [] });
    this.frameDuration = 1 / fps;
    const self = this;
    this.layers = {
      add(item) { if (item === self) fail("comp dentro de si mesma"); return self._push(new Layer(self, item.typeName === "Composition" ? "precomp" : "footage", item)); },
      addNull() { return self._push(new Layer(self, "null")); },
      addSolid(color, name, w, h, par) { if (!Array.isArray(color) || color.length !== 3) fail("addSolid cor [r,g,b]"); const L = new Layer(self, "solid", { typeName: "Footage", name, mainSource: {} }); L.name = name; return self._push(L); },
      addText(t) { if (typeof t !== "string") fail("addText string"); return self._push(new Layer(self, "text", t)); },
      addShape() { return self._push(new Layer(self, "shape")); },
    };
  }
  _push(L) { this._layers.unshift(L); return L; }
  get numLayers() { return this._layers.length; }
  layer(ref) { if (typeof ref === "number") return this._layers[ref - 1] || null; const L = this._layers.find(l => l.name === ref); if (!L) fail("layer não encontrada: " + ref); return L; }
  openInViewer() {}
}

function makeContext(scriptPath, footageMeta) {
  const project = { items: null, expressionEngine: "extendscript", saved: null,
    importFile(io) { if (!io.file.exists) fail("importFile: arquivo inexistente " + io.file.fsName); const f = new FootageItem(io.file, footageMeta); this.items.list.push(f); this.items.sync(); return f; },
    save(f) { this.saved = f.fsName; },
    renderQueue: { list: [], items: { add(c) { const it = { comp: c, timeSpanStart: 0, timeSpanDuration: c.duration, outputModule() { return this._om || (this._om = { file: null }); } }; project.renderQueue.list.push(it); return it; } } } };
  project.items = new Items(project);
  class File {
    constructor(p) { this.fsName = path.resolve(String(p)); this.encoding = "UTF-8"; this._buf = []; }
    get exists() { return fs.existsSync(this.fsName); }
    get parent() { return new Folder(path.dirname(this.fsName)); }
    get name() { return path.basename(this.fsName); }
    open() { return true; } writeln(s) { this._buf.push(String(s)); } write(s) { this._buf.push(String(s)); }
    close() { LOG.push({ file: this.fsName, lines: this._buf.length }); fs.writeFileSync(this.fsName, this._buf.join("\n")); return true; }
    static openDialog() { return null; }
  }
  class Folder { constructor(p) { this.fsName = path.resolve(String(p)); } get exists() { return fs.existsSync(this.fsName); } create() { fs.mkdirSync(this.fsName, { recursive: true }); return true; } }
  Folder.temp = new Folder(require("os").tmpdir());
  const alerts = [];
  const ctx = {
    app: { project, version: "mock", beginUndoGroup() {}, endUndoGroup() {}, newProject() {}, fonts: undefined },
    $: { fileName: scriptPath, os: "mock", get hiresTimer() { return 1000; } },
    File, Folder, FolderItem, CompItem, FootageItem, KeyframeEase, Shape, MarkerValue,
    ImportOptions: class { constructor(f) { this.file = f; } },
    alert: s => alerts.push(String(s)),
    ...ENUM,
  };
  ctx._alerts = alerts;
  return ctx;
}

// serializa a comp construída (para o harness de expressions e para os testes)
function dumpProp(p, out, prefix) {
  const here = prefix ? prefix + "/" + p.name : p.name;
  if (p.kind !== "group" && (p.keys.length || p._expr || p.kind === "marker")) {
    out[here] = { match: p.matchName, kind: p.kind, value: p.value, expression: p._expr || null,
      keys: p.keys.map(k => ({ t: k.t, v: k.v instanceof MarkerValue ? { comment: k.v.comment, duration: k.v.duration } : k.v,
        inInterp: k.inInterp, outInterp: k.outInterp,
        inEase: k.inEase && Array.from(k.inEase, e => [e.speed, e.influence]), outEase: k.outEase && Array.from(k.outEase, e => [e.speed, e.influence]) })) };
  } else if (p.kind !== "group" && p.kind !== "text" && p.kind !== "shape") {
    out[here] = { match: p.matchName, kind: p.kind, value: p.value, expression: null, keys: [] };
  }
  for (const c of p.children) dumpProp(c, out, here);
}

function run(scriptPath, footageMeta, dumpPath) {
  const src = fs.readFileSync(scriptPath, "utf8");
  const ctx = makeContext(scriptPath, footageMeta);
  vm.createContext(ctx);
  // ExtendScript não tem as APIs ES5 abaixo: se o script as usar, deve falhar aqui
  vm.runInContext("delete Array.prototype.forEach; delete Array.prototype.indexOf; delete Array.prototype.map;" +
                  "delete String.prototype.trim; delete this.JSON;", ctx);
  vm.runInContext(src, ctx, { filename: path.basename(scriptPath) });
  const res = vm.runInContext("CONDE", ctx);
  const comps = ctx.app.project.items.list.filter(i => i.typeName === "Composition");
  const master = comps.find(c => c.name === "MASTER_EDIT");
  const dump = { alerts: ctx._alerts, saved: ctx.app.project.saved, renderQueue: ctx.app.project.renderQueue.list.length,
    folders: ctx.app.project.items.list.filter(i => i.typeName === "Folder").map(f => f.name), comps: {} };
  for (const c of comps) {
    dump.comps[c.name] = { width: c.width, height: c.height, fps: c.frameRate, duration: c.duration, motionBlur: c.motionBlur,
      frameBlending: c.frameBlending, layers: c._layers.map(L => {
        const props = {};
        L.groups.forEach(g => dumpProp(g, props, ""));
        return { name: L.name, kind: L.kind, parent: L._parent ? L._parent.name : null, in: L.inPoint, out: L.outPoint,
          adjustment: L.adjustmentLayer, guide: L.guideLayer, blend: L.blendingMode, motionBlur: L.motionBlur,
          enabled: L.enabled, audioEnabled: L.audioEnabled, label: L.label, comment: L.comment,
          timeRemap: L._tre, frameBlendingType: L.frameBlendingType,
          text: L.kind === "text" ? L.property("ADBE Text Properties").property("ADBE Text Document").value : undefined,
          effects: L.property("ADBE Effect Parade").children.map(e => ({ name: e.name, match: e.matchName })), props };
      }) };
  }
  if (dumpPath) fs.writeFileSync(dumpPath, JSON.stringify(dump));
  return { dump, master, ctx };
}

module.exports = { run };

if (require.main === module) {
  const [script, footage, dumpPath, meta] = process.argv.slice(2);
  const fm = meta ? JSON.parse(meta) : { width: 1920, height: 1080, frameRate: 30, duration: 10 };
  const { dump } = run(script, fm, dumpPath);
  const buildLog = dump.saved ? dump.saved.replace(/\.aep$/, "_BUILD_LOG.txt") : null;
  console.log(dump.alerts.join("\n"));
  if (buildLog && fs.existsSync(buildLog)) console.log(fs.readFileSync(buildLog, "utf8"));
}
