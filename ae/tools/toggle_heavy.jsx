// CONDE — FASE 37: alterna o modo de preview leve (rodar DENTRO do After Effects).
// Desliga/religa efeitos caros e camadas de partículas/RGB split e muda a resolução
// de preview da MASTER_EDIT (meia ↔ cheia). Rode de novo antes do render final.
(function () {
    var HEAVY_FX = {"MOTION_TILE": 1, "FORCE_MB": 1, "DISPLACE": 1, "GLOW": 1, "LENS": 1, "NOISE": 1};
    var comp = null, i, j;
    for (i = 1; i <= app.project.numItems; i++) {
        if (app.project.item(i) instanceof CompItem && app.project.item(i).name === "MASTER_EDIT") { comp = app.project.item(i); }
    }
    if (!comp) { alert("MASTER_EDIT não encontrada."); return; }
    var draft = comp.resolutionFactor[0] === 1;   // está em qualidade cheia → entrar em modo leve
    app.beginUndoGroup("CONDE toggle heavy");
    var n = 0;
    for (i = 1; i <= comp.numLayers; i++) {
        var L = comp.layer(i);
        if (/^VFX_PARTICLES_|^VFX_RGB_SPLIT_/.test(L.name)) { L.enabled = !draft; n++; }
        var fx = L.property("ADBE Effect Parade");
        if (!fx) { continue; }
        for (j = 1; j <= fx.numProperties; j++) {
            if (HEAVY_FX[fx.property(j).name]) { fx.property(j).enabled = !draft; n++; }
        }
    }
    comp.resolutionFactor = draft ? [2, 2] : [1, 1];
    app.endUndoGroup();
    alert((draft ? "Modo LEVE ligado" : "Qualidade TOTAL restaurada") + " (" + n + " itens).");
})();
