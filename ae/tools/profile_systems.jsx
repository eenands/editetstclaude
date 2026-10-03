// CONDE — FASE 38: profiling de render por sistema (rodar DENTRO do After Effects).
// Renderiza a work area da MASTER_EDIT com tudo ligado e depois desligando um grupo
// por vez (GROUP:... no comentário de cada camada). custo(grupo) = t(tudo) - t(sem grupo).
// Resultado: PROFILE_<data>.csv ao lado do projeto. Ajuste a work area para um trecho crítico (2–4 s).
(function () {
    var comp = null, i;
    for (i = 1; i <= app.project.numItems; i++) {
        if (app.project.item(i) instanceof CompItem && app.project.item(i).name === "MASTER_EDIT") { comp = app.project.item(i); }
    }
    if (!comp) { alert("MASTER_EDIT não encontrada."); return; }
    var groups = {}, order = [];
    for (i = 1; i <= comp.numLayers; i++) {
        var m = /GROUP:([^\s|\/]+)/.exec(comp.layer(i).comment);
        if (m && m[1] !== "-" && m[1] !== "CONTROLLERS" && m[1] !== "CAMERA" && m[1] !== "AUDIO") {
            if (!groups[m[1]]) { groups[m[1]] = []; order.push(m[1]); }
            groups[m[1]].push(comp.layer(i));
        }
    }
    var rq = app.project.renderQueue, saved = [];
    for (i = 1; i <= rq.numItems; i++) { saved.push([rq.item(i), rq.item(i).render]); rq.item(i).render = false; }
    function renderOnce() {
        var it = rq.items.add(comp);
        it.timeSpanStart = comp.workAreaStart;
        it.timeSpanDuration = comp.workAreaDuration;
        it.outputModule(1).file = new File(Folder.temp.fsName + "/conde_profile_tmp");
        var t0 = $.hiresTimer;
        rq.render();
        var us = $.hiresTimer;
        it.remove();
        return us / 1e6;
    }
    var base = renderOnce(), rows = ["grupo;camadas;tempo_sem_grupo_s;custo_s;custo_pct"];
    for (var g = 0; g < order.length; g++) {
        var Ls = groups[order[g]], st = [];
        for (i = 0; i < Ls.length; i++) { st.push(Ls[i].enabled); Ls[i].enabled = false; }
        var t = renderOnce();
        for (i = 0; i < Ls.length; i++) { Ls[i].enabled = st[i]; }
        rows.push(order[g] + ";" + Ls.length + ";" + t.toFixed(2) + ";" + (base - t).toFixed(2) + ";" +
                  (100 * (base - t) / base).toFixed(1));
    }
    for (i = 0; i < saved.length; i++) { saved[i][0].render = saved[i][1]; }
    var dir = app.project.file ? app.project.file.parent : Folder.desktop;
    var f = new File(dir.fsName + "/PROFILE_" + new Date().getTime() + ".csv");
    f.open("w");
    f.writeln("tudo_ligado_s;" + base.toFixed(2));
    for (i = 0; i < rows.length; i++) { f.writeln(rows[i]); }
    f.close();
    alert("Profiling concluído: " + f.fsName + "\nTudo ligado: " + base.toFixed(2) + " s");
})();
