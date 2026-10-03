// =============================================================================
// CONDE CORE — interpretador de timeline (conde.timeline/1) para Adobe After Effects
// ExtendScript (ES3). Gerado/consumido pelo pipeline Python "conde".
//
// Princípios:
//  * declarativo: o Python decide, este arquivo só executa o que está na timeline;
//  * cada operação roda em safe(): uma falha (ex.: matchName de efeito diferente
//    numa versão do AE) é registrada no BUILD_LOG e o build continua;
//  * acesso por matchName/índice (independe do idioma da interface do AE);
//  * ao final: verificação de expressions (expressionError), checklist FASE 64,
//    projeto salvo com versão incremental (FASE 62).
// =============================================================================
var CONDE = (function () {
    var LOG = [], STATS = {layers: 0, effects: 0, keys: 0, expressions: 0, markers: 0, failures: 0};
    var EXPR_PROPS = [];

    function log(level, msg) { LOG.push("[" + level + "] " + msg); }
    function safe(desc, fn) {
        try { return fn(); } catch (e) {
            STATS.failures++;
            log("FALHA", desc + " :: " + e.toString() + (e.line ? " (linha " + e.line + ")" : ""));
            return null;
        }
    }
    function pad(n, w) { var s = String(n); while (s.length < w) { s = "0" + s; } return s; }
    function has(o, k) { return o !== null && o !== undefined && Object.prototype.hasOwnProperty.call(o, k); }

    // ------------------------------------------------------------- projeto
    function folder(name, parent) {
        var items = app.project.items;
        for (var i = 1; i <= items.length; i++) {
            var it = items[i];
            if (it instanceof FolderItem && it.name === name && (!parent || it.parentFolder === parent)) { return it; }
        }
        var f = items.addFolder(name);
        if (parent) { f.parentFolder = parent; }
        return f;
    }
    var FOLDERS = ["00_MASTER", "01_FOOTAGE", "02_AUDIO", "03_COMPS", "04_PRECOMPS", "05_VFX", "06_TEXT",
                   "07_GRAPHICS", "08_TRACKING", "09_CONTROLLERS", "10_PRE_RENDERS", "11_EXPORT"];

    function findFootage(tl) {
        var p = tl.project.source, f = new File(p);
        if (f.exists) { return f; }
        var here = new File($.fileName).parent;
        var name = p.replace(/^.*[\\\/]/, "");
        var cands = [new File(here.fsName + "/" + name), new File(here.parent.fsName + "/" + name)];
        for (var i = 0; i < cands.length; i++) { if (cands[i].exists) { return cands[i]; } }
        log("AVISO", "vídeo-fonte não encontrado em '" + p + "' — pedindo ao usuário");
        return File.openDialog("CONDE: selecione o vídeo-fonte (" + name + ")");
    }

    // ------------------------------------------------------------- propriedades
    var TR = {"transform.anchor": "ADBE Anchor Point", "transform.position": "ADBE Position",
              "transform.x": "ADBE Position_0", "transform.y": "ADBE Position_1", "transform.scale": "ADBE Scale",
              "transform.rotation": "ADBE Rotate Z", "transform.opacity": "ADBE Opacity"};
    var SHAPE_ITEM = {"ellipse": "ADBE Vector Shape - Ellipse", "rect": "ADBE Vector Shape - Rect",
                      "path": "ADBE Vector Shape - Group", "stroke": "ADBE Vector Graphic - Stroke",
                      "fill": "ADBE Vector Graphic - Fill", "trim": "ADBE Vector Filter - Trim"};
    var SHAPE_PROP = {"ellipse.size": ["ellipse", "ADBE Vector Ellipse Size"], "rect.size": ["rect", "ADBE Vector Rect Size"],
                      "stroke.width": ["stroke", "ADBE Vector Stroke Width"], "stroke.color": ["stroke", "ADBE Vector Stroke Color"],
                      "fill.color": ["fill", "ADBE Vector Fill Color"], "trim.start": ["trim", "ADBE Vector Trim Start"],
                      "trim.end": ["trim", "ADBE Vector Trim End"]};
    var SHAPE_TR = {"transform.position": "ADBE Vector Position", "transform.scale": "ADBE Vector Scale",
                    "transform.opacity": "ADBE Vector Group Opacity"};

    function paramOf(fx, ref) {
        if (/^\d+$/.test(ref)) { return fx.property(parseInt(ref, 10)); }
        return fx.property(ref);
    }

    function resolve(layer, path) {
        if (has(TR, path)) { return layer.property("ADBE Transform Group").property(TR[path]); }
        if (path === "timeremap") { return layer.property("ADBE Time Remapping"); }
        var p = path.split(":");
        if (p[0] === "effect") { return paramOf(layer.property("ADBE Effect Parade").property(p[1]), p[2]); }
        if (p[0] === "shape") {
            var grp = layer.property("ADBE Root Vectors Group").property(p[1]);
            if (has(SHAPE_TR, p[2])) { return grp.property("ADBE Vector Transform Group").property(SHAPE_TR[p[2]]); }
            var sp = SHAPE_PROP[p[2]];
            return grp.property("ADBE Vectors Group").property(sp[0]).property(sp[1]);
        }
        throw new Error("caminho de propriedade desconhecido: " + path);
    }

    function color4(v) { return v.length === 3 ? [v[0], v[1], v[2], 1] : v; }

    function setValueSmart(prop, v) {
        if (v instanceof Array && prop.propertyValueType === PropertyValueType.COLOR) { v = color4(v); }
        prop.setValue(v);
    }

    var INTERP = null;
    function interp(name) {
        if (!INTERP) {
            INTERP = {"BEZIER": KeyframeInterpolationType.BEZIER, "LINEAR": KeyframeInterpolationType.LINEAR,
                      "HOLD": KeyframeInterpolationType.HOLD};
        }
        return INTERP[name];
    }
    function infl(x) { return Math.max(0.1, Math.min(100, x * 100)); }

    function applyKeys(prop, keys, fps, what) {
        var times = [], vals = [], i, isColor = prop.propertyValueType === PropertyValueType.COLOR;
        for (i = 0; i < keys.length; i++) {
            times.push(keys[i].f / fps);
            vals.push(isColor ? color4(keys[i].v) : keys[i].v);
        }
        prop.setValuesAtTimes(times, vals);
        for (i = 0; i < keys.length; i++) {
            var k = keys[i], idx = prop.nearestKeyIndex(times[i]);
            var it = interp(k.interp_in), ot = interp(k.interp_out);
            prop.setInterpolationTypeAtKey(idx, it, ot);
            if (k.interp_in === "BEZIER" || k.interp_out === "BEZIER") {
                var n = prop.keyInTemporalEase(idx).length, ein = [], eout = [];
                for (var d = 0; d < n; d++) {
                    ein.push(new KeyframeEase(k.in_speed, infl(k.in_infl)));
                    eout.push(new KeyframeEase(k.out_speed, infl(k.out_infl)));
                }
                prop.setTemporalEaseAtKey(idx, ein, eout);
            }
            if (prop.isSpatial) {
                try {
                    prop.setSpatialAutoBezierAtKey(idx, false);
                    var z = []; for (var q = 0; q < prop.value.length; q++) { z.push(0); }
                    prop.setSpatialTangentsAtKey(idx, z, z);
                } catch (e1) { /* opcional */ }
            }
            STATS.keys++;
        }
    }

    function setExpr(prop, text, what) {
        prop.expression = text;
        STATS.expressions++;
        EXPR_PROPS.push([prop, what]);
    }

    // ------------------------------------------------------------- construção de camadas
    var LABEL = {"CONTROLLERS": 1, "CAMERA": 2, "FOOTAGE": 0, "VFX": 9, "TEXT": 11, "GRAPHICS": 13, "COLOR": 14,
                 "TRACKING": 6, "AUDIO": 3};

    function ellipseShape(w, h, cx, cy) {
        var k = 0.5523, a = w / 2, b = h / 2, s = new Shape();
        s.vertices = [[cx, cy - b], [cx + a, cy], [cx, cy + b], [cx - a, cy]];
        s.inTangents = [[-a * k, 0], [0, -b * k], [a * k, 0], [0, b * k]];
        s.outTangents = [[a * k, 0], [0, b * k], [-a * k, 0], [0, -b * k]];
        s.closed = true;
        return s;
    }

    function buildShapeContents(layer, spec) {
        var root = layer.property("ADBE Root Vectors Group");
        var groups = spec.shape.groups;
        for (var g = 0; g < groups.length; g++) {
            var G = groups[g];
            var grp = root.addProperty("ADBE Vector Group");
            grp.name = G.name;
            for (var i = 0; i < G.items.length; i++) {
                var it = G.items[i];
                var cont = root.property(G.name).property("ADBE Vectors Group");
                var p = cont.addProperty(SHAPE_ITEM[it.type]);
                p.name = it.type;
                p = root.property(G.name).property("ADBE Vectors Group").property(it.type);
                if (it.type === "ellipse") { p.property("ADBE Vector Ellipse Size").setValue(it.size); }
                if (it.type === "rect") {
                    p.property("ADBE Vector Rect Size").setValue(it.size);
                    if (it.center) { p.property("ADBE Vector Rect Position").setValue(it.center); }
                }
                if (it.type === "path") {
                    var sh = new Shape();
                    sh.vertices = it.points;
                    sh.closed = it.closed;
                    p.property("ADBE Vector Shape").setValue(sh);
                }
                if (it.type === "stroke") {
                    p.property("ADBE Vector Stroke Color").setValue(color4(it.color));
                    p.property("ADBE Vector Stroke Width").setValue(it.width);
                    safe("line cap " + layer.name, function () { p.property("ADBE Vector Stroke Line Cap").setValue(2); });
                }
                if (it.type === "fill") { p.property("ADBE Vector Fill Color").setValue(color4(it.color)); }
            }
            if (G.physics) {   // partícula: física determinística em expressions (espelhada no preview)
                var ph = G.physics, gt = root.property(G.name).property("ADBE Vector Transform Group");
                setExpr(gt.property("ADBE Vector Position"),
                    "var t = Math.max(0, time - inPoint); var e = (1 - Math.exp(-" + ph.drag + " * t)) / " + ph.drag + ";\n" +
                    "[" + ph.vx + " * e, " + ph.vy + " * e + 0.5 * " + ph.g + " * t * t];", layer.name + "/" + G.name + ".pos");
                setExpr(gt.property("ADBE Vector Group Opacity"),
                    "var t = Math.max(0, time - inPoint); 100 * Math.max(0, 1 - t / " + ph.life + ") * " +
                    "thisComp.layer(\"CTRL_MASTER\").effect(\"GLOBAL_INTENSITY\")(1) * thisComp.layer(\"CTRL_MASTER\").effect(\"PARTICLE_INTENSITY\")(1);",
                    layer.name + "/" + G.name + ".opacity");
                setExpr(gt.property("ADBE Vector Scale"),
                    "var t = Math.max(0, time - inPoint); var s = 100 * Math.max(0.05, 1 - t / " + ph.life + "); [s, s];",
                    layer.name + "/" + G.name + ".scale");
            }
        }
    }

    function chooseFont(pref, fallback) {
        try {
            if (app.fonts && app.fonts.getFontsByPostScriptName) {
                if (app.fonts.getFontsByPostScriptName(pref).length > 0) { return pref; }
                log("AVISO", "fonte '" + pref + "' não instalada — usando '" + fallback + "'");
                return fallback;
            }
        } catch (e) { /* API de fontes indisponível (AE < 24) */ }
        return pref;
    }

    function addTextAnimator(layer, name, a, mode, f0, f1, fps) {
        var animators = layer.property("ADBE Text Properties").property("ADBE Text Animators");
        var an = animators.addProperty("ADBE Text Animator");
        an.name = name;
        var props = function () { return layer.property("ADBE Text Properties").property("ADBE Text Animators").property(name).property("ADBE Text Animator Properties"); };
        var list = [["ADBE Text Position 3D", [a.position[0], a.position[1], 0]], ["ADBE Text Opacity", a.opacity],
                    ["ADBE Text Blur", [a.blur, a.blur]], ["ADBE Text Scale 3D", [a.scale, a.scale, 100]],
                    ["ADBE Text Tracking Amount", a.tracking]];
        var i;
        for (i = 0; i < list.length; i++) { props().addProperty(list[i][0]); }
        for (i = 0; i < list.length; i++) { props().property(list[i][0]).setValue(list[i][1]); }
        var k = "thisComp.layer(\"CTRL_MASTER\").effect(\"TEXT_INTENSITY\")(1)";
        // vetores: mul() (o motor JavaScript do AE não sobrecarrega operadores em arrays)
        setExpr(props().property("ADBE Text Position 3D"), "mul(value, " + k + ");", layer.name + "/" + name + ".position");
        setExpr(props().property("ADBE Text Blur"), "mul(value, " + k + ");", layer.name + "/" + name + ".blur");
        var sel = layer.property("ADBE Text Properties").property("ADBE Text Animators").property(name)
            .property("ADBE Text Selectors").addProperty("ADBE Text Selector");
        sel = layer.property("ADBE Text Properties").property("ADBE Text Animators").property(name)
            .property("ADBE Text Selectors").property(1);
        var adv = sel.property("ADBE Text Range Advanced");
        safe("based on " + layer.name, function () { adv.property("ADBE Text Range Type2").setValue(a.based_on === "words" ? 3 : 1); });
        safe("smoothness " + layer.name, function () { adv.property("ADBE Text Selector Smoothness").setValue(100); });
        var target = sel.property(mode === "in" ? "ADBE Text Percent Start" : "ADBE Text Percent End");
        if (mode === "out") { sel.property("ADBE Text Percent End").setValue(0); }
        applyKeys(target, [
            {f: f0, v: 0, in_speed: 0, out_speed: 0, in_infl: 0.33, out_infl: mode === "in" ? 0.1 : 0.6, interp_in: "BEZIER", interp_out: "BEZIER"},
            {f: f1, v: 100, in_speed: 0, out_speed: 0, in_infl: mode === "in" ? 0.85 : 0.2, out_infl: 0.33, interp_in: "BEZIER", interp_out: "BEZIER"}
        ], fps, layer.name + "/" + name);
    }

    function buildText(comp, spec, fps) {
        var T = spec.text;
        var layer = comp.layers.addText(T.text);
        var tdp = layer.property("ADBE Text Properties").property("ADBE Text Document");
        var doc = tdp.value;
        safe("reset estilo " + spec.name, function () { doc.resetCharStyle(); doc.resetParagraphStyle(); });
        doc.fontSize = T.size;
        doc.font = chooseFont(T.font, T.fallback_font);
        doc.applyFill = true;
        doc.fillColor = T.color;
        doc.applyStroke = false;
        doc.tracking = T.tracking;
        safe("leading " + spec.name, function () { doc.autoLeading = false; doc.leading = T.leading; });
        doc.justification = T.justify === "LEFT" ? ParagraphJustification.LEFT_JUSTIFY :
            (T.justify === "RIGHT" ? ParagraphJustification.RIGHT_JUSTIFY : ParagraphJustification.CENTER_JUSTIFY);
        tdp.setValue(doc);
        var a = {position: T.animator.position, opacity: T.animator.opacity, blur: T.animator.blur,
                 scale: T.animator.scale, tracking: T.animator.tracking, based_on: T.based_on};
        safe("ANIM_IN " + spec.name, function () { addTextAnimator(layer, "ANIM_IN", a, "in", T.in_frames[0], T.in_frames[1], fps); });
        safe("ANIM_OUT " + spec.name, function () { addTextAnimator(layer, "ANIM_OUT", a, "out", T.out_frames[0], T.out_frames[1], fps); });
        safe("sombra " + spec.name, function () {
            var ds = layer.property("ADBE Effect Parade").addProperty("ADBE Drop Shadow");
            ds.name = "SHADOW";
            ds = layer.property("ADBE Effect Parade").property("SHADOW");
            ds.property(2).setValue(T.shadow.opacity * 2.55);
            ds.property(4).setValue(T.shadow.distance);
            ds.property(5).setValue(T.shadow.softness);
            STATS.effects++;
        });
        return layer;
    }

    function addEffects(layer, spec) {
        for (var i = 0; i < spec.effects.length; i++) {
            (function (E) {
                safe("efeito " + E.match + " (" + E.name + ") em " + spec.name, function () {
                    var fx = layer.property("ADBE Effect Parade").addProperty(E.match);
                    fx.name = E.name;
                    fx = layer.property("ADBE Effect Parade").property(E.name);
                    STATS.effects++;
                    for (var k in E.params) {
                        if (has(E.params, k)) {
                            (function (key, val) {
                                safe("parâmetro " + key + " de " + E.name + " em " + spec.name, function () {
                                    setValueSmart(paramOf(fx, key), val);
                                });
                            })(k, E.params[k]);
                        }
                    }
                });
            })(spec.effects[i]);
        }
    }

    function addMasks(layer, spec, W, H) {
        if (!spec.masks) { return; }
        for (var i = 0; i < spec.masks.length; i++) {
            var M = spec.masks[i];
            var m = layer.property("ADBE Mask Parade").addProperty("ADBE Mask Atom");
            var inset = M.inset || 0;
            m.property("ADBE Mask Shape").setValue(ellipseShape(W * (1 - 2 * inset), H * (1 - 2 * inset), W / 2, H / 2));
            m.property("ADBE Mask Feather").setValue([M.feather, M.feather]);
            m.maskMode = M.mode === "SUBTRACT" ? MaskMode.SUBTRACT : MaskMode.ADD;
        }
    }

    function createLayer(comp, spec, ctx) {
        var L = null, W = comp.width, H = comp.height;
        switch (spec.kind) {
        case "null":
            L = comp.layers.addNull();
            break;
        case "solid":
            L = comp.layers.addSolid(spec.color || [0, 0, 0], spec.name, W, H, comp.pixelAspect);
            ctx.solids.push(L.source);
            break;
        case "adjustment":
            L = comp.layers.addSolid([1, 1, 1], spec.name, W, H, comp.pixelAspect);
            L.adjustmentLayer = true;
            ctx.solids.push(L.source);
            break;
        case "text":
            L = buildText(comp, spec, ctx.fps);
            break;
        case "shape":
            L = comp.layers.addShape();
            break;
        case "video":
            L = comp.layers.add(ctx.preSource);
            L.audioEnabled = false;
            break;
        case "rgbsplit":
            L = comp.layers.add(ctx.preRGB);
            L.audioEnabled = false;
            break;
        case "audio":
            L = comp.layers.add(ctx.footage);
            L.enabled = false;
            L.audioEnabled = true;
            break;
        default:
            throw new Error("tipo de camada desconhecido: " + spec.kind);
        }
        L.name = spec.name;
        STATS.layers++;
        return L;
    }

    function configureLayer(comp, L, spec, ctx) {
        var fps = ctx.fps, W = comp.width, H = comp.height;
        safe("tempo " + spec.name, function () {
            if (spec.kind !== "audio") {
                L.inPoint = spec["in"] / fps;
                L.outPoint = spec.out / fps;
            }
        });
        if (spec.kind === "shape") { safe("conteúdo shape " + spec.name, function () { buildShapeContents(L, spec); }); }
        var tr = spec.transform || {};
        if (spec.kind === "shape" && !has(tr, "position")) { tr.position = [0, 0]; tr.anchor = [0, 0]; }
        if (spec.kind === "shape" && !has(tr, "anchor")) { tr.anchor = [0, 0]; }
        if (tr.separate_xy) {
            safe("separar XY " + spec.name, function () { L.property("ADBE Transform Group").property("ADBE Position").dimensionsSeparated = true; });
        }
        safe("transform " + spec.name, function () {
            var T = L.property("ADBE Transform Group");
            if (has(tr, "anchor")) { T.property("ADBE Anchor Point").setValue(tr.anchor); }
            if (has(tr, "position")) {
                if (tr.separate_xy) {
                    T.property("ADBE Position_0").setValue(tr.position[0]);
                    T.property("ADBE Position_1").setValue(tr.position[1]);
                } else { T.property("ADBE Position").setValue(tr.position); }
            }
            if (has(tr, "scale")) { T.property("ADBE Scale").setValue(tr.scale); }
            if (has(tr, "opacity")) { T.property("ADBE Opacity").setValue(tr.opacity); }
        });
        if (spec.kind !== "text" && spec.kind !== "audio") { addEffects(L, spec); }
        addMasks(L, spec, W, H);
        safe("flags " + spec.name, function () {
            if (spec.guide) { L.guideLayer = true; }
            if (spec.motion_blur) { L.motionBlur = true; }
            if (spec.blend === "ADD") { L.blendingMode = BlendingMode.ADD; }
            if (spec.blend === "SCREEN") { L.blendingMode = BlendingMode.SCREEN; }
            var grp = (spec.group || "").split("/")[0];
            if (has(LABEL, grp)) { L.label = LABEL[grp]; }
            L.comment = (spec.comment || "") + " | GROUP:" + (spec.group || "-");
        });
        var p;
        for (p in spec.keys) {
            if (has(spec.keys, p)) {
                (function (path, ks) {
                    safe("keys " + spec.name + "." + path, function () { applyKeys(resolve(L, path), ks, fps, spec.name + "." + path); });
                })(p, spec.keys[p]);
            }
        }
        for (p in spec.baked) {
            if (has(spec.baked, p)) {
                (function (path, b) {
                    safe("baked " + spec.name + "." + path, function () {
                        var times = [], i;
                        for (i = 0; i < b.values.length; i++) { times.push((b.start + i) / fps); }
                        resolve(L, path).setValuesAtTimes(times, b.values);
                        STATS.keys += b.values.length;
                    });
                })(p, spec.baked[p]);
            }
        }
        for (p in spec.expressions) {
            if (has(spec.expressions, p)) {
                (function (path, txt) {
                    safe("expression " + spec.name + "." + path, function () { setExpr(resolve(L, path), txt, spec.name + "." + path); });
                })(p, spec.expressions[p]);
            }
        }
        if (spec.markers && spec.markers.length) {
            safe("marcadores " + spec.name, function () {
                var mp = L.property("ADBE Marker");
                for (var i = 0; i < spec.markers.length; i++) {
                    var M = spec.markers[i], mv = new MarkerValue(M.comment);
                    if (M.duration) { mv.duration = M.duration / fps; }
                    mp.setValueAtTime(M.frame / fps, mv);
                    STATS.markers++;
                }
            });
        }
    }

    // ------------------------------------------------------------- precomps
    function buildPreSource(tl, footage, F) {
        var P = tl.precomps.PRE_SOURCE_REMAP, c = tl.comp;
        var dur = P.duration_frames / P.fps;
        var pc = app.project.items.addComp("PRE_SOURCE_REMAP", c.width, c.height, c.pixel_aspect, dur, P.fps);
        pc.parentFolder = F["04_PRECOMPS"];
        var L = pc.layers.add(footage);
        L.name = "FOOTAGE";
        L.audioEnabled = false;
        safe("ajuste de escala da fonte", function () {
            if (footage.width !== c.width || footage.height !== c.height) {
                var s = 100 * Math.max(c.width / footage.width, c.height / footage.height);
                L.property("ADBE Transform Group").property("ADBE Scale").setValue([s, s]);
                log("AVISO", "fonte " + footage.width + "x" + footage.height + " escalada " + s.toFixed(2) + "% para a comp");
            }
        });
        safe("time remap", function () {
            L.timeRemapEnabled = true;
            var tr = L.property("ADBE Time Remapping");
            while (tr.numKeys > 0) { tr.removeKey(1); }
            applyKeys(tr, P.timeremap, P.fps, "PRE_SOURCE_REMAP.timeremap");
            L.outPoint = dur;
        });
        if (P.frame_blending === "PIXEL_MOTION") {
            safe("frame blending", function () { L.frameBlendingType = FrameBlendingType.PIXEL_MOTION; pc.frameBlending = true; });
        }
        L.comment = "Time remap gerado pelo motor (rampas neutras, micro-sync, freeze). Editável no Graph Editor.";
        return pc;
    }

    function buildPreRGB(tl, pre, F) {
        var c = tl.comp;
        var pc = app.project.items.addComp("PRE_RGB_SPLIT", c.width, c.height, c.pixel_aspect, pre.duration, pre.frameRate);
        pc.parentFolder = F["04_PRECOMPS"];
        // Shift Channels: 2=Red 3=Green 4=Blue, 11=Full Off. Canais isolados somados (ADD) = imagem original.
        var chans = [["R", [2, 11, 11], -1], ["G", [11, 3, 11], 0], ["B", [11, 11, 4], 1]];
        for (var i = chans.length - 1; i >= 0; i--) {
            (function (ch) {
                safe("RGB split canal " + ch[0], function () {
                    var L = pc.layers.add(pre);
                    L.name = "CH_" + ch[0];
                    L.audioEnabled = false;
                    var fx = L.property("ADBE Effect Parade").addProperty("ADBE Shift Channels");
                    fx.property(2).setValue(ch[1][0]);
                    fx.property(3).setValue(ch[1][1]);
                    fx.property(4).setValue(ch[1][2]);
                    if (ch[0] !== "R") { L.blendingMode = BlendingMode.ADD; }
                    if (ch[2] !== 0) {
                        setExpr(L.property("ADBE Transform Group").property("ADBE Position"), CONDE_RGB_EXPR(ch[2]), "PRE_RGB_SPLIT/" + ch[0]);
                    }
                });
            })(chans[i]);
        }
        return pc;
    }

    function CONDE_RGB_EXPR(sign) {
        return "var c = comp(\"MASTER_EDIT\");\nvar px = c.layer(\"CTRL_VFX\").effect(\"RGB_SPLIT_PX\")(1) * " +
            "c.layer(\"CTRL_MASTER\").effect(\"GLOBAL_INTENSITY\")(1) * c.layer(\"CTRL_MASTER\").effect(\"VFX_INTENSITY\")(1);\n" +
            "value.length > 2 ? add(value, [" + sign + " * px, 0, 0]) : add(value, [" + sign + " * px, 0]);";
    }

    // ------------------------------------------------------------- verificação (FASE 64)
    function verify(tl, comp, footage) {
        var c = tl.comp, ok = [];
        function chk(label, cond) { ok.push((cond ? "[x] " : "[ ] ") + label); if (!cond) { log("CHECK", "FALHOU: " + label); } }
        chk("resolução " + c.width + "x" + c.height, comp.width === c.width && comp.height === c.height);
        chk("FPS " + c.fps, Math.abs(comp.frameRate - c.fps) < 0.001);
        chk("duração " + c.duration_frames + " frames", Math.abs(comp.duration * comp.frameRate - c.duration_frames) < 0.5);
        chk("nenhum asset ausente", !footage.footageMissing);
        var bad = 0;
        for (var i = 0; i < EXPR_PROPS.length; i++) {
            var pr = EXPR_PROPS[i][0];
            var err = "";
            try { err = pr.expressionError; } catch (e) { err = ""; }
            if (err && err.length) { bad++; log("EXPR", EXPR_PROPS[i][1] + " :: " + err); }
        }
        chk("nenhuma expression quebrada (" + EXPR_PROPS.length + " verificadas)", bad === 0);
        chk("motion blur da comp ligado", comp.motionBlur === true);
        var au = null;
        try { au = comp.layer("AUDIO_ORIGINAL"); } catch (e2) { au = null; }
        chk("áudio presente", au !== null && au.hasAudio);
        chk("nenhuma falha de construção", STATS.failures === 0);
        return ok;
    }

    function writeLog(path, tl, checks) {
        var f = new File(path);
        f.encoding = "UTF-8";
        f.open("w");
        f.writeln("CONDE BUILD LOG — " + tl.project.name + " v" + pad(tl.project.version, 3));
        f.writeln("After Effects " + app.version + " | " + new Date().toString());
        f.writeln("camadas=" + STATS.layers + " efeitos=" + STATS.effects + " keys=" + STATS.keys +
                  " expressions=" + STATS.expressions + " marcadores=" + STATS.markers + " falhas=" + STATS.failures);
        f.writeln("");
        f.writeln("CHECKLIST (FASE 64)");
        for (var i = 0; i < checks.length; i++) { f.writeln(checks[i]); }
        f.writeln("");
        for (i = 0; i < LOG.length; i++) { f.writeln(LOG[i]); }
        f.close();
    }

    function nextVersionFile(dir, name, v) {
        var f;
        do {
            f = new File(dir.fsName + "/" + name + "_v" + pad(v, 3) + ".aep");
            v++;
        } while (f.exists);
        return f;
    }

    // ------------------------------------------------------------- build
    function build(tl, opts) {
        opts = opts || {};
        app.beginUndoGroup("CONDE build");
        var t0 = $.hiresTimer;   // leitura zera o cronômetro do ExtendScript
        if (!app.project) { app.newProject(); }
        safe("motor de expressions JavaScript", function () { app.project.expressionEngine = "javascript-1.0"; });
        var F = {};
        for (var i = 0; i < FOLDERS.length; i++) { F[FOLDERS[i]] = folder(FOLDERS[i]); }
        var ff = findFootage(tl);
        if (!ff) { log("ERRO", "sem vídeo-fonte: build abortado"); app.endUndoGroup(); return {log: LOG, stats: STATS}; }
        var footage = app.project.importFile(new ImportOptions(ff));
        footage.parentFolder = F["01_FOOTAGE"];
        if (Math.abs(footage.frameRate - tl.source.fps) > 0.01) {
            safe("conform fps", function () { footage.mainSource.conformFrameRate = tl.source.fps; });
            log("AVISO", "fps da fonte no AE (" + footage.frameRate + ") difere da análise (" + tl.source.fps + "): conformado");
        }
        var c = tl.comp;
        var comp = app.project.items.addComp(c.name, c.width, c.height, c.pixel_aspect, c.duration_frames / c.fps, c.fps);
        comp.parentFolder = F["00_MASTER"];
        comp.motionBlur = true;
        safe("shutter", function () { comp.shutterAngle = c.shutter_angle; });
        var ctx = {fps: c.fps, footage: footage, solids: [], preSource: buildPreSource(tl, footage, F), preRGB: null};
        if (tl.precomps.PRE_RGB_SPLIT.enabled) { ctx.preRGB = buildPreRGB(tl, ctx.preSource, F); }
        // camadas: cria de baixo para cima (a lista vem do topo para a base)
        var made = {};
        for (i = tl.layers.length - 1; i >= 0; i--) {
            (function (spec) {
                var L = safe("criar " + spec.name, function () { return createLayer(comp, spec, ctx); });
                if (L) { made[spec.name] = L; }
            })(tl.layers[i]);
        }
        // parentesco antes dos valores (setParentWithJump não altera transformações)
        for (i = 0; i < tl.layers.length; i++) {
            (function (spec) {
                if (spec.parent && made[spec.name] && made[spec.parent]) {
                    safe("parent " + spec.name, function () {
                        if (made[spec.name].setParentWithJump) { made[spec.name].setParentWithJump(made[spec.parent]); }
                        else { made[spec.name].parent = made[spec.parent]; }
                    });
                }
            })(tl.layers[i]);
        }
        for (i = 0; i < tl.layers.length; i++) {
            if (made[tl.layers[i].name]) { configureLayer(comp, made[tl.layers[i].name], tl.layers[i], ctx); }
        }
        if (tl.audio.timeremap && made.AUDIO_ORIGINAL) {
            safe("time remap do áudio", function () {
                var A = made.AUDIO_ORIGINAL;
                A.timeRemapEnabled = true;
                var tr = A.property("ADBE Time Remapping");
                while (tr.numKeys > 0) { tr.removeKey(1); }
                applyKeys(tr, tl.audio.timeremap, c.fps, "AUDIO.timeremap");
                A.outPoint = c.duration_frames / c.fps;
            });
        }
        for (i = 0; i < ctx.solids.length; i++) {
            (function (s) { safe("pasta do sólido", function () { s.parentFolder = F["05_VFX"]; }); })(ctx.solids[i]);
        }
        // fila de render: final + testes dos trechos críticos (FASE 63)
        var dir = opts.saveDir ? new Folder(opts.saveDir) : new File($.fileName).parent;
        safe("render queue", function () {
            var rdir = new Folder(dir.fsName + "/renders");
            if (!rdir.exists) { rdir.create(); }
            var rq = app.project.renderQueue.items.add(comp);
            rq.outputModule(1).file = new File(rdir.fsName + "/" + tl.project.name + "_FINAL");
            var crit = tl.render_tests || [];
            for (var r = 0; r < crit.length; r++) {
                var it = app.project.renderQueue.items.add(comp);
                it.timeSpanStart = crit[r].start_frame / c.fps;
                it.timeSpanDuration = (crit[r].end_frame - crit[r].start_frame) / c.fps;
                it.outputModule(1).file = new File(rdir.fsName + "/" + tl.project.name + "_TEST_" + crit[r].id);
            }
        });
        comp.openInViewer();
        var checks = verify(tl, comp, footage);
        var out = nextVersionFile(dir, tl.project.name, tl.project.version);
        safe("salvar projeto", function () { app.project.save(out); });
        log("INFO", "build em " + ($.hiresTimer / 1e6).toFixed(1) + " s → " + out.fsName);
        writeLog(out.fsName.replace(/\.aep$/, "_BUILD_LOG.txt"), tl, checks);
        app.endUndoGroup();
        return {log: LOG, stats: STATS, checks: checks, project: out.fsName};
    }

    return {build: build, _resolve: resolve, _applyKeys: applyKeys};
})();
