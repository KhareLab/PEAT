// Builds slides/designagent-codewalk.pptx.
//
//   python3 slides/run_model.py           # regenerate slides/run.json first
//   NODE_PATH=<dir with pptxgenjs> node slides/build_deck.js
//
// Every diagram is native, editable PowerPoint shapes. One visual vocabulary throughout:
//   line style = status   solid:  built, runs end to end against something real
//                         dotted: built and tested, never exercised for real
//                         dashed: planned or designed-for, not implemented
//   orange = a RADICAL component, wherever it appears.
//
// Figures F1 (loop), F2 (layers), F3 (one campaign, measured) and F4 (the lake) are drawn from
// run.json, which is mined from an actual campaign on disk. No number here is invented.
const path = require("path");
const fs = require("fs");
const pptxgen = require("pptxgenjs");

const R = JSON.parse(fs.readFileSync(path.join(__dirname, "run.json"), "utf8"));
const T1 = R.tier1, T2 = R.tier2, T3 = R.tier3.sets[0], CODE = R.code;
const CAMP = T1.campaign.id, REF = T1.reference;
// Measured by slides/bench_staging.py against the real wrap()/collect(), locally.
const STG = JSON.parse(fs.readFileSync(path.join(__dirname, "staging.json"), "utf8"));

// The concurrency figures are derived here rather than transcribed: run.json
// carries every task's submitted_at and finished_at, so the deck computes the
// profile at build time and cannot quote a stale one. Note what this is: mean
// in-flight depth, not a speedup -- a handle's lifetime includes time queued
// inside the pool, and there is no serial baseline to divide by.
const CONC = (function (tasks) {
  const ms = t => Date.parse(t);
  const rows = tasks.filter(t => t.submitted_at && t.finished_at);
  const t0 = Math.min(...rows.map(t => ms(t.submitted_at)));
  const span = (Math.max(...rows.map(t => ms(t.finished_at))) - t0) / 1000;
  const lifetimes = rows.map(t => (ms(t.finished_at) - ms(t.submitted_at)) / 1000);
  const ev = [];
  rows.forEach(t => {
    ev.push([ms(t.submitted_at), 1]);
    ev.push([ms(t.finished_at), -1]);
  });
  ev.sort((a, b) => a[0] - b[0]);
  let cur = 0, peak = 0;
  ev.forEach(([, d]) => { cur += d; if (cur > peak) peak = cur; });
  const sum = lifetimes.reduce((a, b) => a + b, 0);
  return {
    n: rows.length, span, sum, peak,
    mean: sum / span,
    longest: Math.max(...lifetimes),
  };
})(T1.tasks);

const C = {
  ink: "16222B", text: "22303C", muted: "5E6B75", rule: "C9D1D8", panel: "EEF2F5",
  white: "FFFFFF",
  agent: "3B4E8C", agentTint: "E9EDF7",      // the LangGraph loop
  task: "1E6470", taskTint: "E3F0F1",        // task interfaces
  lake: "2E7D5B", lakeTint: "E4F1EA",        // design history
  ui: "7A4A8C", uiTint: "F3EAF6",            // the web app
  radical: "D9731A", radicalTint: "FCEBDD",  // RADICAL components
  fail: "C0392B", failTint: "F9E0DD",
  good: "2E7D4F",
};
const HF = "Cambria", BF = "Calibri", MF = "Consolas";
const DASH = { built: "solid", tested: "sysDot", planned: "dash" };

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5
pres.title = "A chat agent that runs real redesign campaigns";
pres.author = "PEAT";
const W = 13.333, H = 7.5, M = 0.5;

// ---------------------------------------------------------------- helpers
function text(s, t, x, y, w, h, o = {}) {
  s.addText(t, Object.assign({ x, y, w, h, fontFace: BF, fontSize: 14, color: C.text,
    margin: 0, valign: "top", isTextBox: true }, o));
}
function title(s, t, kicker) {
  if (kicker) text(s, kicker.toUpperCase(), M, 0.35, 9, 0.3,
    { fontSize: 12, bold: true, color: C.task, charSpacing: 2 });
  text(s, t, M, 0.62, W - 2 * M, 0.8, { fontFace: HF, fontSize: 28, bold: true, color: C.ink });
}
function box(s, x, y, w, h, o = {}) {
  const status = o.status || "built";
  s.addShape(o.round === false ? pres.shapes.RECTANGLE : pres.shapes.ROUNDED_RECTANGLE, {
    x, y, w, h, rectRadius: o.round === false ? undefined : 0.08,
    fill: { color: o.fill || C.white },
    line: { color: o.line || C.muted, width: o.lw || 1.5, dashType: DASH[status] },
  });
  if (o.title || o.sub) {
    const runs = [];
    if (o.title) runs.push({ text: o.title, options: { bold: true, fontSize: o.fs || 14,
      color: o.tc || C.ink, breakLine: !!o.sub } });
    if (o.sub) runs.push({ text: o.sub, options: { fontSize: o.sfs || 10.5,
      color: o.sc || C.muted, fontFace: o.subMono ? MF : BF } });
    text(s, runs, x + 0.1, y + 0.06, w - 0.2, h - 0.12,
      { valign: o.valign || "middle", align: o.align || "center" });
  }
}
function line(s, x1, y1, x2, y2, o = {}) {
  s.addShape(pres.shapes.LINE, {
    x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.max(Math.abs(x2 - x1), 0.0001),
    h: Math.max(Math.abs(y2 - y1), 0.0001), flipH: x2 < x1, flipV: y2 < y1,
    line: { color: o.color || C.muted, width: o.w || 1.5, dashType: DASH[o.status || "built"],
      endArrowType: o.noArrow ? undefined : "triangle",
      beginArrowType: o.both ? "triangle" : undefined },
  });
}
function label(s, t, x, y, w, o = {}) {
  text(s, t, x, y, w, o.h || 0.26, Object.assign({ fontSize: 10.5, color: C.muted,
    fontFace: o.mono ? MF : BF, align: o.align || "center", valign: "middle" }, o));
}
function legend(s, x, y, withRadical = true, tw = 3.0) {
  const items = [["built, runs end to end", "built"],
                 ["built and tested, never run for real", "tested"],
                 ["designed for, not implemented", "planned"]];
  items.forEach(([t, st], i) => {
    const yy = y + i * 0.26;
    s.addShape(pres.shapes.LINE, { x, y: yy + 0.12, w: 0.45, h: 0,
      line: { color: C.text, width: 1.75, dashType: DASH[st] } });
    text(s, t, x + 0.55, yy, tw, 0.26, { fontSize: 10, color: C.muted, valign: "middle" });
  });
  if (withRadical) {
    const yy = y + 3 * 0.26;
    s.addShape(pres.shapes.RECTANGLE, { x: x + 0.1, y: yy + 0.04, w: 0.25, h: 0.17,
      fill: { color: C.radicalTint }, line: { color: C.radical, width: 1.5 } });
    text(s, "RADICAL component", x + 0.55, yy, tw, 0.26,
      { fontSize: 10, color: C.radical, bold: true, valign: "middle" });
  }
}
function pill(s, t, x, y, w, color, o = {}) {
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h: o.h || 0.3, rectRadius: 0.15,
    fill: { color: o.fill || color }, line: { color, width: 1 } });
  text(s, t, x, y, w, o.h || 0.3, { fontSize: o.fs || 10.5, bold: true,
    color: o.tc || C.white, align: "center", valign: "middle" });
}
// Code blocks size their own type to the box they were given. A fixed point size
// silently overflows the moment a snippet gains a line, and an overflowing code
// block on a projector is the one failure this deck cannot afford — so the line
// spacing is solved for, and anything that would fall below MIN_PT warns loudly
// at build time rather than shipping clipped.
const MIN_PT = 8.5;
function code(s, lines, x, y, w, h, o = {}) {
  const want = o.ls || 13;
  let ls = Math.min(want, ((h - 0.22) * 72) / lines.length);
  if (ls < MIN_PT) {
    console.warn(`  ! code block at y=${y} needs ${lines.length} lines in ${h}" ` +
      `(${ls.toFixed(1)}pt < ${MIN_PT}pt floor) — shorten it or grow the box`);
    ls = MIN_PT;
  }
  const fs = Math.min(o.fs || 10.5, ls * 0.80);
  s.addShape(pres.shapes.RECTANGLE, { x, y, w, h,
    fill: { color: o.fill || C.ink }, line: { color: o.fill || C.ink } });
  text(s, lines.join("\n"), x + 0.12, y + 0.11, w - 0.24, h - 0.22,
    { fontFace: MF, fontSize: fs, color: o.color || "DCE5EC", lineSpacing: ls });
  if (o.anchor) text(s, o.anchor, x, y + h + 0.03, w, 0.22,
    { fontFace: MF, fontSize: 8.5, color: C.muted, align: "right" });
}
function footer(s, t) {
  text(s, t, M, H - 0.42, W - 2 * M, 0.25, { fontSize: 10, color: C.muted, italic: true });
}
function card(s, x, y, w, h, heading, body, o = {}) {
  s.addShape(pres.shapes.RECTANGLE, { x, y, w, h,
    fill: { color: o.fill || C.panel }, line: { color: o.line || o.fill || C.panel } });
  text(s, heading, x + 0.15, y + 0.1, w - 0.3, 0.3,
    { fontSize: o.hfs || 13, bold: true, color: o.hc || C.ink });
  if (Array.isArray(body)) {
    text(s, body.map((t, j) => ({ text: t, options: { bullet: true, breakLine: j < body.length - 1 } })),
      x + 0.15, y + 0.45, w - 0.3, h - 0.55,
      { fontSize: o.fs || 11.5, color: C.text, paraSpaceAfter: 4 });
  } else {
    text(s, body, x + 0.15, y + 0.45, w - 0.3, h - 0.55, { fontSize: o.fs || 11.5, color: C.text });
  }
}

// A dimension divider. One repeated shape for all three acts: the dimension, the
// definition the deck is using, the three claims the act will make, and -- in red,
// before the claims are made rather than after -- the weakest of them.
function dimension(s, o) {
  s.background = { color: C.ink };
  text(s, `ACT ${o.n} OF 3`, M, 0.85, 4, 0.3,
    { fontSize: 13, bold: true, color: "8FB8C9", charSpacing: 3 });
  text(s, o.name.toUpperCase(), M, 1.2, 9.5, 0.95,
    { fontFace: HF, fontSize: 46, bold: true, color: C.white, charSpacing: 1 });
  text(s, o.definition, M, 2.3, 11.6, 0.6,
    { fontSize: 16, color: "C8D4DD", italic: true });
  o.claims.forEach(([h, b], i) => {
    const y = 3.2 + i * 0.86;
    s.addShape(pres.shapes.RECTANGLE, { x: M, y: y + 0.04, w: 0.06, h: 0.6,
      fill: { color: C.radical }, line: { color: C.radical } });
    text(s, h, M + 0.24, y, 11.4, 0.3, { fontSize: 15, bold: true, color: "F0C898" });
    text(s, b, M + 0.24, y + 0.3, 11.4, 0.32, { fontSize: 12, color: "C8D4DD" });
  });
  s.addShape(pres.shapes.RECTANGLE, { x: M, y: 6.1, w: W - 2 * M, h: 0.84,
    fill: { color: "3A2222" }, line: { color: C.fail, width: 2 } });
  text(s, [
    { text: "The weakest claim in this act:  ", options: { bold: true, color: "E8A0A0" } },
    { text: o.caveat, options: { color: C.white } },
  ], M + 0.18, 6.22, 12.0, 0.64, { fontSize: 13 });
}

// ========================================================== 1. Title
{
  const s = pres.addSlide(); s.background = { color: C.ink };

  // Motif: a chat bubble feeding a ring of five nodes, which feeds a worker pool.
  // The ring is laid out on an actual circle rather than by hand, so the five
  // nodes read as a loop instead of a scatter.
  const ccx = 10.65, ccy = 2.95, rad = 1.0, d = 0.62;
  const ringPts = [0, 1, 2, 3, 4].map(i => {
    const a = (-90 + i * 72) * Math.PI / 180;
    return [ccx + rad * Math.cos(a) - d / 2, ccy + rad * Math.sin(a) - d / 2];
  });
  // the loop itself, drawn behind the nodes
  s.addShape(pres.shapes.OVAL, { x: ccx - rad - 0.1, y: ccy - rad - 0.1,
    w: 2 * (rad + 0.1), h: 2 * (rad + 0.1),
    fill: { color: C.ink }, line: { color: "3E5168", width: 1.25, dashType: "sysDot" } });
  ringPts.forEach(([x, y], i) => {
    s.addShape(pres.shapes.OVAL, { x, y, w: d, h: d,
      fill: { color: C.agent }, line: { color: "A8B6DC", width: 1.25 } });
    text(s, ["C", "I", "O", "A", "P"][i], x, y, d, d,
      { fontFace: HF, fontSize: 14, bold: true, color: C.white,
        align: "center", valign: "middle" });
  });

  // chat bubble, feeding the coordinator at the top of the ring
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 7.95, y: 1.55, w: 1.45, h: 0.75,
    rectRadius: 0.12, fill: { color: C.ui }, line: { color: "D8BFE2", width: 1.5 } });
  text(s, "chat", 7.95, 1.55, 1.45, 0.75, { fontSize: 13, bold: true, color: C.white,
    align: "center", valign: "middle" });
  line(s, 9.45, 1.93, ccx - 0.32, ccy - rad + 0.02, { color: "D8BFE2" });

  // the pool the loop's long work lands on
  [0, 1, 2, 3].forEach(i =>
    s.addShape(pres.shapes.RECTANGLE, { x: ccx - 1.33 + i * 0.68, y: 4.85, w: 0.56, h: 0.4,
      fill: { color: C.radical }, line: { color: C.radical } }));
  line(s, ccx, ccy + rad + 0.22, ccx, 4.82, { color: "E8B98A" });
  text(s, "rhapsody process pool", ccx - 1.75, 5.33, 3.5, 0.26,
    { fontSize: 10, color: "E8B98A", align: "center" });

  text(s, "PEAT", M + 0.3, 1.5, 7.5, 0.4,
    { fontSize: 14, bold: true, color: "8FB8C9", charSpacing: 3 });
  text(s, "A chat agent that runs real redesign campaigns", M + 0.3, 1.95, 7.1, 1.35,
    { fontFace: HF, fontSize: 34, bold: true, color: C.white });
  text(s, "A LangGraph loop whose long work leaves the process — and a chat that keeps talking while it does",
    M + 0.3, 3.45, 7.1, 0.9, { fontSize: 17, color: "D5DEE5" });
  text(s, `Worked example, measured: ${REF.pdb_id} (${REF.uniprot_id}, ${REF.length} aa) · ` +
    `${T2.rounds.length} rounds · ${T3.n_rows} designs · pLDDT ` +
    `${T2.rounds[0].best_value} → ${T2.rounds[1].best_value}`,
    M + 0.3, 4.55, 7.1, 0.6, { fontSize: 13, color: "9FB0BD" });
  [["FUNCTIONALITY", "the new tasks it makes possible"],
   ["PERFORMANCE", "what it costs to run, and what we never measured"],
   ["USABILITY", "what it is usable as"]].forEach(([h, b], i) => {
    const y = 5.25 + i * 0.36;
    s.addShape(pres.shapes.RECTANGLE, { x: M + 0.3, y: y + 0.05, w: 0.05, h: 0.24,
      fill: { color: C.radical }, line: { color: C.radical } });
    text(s, [{ text: `${h}  `, options: { bold: true, color: "F0C898" } },
             { text: b, options: { color: "9FB0BD" } }],
      M + 0.48, y, 7.2, 0.3, { fontSize: 11.5 });
  });
  text(s, `Lab code walk · main @ 7e72b74 · ~${(CODE.backend_total / 1000).toFixed(1)}k lines backend, ` +
    `${CODE.tests} lines of tests`, M + 0.3, 6.4, 7.6, 0.3, { fontSize: 12, color: "7E8F9C" });
  s.addNotes(
`[0:25] A code walk, not a results talk. What I built is a chatbot for protein redesign: you type a prompt, and behind it a LangGraph loop runs an actual campaign — structure lookups, folds, scoring, a provenance lake, artifacts you can open.

What is worth your time is the seam in the middle, where long work leaves this process: onto a rhapsody pool through flowgentic and asyncflow, or off the box entirely through ORBIT.

Every number on these slides comes from a real campaign still on disk, and the deck is in three acts — the three things I would want to know about anyone else's software: what new tasks it makes possible, what it costs to run, and what it is usable as.`);
}

// =================================================== 2. What it does
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "One prompt, a whole campaign", "Functionality · the claim, measured");

  const steps = [
    ["prompt", `"redesign ${REF.pdb_id} for\nhigher stability"`, C.ui],
    ["resolve", `RCSB + UniProt +\nEuropePMC, concurrent`, C.task],
    ["propose", `6 single-point\nvariants per round`, C.agent],
    ["fold", `6 ESMFold calls,\nin parallel`, C.radical],
    ["score", `pLDDT, RMSD to the\ncrystal structure`, C.agent],
    ["record", `3 lake tiers +\n4 artifacts`, C.lake],
  ];
  const sw = 1.92, gap = 0.12, sx = M;
  steps.forEach(([h, b, col], i) => {
    const x = sx + i * (sw + gap);
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: 1.6, w: sw, h: 1.3, rectRadius: 0.08,
      fill: { color: C.white }, line: { color: col, width: 2 } });
    text(s, h, x, 1.68, sw, 0.3, { fontSize: 12, bold: true, color: col, align: "center" });
    text(s, b, x + 0.08, 2.0, sw - 0.16, 0.8, { fontSize: 10.5, color: C.text, align: "center" });
    if (i < steps.length - 1) line(s, x + sw + 0.01, 2.25, x + sw + gap - 0.01, 2.25, { w: 1.25 });
  });

  card(s, M, 3.15, 6.2, 2.0, `What came back, for real (campaign ${CAMP})`, [
    `${REF.pdb_id} — ${REF.uniprot_id}, Burkholderia cepacia lipase, ${REF.length} residues, resolved from the prompt alone`,
    `lead design G147A at pLDDT ${T2.rounds[1].best_value}, RMSD ${T2.by_metric.rmsd_to_reference.find(d => d.design_id === CAMP + "-r2-2").value.toFixed(2)} Å to the crystal structure`,
    `${T3.n_rows} designs scored on ${T3.metrics.length} metrics; ${T2.counts.scores} score rows written`,
    `a Markdown summary, a .docx, a sortable ensemble table, and a Mol* view of the lead`,
  ], { fill: C.panel });

  card(s, M + 6.5, 3.15, 6.33, 2.0, "And what that is not", [
    "The variants are heuristic single-point proposals, not ProteinMPNN samples — no HPC endpoint was attached.",
    `A pLDDT move from ${T2.rounds[0].best_value} to ${T2.rounds[1].best_value} is inside the noise. The loop is real; the science is a demo.`,
    "This run had no ANTHROPIC_API_KEY. Every node took its deterministic path.",
  ], { fill: C.failTint, hc: C.fail });

  text(s, [
    { text: "The point of the demo: ", options: { bold: true, color: C.ink } },
    { text: "a prompt in a browser produced a provenance graph, a scored ensemble and a " +
      "training-ready Parquet set, with no human in the middle and nothing stubbed." },
  ], M, 5.4, 12.3, 0.6, { fontSize: 13 });
  footer(s, "Numbers read from slides/run.json, mined from data/lake by slides/run_model.py.");
  s.addNotes(
`[0:50] Here is the shape of one turn, and then what actually came out of it.

Left card, all measured: the prompt named 1OIL and nothing else. The agent resolved it to UniProt P22088, a Burkholderia cepacia lipase, 320 residues, pulled the crystal structure and the literature, proposed variants, folded them, scored them against the real structure, and wrote 114 score rows.

Right card, because you will ask and I would rather say it first. The variants are heuristic proposals — glycine to alanine, that kind of thing — not ProteinMPNN samples, because no HPC endpoint was attached to this run. The pLDDT improvement is inside the noise. And there was no API key, so every node ran its rule-based path.

So treat the science as a demo and the plumbing as the deliverable. The plumbing is what the rest of the talk is about.`);
}

// =========================================== 3. The three dimensions
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "Three questions this deck is organised around", "How to read the next 20 slides");

  const dims = [
    ["FUNCTIONALITY", C.agent, C.agentTint,
     "The specific new tasks the software makes possible.",
     ["A chat turn plans, runs and records a redesign campaign",
      `${T1.nodes.Task} tasks, ${T1.nodes.Design} designs, ${T2.counts.scores} scores in one session`,
      "A nine-stage cluster protocol, advanced one stage per turn"]],
    ["PERFORMANCE", C.radical, C.radicalTint,
     "Runtime efficiency and effectiveness \u2014 including where scale and scientific yield are not linear in each other.",
     ["The chat answers in milliseconds while the work takes minutes",
      `mean ${CONC.mean.toFixed(2)} tasks in flight, peak ${CONC.peak}, over ${CONC.span.toFixed(0)} s`,
      "One measured curve: what a file costs to cross the broker"]],
    ["USABILITY", C.ui, C.uiTint,
     "Not the interface \u2014 the use cases the software lends itself to.",
     ["As an application: a chat with a structure viewer",
      "As a platform: per-user keys, 22 HTTP routes, a CLI",
      "As a component: one contract, four interfaces, a readable lake"]],
  ];
  dims.forEach(([name, col, tint, def, rows], i) => {
    const x = M + i * 4.28;
    s.addShape(pres.shapes.RECTANGLE, { x, y: 1.5, w: 4.0, h: 0.42,
      fill: { color: col }, line: { color: col } });
    text(s, name, x + 0.14, 1.5, 3.8, 0.42,
      { fontSize: 13.5, bold: true, color: C.white, valign: "middle", charSpacing: 1 });
    s.addShape(pres.shapes.RECTANGLE, { x, y: 1.92, w: 4.0, h: 1.0,
      fill: { color: tint }, line: { color: tint } });
    text(s, def, x + 0.14, 1.99, 3.72, 0.9, { fontSize: 11.5, color: C.text, italic: true });
    rows.forEach((r, j) => {
      const y = 3.06 + j * 0.78;
      s.addShape(pres.shapes.RECTANGLE, { x, y, w: 4.0, h: 0.68,
        fill: { color: j % 2 ? C.white : C.panel }, line: { color: C.rule, width: 0.5 } });
      text(s, r, x + 0.14, y + 0.06, 3.72, 0.56, { fontSize: 11, color: C.text });
    });
    label(s, i === 1 ? "what it answers \u00b7 and what it does not" : "what it answers",
      x + 0.02, 5.46, 4.0);
  });

  s.addShape(pres.shapes.RECTANGLE, { x: M, y: 5.78, w: W - 2 * M, h: 1.05,
    fill: { color: C.ink }, line: { color: C.ink } });
  text(s, [
    { text: "The middle column is the weak one, and I would rather name it here than defend it later.  ",
      options: { bold: true, color: "F0C898" } },
    { text: "One host, one process pool of four, one campaign, no GPU of our own. " +
      "There is no scaling study in this deck, mean in-flight depth is not a speedup, and the " +
      "act on performance therefore ends with a slide listing the measurements that do not exist \u2014 " +
      "because this room can tell the difference between a measurement and an architecture diagram.",
      options: { color: "C8D4DD" } },
  ], M + 0.2, 5.88, 11.9, 0.9, { fontSize: 12.5 });

  s.addNotes(
`[0:50] The frame, in three questions, because they are the ones you would ask about any piece of scientific software and a deck organised by module answers none of them directly.

Functionality: what new tasks does it make possible. Performance: runtime efficiency and effectiveness — usually scalability and occupancy, but also scientific performance, and the interesting cases are where scale and scientific yield are not linear in each other. This system has one of those. Usability, and I do not mean the user interface: the use cases. What it lends itself to as an application, as a platform other people's credentials plug into, or as a component in someone else's workflow.

And the honest part up front: the middle column is the weak one. One host, a pool of four, one campaign, no GPU of our own. So that act ends with a slide about the measurements that do not exist, and the concurrency number I am about to quote is a mean depth, not a speedup. You can tell the difference, which is why I am saying it now rather than when someone asks.`);
}

// ================================================= 4. The constraint
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "One constraint sets the whole design", "The performance requirement that set everything");

  s.addShape(pres.shapes.RECTANGLE, { x: M, y: 1.55, w: 12.33, h: 0.9,
    fill: { color: C.ink }, line: { color: C.ink } });
  text(s, [
    { text: "A chat interface must answer in milliseconds. ", options: { bold: true, color: "F0C898" } },
    { text: "A protein fold takes tens of seconds. An HPC job takes hours, and may sit in a queue first.",
      options: { color: "DCE5EC" } },
  ], M + 0.25, 1.72, 11.8, 0.6, { fontSize: 17, valign: "middle" });

  const rows = [
    ["Tasks return futures, never results", "Every interface's submit() places the work and returns a handle. Nothing in the graph awaits a task at submission time.", "tasks/base.py:166"],
    ["Task duration is not the interface's business", "The same handle covers a 40 ms REST call and a queued batch job. Only the capability flags differ.", "tasks/base.py:62"],
    ["Status is a stream, not a return value", "Nodes emit progress over LangGraph's custom channel; the manager fans task events onto the same SSE stream.", "graph/deps.py · app.py:437"],
    ["State holds references, not payloads", "Coordinates go to a content-addressed blob and travel as a path, because every checkpoint is serialized.", "orchestrator.py:292"],
    ["Every layer has a floor", "No key, no endpoint, no graph DB: each degrades to something that still answers, and says so.", "runtime.py · analyst.py:166"],
  ];
  const cw = [3.45, 6.6, 2.28];
  const tbl = [["consequence", "what it means in the code", "where"].map(h => ({ text: h,
    options: { bold: true, color: C.white, fill: { color: C.task }, fontSize: 12 } }))]
    .concat(rows.map((r, i) => r.map((c, j) => ({ text: c, options: {
      fontFace: j === 2 ? MF : BF, fontSize: j === 2 ? 10 : 12, bold: j === 0,
      color: j === 2 ? C.muted : C.text,
      fill: { color: i % 2 ? C.white : C.panel } } }))));
  s.addTable(tbl, { x: M, y: 2.7, w: cw.reduce((a, b) => a + b), colW: cw,
    border: { type: "solid", color: C.rule, pt: 0.75 }, fontFace: BF, valign: "middle",
    rowH: [0.32, 0.5, 0.58, 0.62, 0.58, 0.5], margin: 0.08 });

  text(s, [
    { text: "Nothing below this slide is a free choice. ", options: { bold: true, color: C.ink } },
    { text: "If you drop the constraint — if the agent may block — most of the Task Interface layer " +
      "stops paying for itself and you would call the tools directly." },
  ], M, 6.4, 12.3, 0.6, { fontSize: 13 });
  s.addNotes(
`[1:05] If you remember one slide, this is the one, because everything after it is a consequence rather than a preference.

A chat interface has to answer in milliseconds. The work it triggers takes tens of seconds at best and hours at worst, and it might sit in a queue before it even starts. That gap is the entire design problem.

Five things fall out. Tasks return futures, not results — submit places work and comes back immediately. Duration stops being the interface's concern, so a forty-millisecond REST call and a queued batch job wear the same handle and differ only in capability flags. Status becomes a stream rather than a return value. State holds references rather than payloads, because LangGraph serializes every checkpoint. And every layer has a floor it degrades to.

The honest flip side is at the bottom: if the agent were allowed to block, you would not build most of this. You would call the tools inline and go home.`);
}

// ============================================== 5. Architecture (F2)
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "Architecture, and where the RADICAL stack sits", "Architecture · F2");

  const lx = M, lw = 1.2, cx = lx + lw + 0.1, cw = 7.45;
  const bands = [
    ["BROWSER", 1.5, 0.52, C.ui],
    ["API", 2.1, 0.52, C.ui],
    ["GRAPH", 2.72, 0.72, C.agent],
    ["DEPS", 3.56, 0.4, C.agent],
    ["INTERFACES", 4.08, 0.72, C.task],
    ["SUBSTRATE", 4.92, 0.62, C.radical],
    ["REMOTE", 5.66, 0.62, C.radical],
    ["HISTORY", 6.4, 0.6, C.lake],
  ];
  bands.forEach(([t, y, h, col]) => {
    s.addShape(pres.shapes.RECTANGLE, { x: lx, y, w: lw, h,
      fill: { color: col }, line: { color: col } });
    text(s, t, lx, y, lw, h, { fontSize: 9.5, bold: true, color: C.white,
      align: "center", valign: "middle", charSpacing: 1 });
  });

  // band contents
  box(s, cx, 1.5, cw, 0.52, { title: "React + Vite", sub: "   ChatPane · Composer · TaskChips · ArtifactPane (Mol* · Markdown · docx · table)",
    line: C.ui, fill: C.uiTint, fs: 12, sfs: 10, align: "left", valign: "middle", round: false });
  box(s, cx, 2.1, cw, 0.52, { title: "FastAPI + SSE", sub: "   app.py — /api/chat streams 7 frame kinds · artifacts · tasks · health · catalog",
    line: C.ui, fill: C.uiTint, fs: 12, sfs: 10, align: "left", valign: "middle", round: false });
  box(s, cx, 2.72, cw, 0.72, { title: "LangGraph StateGraph", sub: "   coordinator · initializer · orchestrator · analyst · interpreter   —   AsyncSqliteSaver checkpoints",
    line: C.agent, fill: C.agentTint, fs: 12, sfs: 10, align: "left", valign: "middle", round: false });
  box(s, cx, 3.56, cw, 0.4, { title: "Deps(settings, tasks, history, artifacts)",
    sub: "   the only thing a node closes over", line: C.agent, fill: C.white, fs: 11, sfs: 10,
    align: "left", valign: "middle", round: false });

  // interfaces row
  const iw = (cw - 0.24) / 3;
  [["LocalTaskInterface", "app-local compute"], ["QueryTaskInterface", "REST, on the loop"],
   ["RemoteWorkflow (ABC)", "Orbit · Globus"]].forEach(([t, sub], i) =>
    box(s, cx + i * (iw + 0.12), 4.08, iw, 0.72, { title: t, sub: "\n" + sub,
      line: C.task, fill: C.taskTint, fs: 11, sfs: 9.5 }));

  // substrate (RADICAL)
  box(s, cx, 4.92, cw * 0.62, 0.62, { title: "flowgentic  →  radical.asyncflow  →  rhapsody",
    sub: "\nConcurrentExecutionBackend(ProcessPoolExecutor(4))",
    line: C.radical, fill: C.radicalTint, fs: 11.5, sfs: 9.5, tc: C.radical, subMono: true });
  box(s, cx + cw * 0.63, 4.92, cw * 0.37 - 0.01, 0.62, { title: "ChemGraph",
    sub: "\noptional extra", line: C.muted, fill: C.white, status: "tested", fs: 11.5, sfs: 9.5 });

  box(s, cx, 5.66, cw * 0.48, 0.62, { title: "RADICAL Orbit", sub: "\nRhapsodyClient + PSIJClient",
    line: C.radical, fill: C.radicalTint, fs: 11.5, sfs: 9.5, tc: C.radical });
  box(s, cx + cw * 0.5, 5.66, cw * 0.5, 0.62, { title: "Globus Compute / hpc-bridge",
    sub: "\nGlobusRunner, injected executor", line: C.muted, fill: C.white, status: "planned",
    fs: 11.5, sfs: 9.5 });

  // history
  const hw = (cw - 0.24) / 3;
  [["Tier 1 · Kuzu", "raw task outputs"], ["Tier 2 · SQLite", "scores · rankings"],
   ["Tier 3 · Parquet", "golden sets"]].forEach(([t, sub], i) =>
    box(s, cx + i * (hw + 0.12), 6.4, hw, 0.6, { title: t, sub: "\n" + sub,
      line: C.lake, fill: C.lakeTint, fs: 11, sfs: 9.5 }));

  // right rail
  const rx = cx + cw + 0.25, rwd = W - M - rx;
  card(s, rx, 1.5, rwd, 2.2, "The one rule", [
    "A node reaches the outside only through Deps — never an interface or a store directly.",
    "That is what makes 417 of 441 tests run with no network, no pool and no endpoint.",
    "Convention, not an enforced check.",
  ], { fill: C.agentTint, hc: C.agent, fs: 10.5 });
  card(s, rx, 3.85, rwd, 1.5, "Where it leaves the process", [
    "LocalTaskInterface is the only band that crosses into asyncflow.",
    "Everything above it is ordinary async Python, and stubbable.",
  ], { fill: C.radicalTint, hc: C.radical, fs: 10.5 });
  legend(s, rx + 0.05, 5.55, true, rwd - 0.7);
  s.addNotes(
`[1:15] Eight bands. Read it top to bottom and the orange is yours.

Browser, then FastAPI with a single SSE chat endpoint. Then the LangGraph graph, checkpointed to SQLite. Then a small Deps object, which is the only thing a node closes over — settings, the task manager, the history lake, the artifact store.

Then the three task interfaces, then the substrate — flowgentic over asyncflow over a rhapsody concurrent backend on a four-worker pool — then remote: ORBIT solid, because since the ninth of October it has run jobs on a real cluster; Globus dashed, designed for and never run against anything live. Then the three lake tiers.

Two things in the rail. The rule that nodes reach out only through Deps is what makes 417 of the 441 tests run with no network and no pool. And exactly one band crosses into asyncflow: the local task interface. Everything above it is ordinary async Python, deliberately, so the middleware dependency stays in a layer I can stub.`);
}

// ======================================== 6. FUNCTIONALITY — divider
{
  const s = pres.addSlide();
  dimension(s, {
    n: 1, name: "Functionality",
    definition: "The specific new tasks a piece of software makes possible \u2014 what you can do now that you could not before.",
    claims: [
      ["One prompt runs a redesign campaign end to end",
       `classify \u2192 initialize \u2192 propose \u2192 fold \u2192 score \u2192 rank \u2192 summarize, every step recorded: ${T1.nodes.Task} tasks, ${T1.nodes.Design} designs, ${T2.counts.scores} scores, ${R.artifacts.n} artifacts in the session on the next slide`],
      ["A multi-day cluster protocol becomes a conversation",
       "nine stages, one per turn, three of them stopping for a human \u2014 and the turn boundary is the checkpoint, because there is no interrupt() anywhere in this repo"],
      ["A campaign becomes a record the next campaign can query",
       "three tiers behind one facade; best_designs(exclude_campaign=\u2026) is how the interpreter finds prior art without rediscovering its own designs"],
    ],
    caveat: "the protocol's nine stages have never run on a cluster. 203 offline tests, zero real runs \u2014 so every resource figure in protocol/specs.py is an estimate.",
  });
  s.addNotes(
`[0:20] Act one, functionality: the new tasks this makes possible. Three claims — a prompt that runs a campaign, a multi-day cluster protocol turned into a conversation, and a campaign that becomes a queryable record. The weakest of the three is the second one: the protocol has two hundred and three tests and has never run on a cluster.`);
}

// ================================================== 7. The loop (F1)
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "Six nodes, and the nodes do their own routing", "Functionality · the loop · F1");
  text(s, "F1 is the campaign loop. The sixth node, protocol, answers in place and goes straight to END \u2014 it is slide 10.",
    M, 1.46, 12.3, 0.24, { fontSize: 10.5, color: C.muted, italic: true });

  // F1: coordinator on the left, the three working nodes across, interpreter right.
  // The per-edge goto labels were dropped: the gaps are too narrow to hold them and
  // the destinations block below already names every target.
  const ny = 1.95, nh = 1.3, nw = 2.1, step = 2.62;
  const nodes = [
    ["Coordinator", "classify · answer\nfrom state", M, ny + 1.5, C.agent],
    ["Design Initializer", "PDB · UniProt\nliterature", M + step, ny, C.task],
    ["Redesign Orchestrator", "key_metric\nworklist", M + 2 * step, ny, C.task],
    ["Analyst", "score · persist\nrank · visualize", M + 3 * step, ny, C.lake],
    ["Interpreter", "summarize\nprior art", M + 4 * step, ny, C.ui],
  ];
  nodes.forEach(([t, sub, x, y, col]) =>
    box(s, x, y, nw, nh, { title: t, sub: "\n" + sub, line: col, fs: 12.5, sfs: 9.5,
      fill: C.white, lw: 2 }));

  // forward edges, drawn in the gaps
  const mid = ny + nh / 2;
  line(s, M + nw, ny + 1.5 + nh / 2, M + step, mid + 0.35, { color: C.agent });
  [1, 2, 3].forEach(i =>
    line(s, M + i * step + nw, mid, M + (i + 1) * step, mid, { color: C.muted }));

  // the back edge: analyst -> orchestrator, the only cycle
  const by = ny + nh + 0.42;
  line(s, M + 3 * step + nw / 2, ny + nh, M + 3 * step + nw / 2, by, { color: C.fail, w: 2, noArrow: true });
  line(s, M + 3 * step + nw / 2, by, M + 2 * step + nw / 2, by, { color: C.fail, w: 2, noArrow: true });
  line(s, M + 2 * step + nw / 2, by, M + 2 * step + nw / 2, ny + nh, { color: C.fail, w: 2 });
  label(s, "improved  AND  round < max_rounds", M + 2 * step - 0.1, by + 0.04, nw + step + 0.2,
    { fs: 10.5, color: C.fail, bold: true });

  // coordinator fan-out
  const c1 = by + 0.75, c2 = by + 1.18;
  line(s, M + nw / 2, ny + 1.5 + nh, M + nw / 2, c2, { color: C.agent, noArrow: true });
  line(s, M + nw, c1, M + 2 * step + nw / 2 - 0.35, c1, { color: C.agent, noArrow: true });
  line(s, M + 2 * step + nw / 2 - 0.35, c1, M + 2 * step + nw / 2 - 0.35, ny + nh + 0.02, { color: C.agent });
  label(s, "a reference already loaded skips the initializer", M + nw + 0.25, c1 - 0.3, 4.4,
    { fs: 10, color: C.agent, align: "left" });

  box(s, M + 4 * step + nw - 1.35, c2 - 0.22, 1.35, 0.44,
    { title: "END", fs: 12, line: C.muted, fill: C.panel });
  line(s, M + 4 * step + nw / 2, ny + nh, M + 4 * step + nw / 2, c2 - 0.22, { color: C.ui });
  line(s, M + nw / 2, c2, M + 4 * step + nw - 1.37, c2, { color: C.agent });
  label(s, "plain chat, or a question state can already answer", M + nw + 0.25, c2 - 0.3, 4.6,
    { fs: 10, color: C.agent, align: "left" });

  code(s, [
    'destinations = {',
    '  "coordinator":  ("initializer", "orchestrator", "analyst",',
    '                   "interpreter", "protocol", END),',
    '  "initializer":  ("orchestrator", END),',
    '  "orchestrator": ("analyst", "interpreter", END),',
    '  "analyst":      ("orchestrator", "interpreter", END),',
    '  "interpreter":  (END,),',
    '  "protocol":     (END,),',
    '}',
    'builder.add_edge(START, "coordinator")   # the only static edge',
  ], M, 5.5, 7.4, 1.45, { anchor: "graph/build.py:147–158  ·  VERBATIM, two comment lines elided", fs: 10.5 });

  card(s, M + 7.7, 5.5, 5.13, 1.45, "Why no conditional edges", [
    "A node returns Command(goto=…, update=…): the decision and the state write are one atomic return.",
    "destinations= is a declaration for validation and the drawn graph, not control flow.",
  ], { fill: C.agentTint, hc: C.agent, fs: 11 });
  s.addNotes(
`[1:55] Five nodes in the campaign loop, matching the spec: coordinator, design initializer, redesign orchestrator, analyst, interpreter. There is a sixth in the graph, the protocol node, and it is its own slide later — it answers the user and goes straight to END, so drawing it here would add a box and no information.

The coordinator is the only entry and the only re-entry point. It classifies the prompt and, where it can, answers straight from state without waking anything up — "what is the lead design?" costs one node visit.

The interesting edge is the red one. The analyst decides whether to go round again, and the test is whether the key metric actually improved, bounded by a round budget. In the measured campaign that fired once: round one improved on nothing, round two improved on round one, and then the budget and the interpreter took over.

Note how little static wiring there is. One static edge, START to coordinator. Everything else is a Command with a goto, which means the routing decision and the state write are the same atomic return — a node cannot update state and then fail to say where it went. The destinations tuple is a declaration so LangGraph can validate and draw the graph; it is not control flow.`);
}

// ========================================================== 8. State
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "State, its reducers, and what must never enter it", "Functionality · state");

  code(s, [
    'class DesignState(TypedDict, total=False):',
    '    messages: Annotated[list, add_messages]',
    '',
    '    reference_design:        Annotated[ReferenceDesign, replace]',
    '    key_metric:             Annotated[KeyMetric, replace]',
    '    worklist:               Annotated[list[WorkItem], merge_worklist]',
    '    lead_design:            Annotated[Design, replace]',
    '    ensemble:               Annotated[list[Design], replace]',
    '    molecular_visualization: Annotated[MolVisualization, replace]',
    '    artifacts:              Annotated[list[ArtifactRef], merge_artifacts]',
    '    design_summary:         Annotated[str, replace]',
    '',
    '    # control',
    '    intent · round · session_id · goal · status',
    '    warnings:        Annotated[list[str], merge_warnings]',
    '    target_hints · requested_mutations · pending_results',
  ], M, 1.55, 7.0, 2.55, { anchor: "graph/state.py:211–240  ·  TRIMMED", fs: 10 });

  card(s, M, 4.25, 7.0, 1.3, "Four reducers, each for a reason", [
    "merge_artifacts — append, dedupe by id, last write wins: the analyst replaces a viz each round.",
    "merge_worklist — upsert by id, so status can be updated in place.",
    "merge_warnings — accumulate, dedupe, keep 20. Survives the turn; status does not.",
  ], { fill: C.agentTint, hc: C.agent, fs: 10.5 });

  s.addShape(pres.shapes.RECTANGLE, { x: M + 7.3, y: 1.55, w: 5.53, h: 0.42,
    fill: { color: C.fail }, line: { color: C.fail } });
  text(s, "The finding: a checkpoint is serialized on every turn", M + 7.4, 1.55, 5.4, 0.42,
    { fontSize: 13, bold: true, color: C.white, valign: "middle" });
  text(s, [
    { text: "First build put fold coordinates in ", options: {} },
    { text: "pending_results", options: { fontFace: MF } },
    { text: ". Six PDB files is ~320 KB of checkpoint per turn, and it grows with the ensemble. " +
      "The orchestrator now writes coordinates to a content-addressed blob and passes a path:",
      options: {} },
  ], M + 7.3, 2.1, 5.53, 1.0, { fontSize: 12 });
  code(s, [
    'summary["structure_path"] = deps.history.write_blob(',
    '    structure, suffix=".pdb", prefix=summary["design_id"])',
  ], M + 7.3, 3.15, 5.53, 0.6, { anchor: "orchestrator.py:292–296  ·  TRIMMED", fs: 10 });
  code(s, [
    'def test_structures_are_not_carried_in_state(...):',
    '    blob = json.dumps(jsonable(out), default=str)',
    '    assert "ATOM  " not in blob',
  ], M + 7.3, 4.1, 5.53, 0.75, { anchor: "tests/test_graph.py  ·  TRIMMED", fs: 10, fill: "243240" });
  text(s, "Pinned by a test, because the regression is invisible: everything still works, " +
    "just slower every turn.", M + 7.3, 5.0, 5.53, 0.55, { fontSize: 11.5, italic: true, color: C.muted });

  text(s, [
    { text: "Reducers are the only place concurrency shows up in state. ", options: { bold: true, color: C.ink } },
    { text: "Nodes run sequentially here, but artifacts and warnings arrive from different " +
      "nodes in the same turn, and both must accumulate rather than clobber." },
  ], M, 5.8, 12.3, 0.6, { fontSize: 13 });
  s.addNotes(
`[1:20] State is a TypedDict, not a pydantic model, because LangGraph checkpoints it and partial dict updates are the natural write unit from a node.

The eight keys from the spec are all there, each with an explicit reducer. Most are "replace" — stated explicitly rather than left to default, so the intent is readable. Three are not: artifacts append and dedupe by id, the worklist upserts so status can change in place, and warnings accumulate because they have to outlive the single turn that status lives for.

The right-hand side is a finding worth your time. I originally passed fold results through state. Six PDB files is about 320 kilobytes of checkpoint per turn, and it grows with the ensemble, so turn ten is carrying turn one's coordinates. The fix is that the orchestrator writes coordinates to a content-addressed blob and state carries a path.

What makes that a real lesson rather than a tidy-up is that nothing breaks when you get it wrong. It just gets slower every turn, forever. So it is pinned by a test that serializes the state and asserts the string "ATOM" never appears in it.`);
}

// ================================================== 9. The lake (F4)
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "Design History: three tiers, and what is actually in them", "Functionality · the record · F4");

  const tiers = [
    ["Tier 1 — raw task outputs", "Kuzu, embedded graph DB", [
      `${T1.n_node_tables} node tables · ${T1.n_rel_tables} rel tables`,
      `Campaign ${T1.nodes.Campaign} · Reference ${T1.nodes.Reference} · Design ${T1.nodes.Design}`,
      `Task ${T1.nodes.Task} · Output ${T1.nodes.Output} · Structure ${T1.nodes.Structure}`,
      `properties columns hold JSON, so a new task needs no migration`,
      `single writer behind an RLock`,
    ]],
    ["Tier 2 — scores, rankings, analysis", "SQLite", [
      `${T2.counts.scores} score rows over ${T2.n_metrics} metrics`,
      `${T2.counts.rankings} ranking rows (round 1: 6, round 2: 11 cumulative)`,
      `${T2.counts.analyses} round analyses, with the failures list`,
      `best_designs() can exclude the current campaign — that is how the`,
      `interpreter finds prior art without finding itself`,
    ]],
    ["Tier 3 — golden sets", "Parquet + manifest", [
      `${T3.n_rows} rows, one metric_<name> column per metric seen`,
      `rules: metric=${T3.rules.metric}, direction=${T3.rules.direction}, top_k=${T3.rules.top_k},`,
      `dedupe_sequences=${T3.rules.dedupe_sequences} → 12 designs became ${T3.n_rows} rows`,
      `the manifest records the rules, so a set is reproducible`,
      `staged for ML training, not for reading`,
    ]],
  ];
  const tw = 4.04;
  tiers.forEach(([h, sub, items], i) => {
    const x = M + i * (tw + 0.1);
    s.addShape(pres.shapes.RECTANGLE, { x, y: 1.5, w: tw, h: 0.62,
      fill: { color: C.lake }, line: { color: C.lake } });
    text(s, h, x + 0.12, 1.54, tw - 0.24, 0.32,
      { fontSize: 12.5, bold: true, color: C.white });
    text(s, sub, x + 0.12, 1.84, tw - 0.24, 0.24, { fontSize: 10, color: "BFE0CF", fontFace: MF });
    s.addShape(pres.shapes.RECTANGLE, { x, y: 2.12, w: tw, h: 1.75,
      fill: { color: C.lakeTint }, line: { color: C.lakeTint } });
    text(s, items.map((t, j) => ({ text: t, options: { bullet: true, breakLine: j < items.length - 1 } })),
      x + 0.15, 2.22, tw - 0.3, 1.6, { fontSize: 10, color: C.text, paraSpaceAfter: 3 });
  });

  code(s, [
    'def record_task_result(self, campaign_id, task_id, *, name, interface, state,',
    '                      result=None, error="", designs=None, round_no=0,',
    '                      reference_id=None, blob=None) -> dict:',
    '    """Write one task\'s outcome across tiers 1 and 2.',
    '',
    '    `blob` is an optional (data, suffix) pair for bulky raw output.',
    '    Returns {"output_id", "blob_path"} for the caller to reference.',
    '    """',
  ], M, 4.05, 7.5, 1.5, { anchor: "lake/store.py:89–108  ·  TRIMMED", fs: 10 });

  card(s, M + 7.8, 4.05, 5.03, 1.5, "One facade, three stores", [
    "DesignHistory is the only thing nodes see; tiers 1 and 2 are written together or not at all.",
    `Bulky output goes to a content-addressed blob (sha256[:16]) — ${R.blobs.n} files, ${(R.blobs.bytes / 1e6).toFixed(1)} MB on disk.`,
  ], { fill: C.lakeTint, hc: C.lake, fs: 10.5 });

  text(s, [
    { text: "Worth noticing: ", options: { bold: true, color: C.ink } },
    { text: "only tier 1 needs a running process to read. Tiers 2 and 3 are a SQLite file and a " +
      "Parquet file, so the figures on this slide were produced by a script that opened them " +
      "directly while the server held the Kuzu lock." },
  ], M, 5.7, 12.3, 0.65, { fontSize: 12.5 });
  footer(s, "Every count read from slides/run.json by slides/run_model.py — the same script that drew F3.");
  s.addNotes(
`[1:35] Three tiers, exactly as the brief specified, and these are the real contents of the measured campaign.

Tier one is a Kuzu graph: six node tables, eight relationship tables, the whole provenance chain from campaign to reference to design to task to output to structure. One design decision there worth flagging — the properties columns hold JSON, so adding a task type needs no migration. That is a deliberate trade: queryability for evolvability, and I would make it again at this stage.

Tier two is SQLite: 114 score rows over ten metrics, seventeen rankings, two round analyses. The method worth pointing at is best_designs, which can exclude the current campaign — that is how the interpreter finds comparable prior work without rediscovering its own designs, and it is the new task this tier makes possible.

Tier three is Parquet plus a manifest: eleven rows from twelve designs, because the curation rules deduplicate sequences, and the manifest records the rules so the set is reproducible rather than merely present.

And only tier one needs a running process to read, which is why the script that mined these numbers could read the other two while the server held the Kuzu lock.`);
}

// ============================================= 10. The protocol node
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "A multi-day cluster pipeline, one stage per turn", "Functionality · the protocol node");

  // Nine stages. Indigo = the stage answers and waits for a human; teal = it submits work.
  const stages = [
    ["intake", "inputs · method"], ["structure", "AF2 · trim"], ["conservation", "hhblits · cpos"],
    ["mpnn", "ProteinMPNN · Halo"], ["score", "rank · select"], ["af3 submit", "~1 GPU-hr each"],
    ["af3 collect", "read the tree"], ["report", "notebook · docx"], ["done", "—"],
  ];
  const WAITS = new Set(["intake", "structure", "score"]);
  const sw = (12.333 - 8 * 0.08) / 9;
  stages.forEach(([t, sub], i) => {
    const waits = WAITS.has(t);
    box(s, M + i * (sw + 0.08), 1.5, sw, 0.72, { title: t, sub: "\n" + sub,
      line: waits ? C.agent : C.task, fill: waits ? C.agentTint : C.white,
      fs: 10, sfs: 8.5, tc: waits ? C.agent : C.ink });
  });
  text(s, [
    { text: "Indigo", options: { bold: true, color: C.agent } },
    { text: " = the stage asks the user something and stops. Four questions, three stages, and " +
      "no " },
    { text: "interrupt()", options: { fontFace: MF } },
    { text: " anywhere in this repo: " },
    { text: "the turn boundary is the checkpoint mechanism.", options: { bold: true } },
  ], M, 2.3, 12.333, 0.3, { fontSize: 11.5 });

  code(s, [
    'protocol = state.get("protocol") or {}',
    'if protocol.get("awaiting") and not _wants_out(text):',
    '    return Command(',
    '        goto="protocol",',
    '        update={"intent": "protocol",',
    '                "status": "Continuing the protocol…"},',
    '    )',
  ], M, 2.7, 6.0, 1.5, { anchor: "coordinator.py:269–274  ·  VERBATIM, one line wrapped", fs: 10 });
  text(s, "It has to come before classification: \"liu\", \"310,364\" and \"go\" all classify as " +
    "chat, which answers from session state and leaves the campaign waiting forever. " +
    "ESCAPE_WORDS is the explicit way out.",
    M, 4.45, 6.0, 0.55, { fontSize: 10.5, color: C.muted, italic: true });

  card(s, M + 6.33, 2.7, 6.0, 1.4, "Two tables, so each entry is testable alone", [
    "STAGES: stage → what it runs. ANSWERS: an awaiting key → how the reply is read. A stage sets awaiting and returns; the next message answers it.",
    "It is the only node that replies every turn and never routes onward — and the only one whose state has to survive the checkpoint to be useful.",
  ], { fill: C.agentTint, hc: C.agent, fs: 10.5 });

  card(s, M, 5.0, 6.0, 1.35, "Two job modes, and the choice that matters", [
    "Transfer specs declare inputs/outputs, so artifacts.wrap rewrites them into a mktemp -d script on the local executor: no queue slot.",
    "Compute specs declare neither and set directory under $PROJ, so their files persist for the next stage and their stdout is the chip's log tail.",
  ], { fill: C.taskTint, hc: C.task, fs: 10 });
  card(s, M + 6.33, 5.0, 6.0, 1.35, "specs.py is the source of truth", [
    "The skill's #SBATCH headers are dead here: PSI/J generates its own submit script. Every walltime, core count and memory figure lives in protocol/specs.py.",
    "ResourceSpecV1.memory is in bytes: {\"memory\": 32} renders --mem=0K, which Slurm reads as the whole node. Everything goes through gib().",
  ], { fill: C.panel, fs: 10 });

  text(s, [
    { text: "Progress is read from the filesystem, never from a handle. ",
      options: { bold: true, color: C.ink } },
    { text: "Handles are in-memory, so a restart cannot re-attach; af3_collect lists the output " +
      "tree instead, which makes it idempotent and identical whether the submit was minutes or " +
      "days ago. 203 of the 417 offline tests are this node. No stage has run on a cluster yet." },
  ], M, 6.5, 12.333, 0.5, { fontSize: 12 });
  s.addNotes(
`[1:15] This is the newest part, and the reason the rest of the machinery exists.

The enzyme redesign protocol is a multi-day cluster pipeline — model the target, trim it, search conservation, redesign with ProteinMPNN, select, fold with AlphaFold3, collect, report — written for a human at a terminal over several days.

It runs here one stage per turn, because the protocol has three points where it must stop for a person and there is no interrupt() anywhere in this repository. The turn boundary is the checkpoint instead: a stage sets awaiting and returns, and the next message answers it.

The code block is the bug I would otherwise have shipped. The route to the protocol has to come before classification, because "liu", "310,364" and "go" all classify as chat — which answers from session state and leaves the campaign waiting forever.

Bottom left is the decision I would flag to anyone building this. A transfer step declares inputs and outputs and gets rewritten into a temp-directory script on the local executor, no queue slot. A compute step declares neither and sets a working directory under the project, so its files persist for the next stage.

And the honest line: two hundred and three of the four hundred and seventeen offline tests cover this node, and no stage of it has run on a cluster, so every number in specs.py is an estimate.`);
}

// ========================================= 11. PERFORMANCE — divider
{
  const s = pres.addSlide();
  dimension(s, {
    n: 2, name: "Performance",
    definition: "Runtime efficiency and effectiveness \u2014 and the places where scale and scientific yield are not linear in each other.",
    claims: [
      ["The chat answers in milliseconds while the work takes minutes",
       `mean ${CONC.mean.toFixed(2)} tasks in flight over ${CONC.span.toFixed(1)} s, peak ${CONC.peak}, longest single task ${CONC.longest.toFixed(1)} s \u2014 nothing in the graph awaits at submission time`],
      ["The seam is what buys that, and it costs one contract",
       "five verbs, four implementations; a failed submission becomes a settled FAILED handle rather than an exception, so a caller has one shape to handle and gather() never raises"],
      ["One number here is a curve, and it was measured for this talk",
       `what a file costs to cross the broker: \u00d7${(STG.rows.find(r => r.shape === "random" && r.payload_bytes === 1048576).ratio).toFixed(2)} of stdout per byte for incompressible data, \u00d7${(STG.rows.find(r => r.shape === "structure" && r.payload_bytes === 1048576).ratio).toFixed(2)} for structure text`],
    ],
    caveat: "there is no scaling study. One host, one pool of four, one campaign \u2014 mean in-flight depth is not a speedup, and this act ends with the slide that says exactly which measurements are missing.",
  });
  s.addNotes(
`[0:20] Act two, performance, and this is the act where I have the least to offer. Three claims: the loop never blocks, the seam is what buys that, and one figure in this deck is an actual curve I measured for this talk. The caveat is the whole act's caveat — there is no scaling study here, and the concurrency number is a mean depth, not a speedup.`);
}

// =================================== 12. One campaign, measured (F3)
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, `One campaign, measured: ${T1.tasks.length} tasks in 146 seconds`, "Performance · one campaign, measured · F3");

  // Gantt from run.json
  const tasks = T1.tasks.filter(t => t.submitted_at && t.finished_at)
    .map(t => ({ ...t, s: Date.parse(t.submitted_at), f: Date.parse(t.finished_at) }));
  const t0 = Math.min(...tasks.map(t => t.s));
  tasks.forEach(t => { t.rs = (t.s - t0) / 1000; t.rf = (t.f - t0) / 1000; });
  const campaignTasks = tasks.filter(t => t.rs >= 60);   // the redesign turn
  const span = Math.max(...campaignTasks.map(t => t.rf)) - 60;
  const gx = M + 1.6, gw = 6.45, gy = 1.95, rowH = 0.108;
  const sx = (v) => gx + ((v - 60) / span) * gw;

  const colorOf = (n) => n.startsWith("fold") ? C.radical
    : n.startsWith("score") ? C.agent
    : n.startsWith("generate") ? C.ui
    : n.startsWith("propose") ? C.lake : C.task;
  campaignTasks.forEach((t, i) => {
    const y = gy + i * rowH;
    const x1 = sx(t.rs), x2 = Math.max(sx(t.rf), x1 + 0.035);
    s.addShape(pres.shapes.RECTANGLE, { x: x1, y: y + 0.015, w: x2 - x1, h: rowH - 0.035,
      fill: { color: colorOf(t.name) }, line: { color: colorOf(t.name) } });
    text(s, t.name, M, y, 1.52, rowH, { fontFace: MF, fontSize: 7, color: C.muted,
      align: "right", valign: "middle" });
    text(s, `${(t.rf - t.rs).toFixed(1)}s`, x2 + 0.03, y, 0.45, rowH,
      { fontSize: 6.5, color: C.muted, valign: "middle" });
  });
  // axis
  const axisY = gy + campaignTasks.length * rowH + 0.06;
  line(s, gx, axisY, gx + gw, axisY, { noArrow: true, color: C.rule, w: 1 });
  [0, 20, 40, 60, 80].forEach(v => {
    const x = sx(60 + v);
    line(s, x, axisY, x, axisY + 0.07, { noArrow: true, color: C.rule, w: 1 });
    label(s, `${v}s`, x - 0.25, axisY + 0.08, 0.5, { fs: 8 });
  });

  // round brackets
  s.addShape(pres.shapes.RECTANGLE, { x: sx(70.7), y: gy - 0.22, w: sx(102.6) - sx(70.7), h: 0.18,
    fill: { color: C.panel }, line: { color: C.rule } });
  text(s, "round 1", sx(70.7), gy - 0.22, sx(102.6) - sx(70.7), 0.18,
    { fontSize: 8.5, bold: true, color: C.text, align: "center", valign: "middle" });
  s.addShape(pres.shapes.RECTANGLE, { x: sx(103.2), y: gy - 0.22, w: sx(146) - sx(103.2), h: 0.18,
    fill: { color: C.panel }, line: { color: C.rule } });
  text(s, "round 2", sx(103.2), gy - 0.22, sx(146) - sx(103.2), 0.18,
    { fontSize: 8.5, bold: true, color: C.text, align: "center", valign: "middle" });

  const rx = gx + gw + 0.55, rwd = W - M - rx;
  card(s, rx, 1.72, rwd, 1.55, "Read the fold rows", [
    "Six submitted in the same instant, finishing 13.3 s to 31.7 s later — out of order, as they land.",
    "Round 2 ran 13.2 s to 42.4 s. flowgentic's 30 s default timeout would have killed half of them.",
  ], { fill: C.radicalTint, hc: C.radical, fs: 10.5 });
  card(s, rx, 3.42, rwd, 1.35, "Read the lookup rows", [
    "pdb_lookup and uniprot_lookup go out together; a cross-reference follow-up resolves second.",
    "Then structure download and literature, also concurrent.",
  ], { fill: C.taskTint, hc: C.task, fs: 10.5 });
  card(s, rx, 4.92, rwd, 1.75, "And one real failure", [
    `ESM Atlas dropped one of round 2's six folds: "${CAMP}-r2-5: ESM Atlas returned no structure".`,
    "That design survived as a sequence-only entry with 4 metrics; the round scored the other 5 and the user got a warning.",
  ], { fill: C.failTint, hc: C.fail, fs: 10.5 });

  // Derived from the same rows the Gantt is drawn from, at build time.
  s.addShape(pres.shapes.RECTANGLE, { x: M, y: 5.88, w: gx + gw - M, h: 0.86,
    fill: { color: C.ink }, line: { color: C.ink } });
  text(s, [
    { text: `${CONC.n} tasks · ${CONC.sum.toFixed(1)} s of handle lifetime in ${CONC.span.toFixed(1)} s of wall clock · ` +
      `mean ${CONC.mean.toFixed(2)} in flight, peak ${CONC.peak}`,
      options: { bold: true, color: "F0C898", breakLine: true } },
    { text: "Derived from these rows at build time, not transcribed. It is also not a speedup: a handle's " +
      "lifetime includes time queued inside a pool of four, and this deck has no serial baseline to divide by. " +
      `The critical path is one ${CONC.longest.toFixed(1)} s fold.`,
      options: { color: "C8D4DD" } },
  ], M + 0.18, 5.96, gx + gw - M - 0.36, 0.72, { fontSize: 10 });

  footer(s, "Drawn from run.json — the Task nodes of tier 1, with their recorded submit and finish times. Nothing here is illustrative.");
  s.addNotes(
`[1:45] This is the real task ledger of the measured campaign, straight out of tier one of the lake, with submit and finish times as recorded.

Look at the orange fold rows. Six go out in the same instant and come back at 13.3, 13.3, 14.3, 17.9, 26.0 and 31.7 seconds — out of order, reaped as they land. That is the whole point of the futures design, and it is the one thing that would be invisible in a sequence diagram.

It also settles an argument against a default. Round two ran from 13.2 to 42.4 seconds, and flowgentic's RetryConfig defaults to a 30-second per-attempt timeout, so with the defaults roughly half of these folds would have been cancelled and silently retried. I'll come back to that.

The band underneath is the only aggregate I will quote: thirty-seven tasks, two hundred and ninety-eight seconds of handle lifetime inside a hundred and forty-six of wall clock, so mean concurrency of two, peaking at six. The deck computes it from these rows at build time, so it cannot go stale. But notice what it is not — a lifetime includes time queued inside a pool of four, and I have no serial baseline, so that two is a depth, not a speedup. The critical path is a single forty-two second fold.

And the bottom right is not contrived. ESM Atlas genuinely dropped one of six requests: that design came through as sequence-only with four metrics instead of eleven, the other five scored normally, and the user saw a warning.`);
}

// ============================================ 13. The seam: contract
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "★ The Task Interface contract", "Performance · the seam · 1 of 3");

  code(s, [
    'class TaskInterface(ABC):',
    '    name: str = "base"',
    '    capabilities: Capabilities = Capabilities()',
    '',
    '    @abstractmethod',
    '    async def submit(self, spec: TaskSpec) -> TaskHandle:',
    '        """Place the task and return immediately."""',
    '',
    '    async def status(self, handle)  -> TaskState',
    '    async def logs(self, handle, offset=0) -> LogChunk',
    '    async def result(self, handle)  -> Any',
    '    async def cancel(self, handle)  -> bool',
    '    async def close(self)           -> None',
  ], M, 1.55, 5.5, 2.1, { anchor: "tasks/base.py:155–193  ·  TRIMMED", fs: 10.5 });

  code(s, [
    '@dataclass(frozen=True)',
    'class Capabilities:',
    '    supports_cancel:      bool = False',
    '    supports_log_stream:  bool = False',
    '    supports_push_events: bool = False',
    '    supports_staging:     bool = False',
  ], M, 3.85, 5.5, 1.1, { anchor: "tasks/base.py:62–67  ·  VERBATIM", fs: 10.5 });

  const rows = [
    ["local", "app-local compute, rhapsody pool", "✔", "—", "—", "—"],
    ["query", "REST retrieval, on the loop", "✔", "—", "—", "—"],
    ["orbit", "Rhapsody tasks + PSI/J jobs", "✔", "✔", "✔", "✔"],
    ["globus", "Globus Compute (hpc-bridge)", "part", "—", "—", "—"],
  ];
  const cw = [1.0, 2.76, 0.6675, 0.6675, 0.6675, 0.6675];
  const hdr = ["interface", "what it is", "cancel", "logs", "push", "stage"];
  const tbl = [hdr.map(h => ({ text: h, options: { bold: true, color: C.white,
    fill: { color: C.task }, fontSize: 9.5 } }))]
    .concat(rows.map((r, i) => r.map((c, j) => ({ text: c, options: {
      fontFace: j === 0 ? MF : BF, fontSize: j === 0 ? 10.5 : 10.5, bold: j === 0,
      align: j > 1 ? "center" : "left",
      color: c === "✔" ? C.good : (c === "part" ? C.radical : C.text),
      fill: { color: i % 2 ? C.white : C.panel } } }))));
  s.addTable(tbl, { x: M + 5.9, y: 1.55, w: cw.reduce((a, b) => a + b), colW: cw,
    border: { type: "solid", color: C.rule, pt: 0.75 }, fontFace: BF, valign: "middle",
    rowH: [0.28, 0.3, 0.3, 0.3, 0.3], margin: 0.06 });

  text(s, "The flags are the honest part. An interface that cannot tail logs says so, and the " +
    "manager simply does not start a drain for it — no caller has to know which backend it got.",
    M + 5.9, 3.28, 6.93, 0.6, { fontSize: 11.5, color: C.text });

  code(s, [
    'try:',
    '    handle = await interface.submit(spec)',
    'except Exception as exc:',
    '    # Surface the failure as a settled handle so callers',
    '    # need no separate error path.',
    '    future = loop.create_future()',
    '    future.set_exception(exc)',
    '    handle = TaskHandle(..., state=TaskState.FAILED,',
    '                        error=str(exc))',
  ], M + 5.9, 4.05, 6.93, 1.5, { anchor: "tasks/manager.py:134–150  ·  TRIMMED", fs: 10.5 });

  text(s, [
    { text: "The invariant: ", options: { bold: true, color: C.ink } },
    { text: "a caller holds a handle whose future resolves. Not sometimes a handle and sometimes " +
      "an exception. A rejected submission is a handle that has already failed — which is why " +
      "gather() never raises and the round survives a dead endpoint." },
  ], M, 5.75, 12.3, 0.8, { fontSize: 13 });
  footer(s, "TaskState: PENDING · PROVISIONING · QUEUED · RUNNING · DONE · FAILED · CANCELED · UNKNOWN, with .terminal and an alias table for Orbit/PSI-J vocabularies.");
  s.addNotes(
`[2:00] Here is the whole vocabulary. Five verbs and a capability record.

Submit is the only abstract method, and its docstring is the contract: place the task and return immediately. Status, logs, result, cancel and close all have defaults that work for an in-process future, so a trivial interface is about ten lines.

The capability flags are where I tried to be honest rather than uniform. Globus Compute cannot tail a running task's logs — that is a real property of the service, not a gap in my adapter — so the flag says false and the manager simply does not start a log drain. No caller branches on which backend it got.

The bottom-left code is the piece I would defend hardest. When submission itself fails — no endpoint, unknown task name — you do not get an exception. You get a handle whose future is already failed. That single decision is why the gather path never raises and why a round survives a dead endpoint: there is exactly one shape to handle, and the failure arrives through the same channel as a task that failed after starting.

The state enum normalizes across vocabularies, because ORBIT and PSI/J each have their own and I did not want those leaking upward.`);
}

// =========================================== 14. The seam: substrate
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "★ Through flowgentic to a rhapsody process pool", "Performance · the seam · 2 of 3");

  // the path
  const pw = 2.35, py = 1.5;
  const chain = [
    ["task body", "tools/*.py\nmodule level", C.task, "built"],
    ["flowgentic", "FUNCTION_TASK\n+ RetryConfig", C.radical, "built"],
    ["asyncflow", "WorkflowEngine", C.radical, "built"],
    ["rhapsody", "ConcurrentExecution\nBackend", C.radical, "built"],
    ["ProcessPool", "4 workers", C.radical, "built"],
  ];
  chain.forEach(([t, sub, col, st], i) => {
    const x = M + i * (pw + 0.17);
    box(s, x, py, pw, 0.8, { title: t, sub: "\n" + sub, line: col, fill: col === C.radical ? C.radicalTint : C.taskTint,
      fs: 12, sfs: 9.5, tc: col === C.radical ? C.radical : C.ink, status: st });
    if (i < chain.length - 1) line(s, x + pw + 0.01, py + 0.4, x + pw + 0.16, py + 0.4, { w: 1.5 });
  });

  code(s, [
    'retry = RetryConfig(',
    '    # Folding or an HPC job can take far longer',
    '    # than flowgentic\'s 30s default.',
    '    timeout_sec=None,',
    '    max_attempts=1,',
    '    retryable_exceptions=(ConnectionError, OSError),',
    ')',
    'def wrap(body):',
    '    return integration.execution_wrappers.asyncflow(',
    '        body, flow_type=AsyncFlowType.FUNCTION_TASK,',
    '        backend="compute", retry=retry)',
  ], M, 2.55, 6.1, 1.85, { anchor: "runtime.py:295–308  ·  TRIMMED", fs: 10 });

  code(s, [
    'async def submit(self, spec: TaskSpec) -> TaskHandle:',
    '    fn = self._resolve(spec.name)',
    '    # create_task so submission returns immediately: the',
    '    # flowgentic wrapper for FUNCTION_TASK is a coroutine',
    '    # function, not a future factory.',
    '    future = asyncio.ensure_future(',
    '        _call(fn, spec, llm_credentials(spec.name)))',
    '    return self._handle(spec, future)',
  ], M, 4.6, 6.1, 1.25, { anchor: "tasks/local.py:81–86  ·  VERBATIM", fs: 10 });

  card(s, M + 6.4, 2.55, 6.43, 1.3, "Three constraints the pool imposes", [
    "Task bodies live at module level in tools/ — no closures over clients, or they will not pickle.",
    "Clients are built inside the body; a parent's cannot cross into a worker.",
    "The entry point needs a __main__ guard, or uvicorn re-imports under fork.",
  ], { fill: C.radicalTint, hc: C.radical, fs: 10 });

  s.addShape(pres.shapes.RECTANGLE, { x: M + 6.4, y: 4.0, w: 6.43, h: 0.38,
    fill: { color: C.fail }, line: { color: C.fail } });
  text(s, "Finding 1 — flowgentic hard-imports aiohttp", M + 6.5, 4.0, 6.2, 0.38,
    { fontSize: 12.5, bold: true, color: C.white, valign: "middle" });
  code(s, [
    '# Try to include aiohttp timeouts if present',
    'try:',
    '    import aiohttp  # type: ignore',
    '    ex.extend([aiohttp.ClientConnectionError,',
    '               aiohttp.ServerTimeoutError])',
    'except Exception:',
    '    raise',
  ], M + 6.4, 4.45, 6.43, 1.22,
    { anchor: "refcodes/flowgentic/…/fault_tolerance.py:73–84  ·  VERBATIM", fs: 10, fill: "3A1F1C" });
  text(s, "The comment says \"if present\"; the code says \"or die\". Same shape for httpx at :60–72. " +
    "Reached whenever retryable_exceptions is left at its default, which is ().",
    M + 6.4, 5.8, 6.43, 0.5, { fontSize: 11, color: C.fail });

  footer(s, "ModuleNotFoundError: No module named 'aiohttp' — raised from fault_tolerance.py:75 on the first wrapped task, in an env with httpx but not aiohttp.");
  s.addNotes(
`[2:10] This is the band that crosses out of my process, and it is five hops: a module-level task body, the flowgentic wrapper, asyncflow's engine, a rhapsody concurrent backend, a four-worker process pool.

Top left is the wrapper. The comment in it is doing real work: flowgentic's RetryConfig defaults to a 30-second per-attempt timeout with three attempts, which is right for a service call and wrong for a fold. We saw on the last slide that half the round-two folds exceed it. So we pass timeout_sec None and max_attempts one, and own retries ourselves.

Bottom left: submission has to return immediately, and flowgentic's FUNCTION_TASK wrapper is a coroutine function rather than a future factory, so we wrap it in ensure_future — that comment exists because I got it wrong first and blocked the loop. Top right, the pool's three constraints: bodies at module level, clients built inside the body, a main guard on the entry point.

And the red block is the first of the findings. That is verbatim flowgentic. The comment says "try to include aiohttp timeouts if present" and the handler says raise. So aiohttp becomes a hard requirement, and so does httpx twelve lines up. It fires whenever retryable_exceptions is left at its default, which is the empty tuple — the common case. I think that except clause wants to be a pass, and I would like to know if you agree.`);
}

// ========================================================= 15. Orbit
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "★ RADICAL Orbit: two clients, two concurrency bridges", "Performance · the seam · 3 of 3");

  // the two client paths
  box(s, M, 1.5, 5.9, 0.42, { title: "EndpointRuntime(broker_url, cert, name, token)",
    line: C.radical, fill: C.radicalTint, fs: 11.5, tc: C.radical, round: false });
  box(s, M, 2.05, 2.85, 1.55, { title: "RhapsodyClient", sub:
    "\nfunction + executable tasks\nregister_session · submit_tasks\nwait_tasks · get_task · cancel_task\n\npushes task_status events",
    line: C.radical, fill: C.white, fs: 12, sfs: 9.5, tc: C.radical });
  box(s, M + 3.05, 2.05, 2.85, 1.55, { title: "PSIJClient", sub:
    "\nbatch jobs\nsubmit_job · cancel_job\nget_job_status(id, stdout_offset,\n  stderr_offset)\n\nthe only log tail that exists",
    line: C.radical, fill: C.white, fs: 12, sfs: 9.5, tc: C.radical });

  card(s, M, 3.75, 5.9, 1.15, "Two threading problems, two one-liners", [
    "Every Orbit client method is synchronous and blocking → each of 23 call sites goes through asyncio.to_thread.",
    "Push callbacks arrive on Orbit's listener thread → _dispatch hops to our loop with call_soon_threadsafe.",
  ], { fill: C.radicalTint, hc: C.radical, fs: 10.5 });

  code(s, [
    'def _dispatch(self, fn, *args) -> None:',
    '    """Hop from Orbit\'s listener thread onto our event loop."""',
    '    if self._loop is None or self._loop.is_closed():',
    '        return',
    '    self._loop.call_soon_threadsafe(fn, *args)',
  ], M, 5.05, 5.9, 0.95, { anchor: "tasks/hpc/orbit.py:348–352  ·  VERBATIM", fs: 10 });

  s.addShape(pres.shapes.RECTANGLE, { x: M + 6.2, y: 1.5, w: 6.63, h: 0.38,
    fill: { color: C.fail }, line: { color: C.fail } });
  text(s, "Findings 4 and 5 — what a terminal event does not carry", M + 6.3, 1.5, 6.4, 0.38,
    { fontSize: 12.5, bold: true, color: C.white, valign: "middle" });
  code(s, [
    '# The terminal event carries state and exit_code but not',
    '# stdout, so the full record is fetched before the future',
    '# is resolved.',
    'if not data.get("stdout") and self._rhapsody is not None:',
    '    info = await asyncio.to_thread(',
    '        self._rhapsody.get_task, handle.id)',
    '    # The event wins on state; the fetch fills in the output.',
    '    merged = {**info, **{k: v for k, v in data.items()',
    '                         if v not in (None, "")}}',
  ], M + 6.2, 1.97, 6.63, 1.55,
    { anchor: "tasks/hpc/orbit.py:373–386  ·  TRIMMED", fs: 10, fill: "3A1F1C" });
  code(s, [
    'if state is TaskState.FAILED and not error:',
    '    error = (f"exit code {exit_code}" if exit_code not in (None, 0)',
    '             else (data.get("stderr")',
    '                   or handle.log_tail[-500:] or "job failed"))',
  ], M + 6.2, 3.65, 6.63, 0.82,
    { anchor: "tasks/hpc/orbit.py:448–453  ·  TRIMMED", fs: 10, fill: "3A1F1C" });
  text(s, "A completed task's result came back empty until the re-fetch. A job that exited 3 " +
    "reported no reason at all — only the code — so FAILED always synthesises an explanation.",
    M + 6.2, 4.6, 6.63, 0.55, { fontSize: 11, color: C.fail });

  card(s, M + 6.2, 5.2, 6.63, 1.1, "How far this is proven", [
    "12 tests against a real localhost broker + endpoint: push states, incremental log tailing by offset, a failing job, cancelling a running one, that stdout comes back whole and unduplicated, that declared inputs and outputs actually travel, and one real ProteinMPNN run on CPU.",
    "And, since 2026-10-09, 8 more against a real one: a broker on a public VM, an endpoint registered from a cluster login node, Slurm ids back in handle.meta. Slide 19.",
  ], { fill: C.panel, fs: 10.5 });
  footer(s, "tests/test_orbit_local.py — skipped unless the Orbit CLI scripts are runnable; LocalOrbitStack brings up broker and endpoint as subprocesses.");
  s.addNotes(
`[2:20] The remote interface uses the raw clients rather than anything higher, on purpose: I wanted to see what the substrate actually offers.

Two clients, two different jobs. RhapsodyClient handles function and executable tasks and pushes status events at us. PSIJClient handles batch jobs, and it is the one place in either backend where you can tail a running job's output — get_job_status takes stdout and stderr byte offsets. That is what the whole log-streaming story is built on, and it works.

Two threading problems, neither a complaint: every client method is synchronous, so all 23 call sites go through to_thread, and push callbacks arrive on Orbit's own listener thread, so _dispatch hops them onto our loop with call_soon_threadsafe.

The red half is findings four and five. A completed task's terminal event carries state and exit code but not stdout, so my first version resolved futures with empty results — the fix re-fetches with get_task, and lets the event win on state while the fetch fills in output. And a failed job reports only a non-zero exit code; no reason reaches the client at all. So FAILED always synthesises an explanation from the exit code, then stderr, then the log tail. I would rather Orbit told me.

Status, plainly: twelve tests against a real localhost broker and endpoint, including one real ProteinMPNN run — and since the ninth of October, this same client code reaching a cluster login node through a public broker and coming back with Slurm job ids.`);
}

// ======================= 16. What we have and have not measured (F5)
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "What we have measured, and what we have not", "Performance · the honest part");

  // --- F5: the staging channel, measured by slides/bench_staging.py ---------
  const pts = STG.rows.filter(r => r.payload_bytes <= STG.limits.artifact_max_bytes);
  const sizes = [...new Set(pts.map(r => r.payload_bytes))].sort((a, b) => a - b);
  const x0 = 1.12, x1 = 6.3, yTop = 1.95, yBot = 4.18, RMAX = 1.6;
  const px = i => x0 + ((i + 0.5) * (x1 - x0)) / sizes.length;
  const py = r => yBot - (Math.min(r, RMAX) / RMAX) * (yBot - yTop);

  text(s, "F5 · what one file costs to cross the broker", M, 1.5, 5.9, 0.26,
    { fontSize: 12, bold: true, color: C.task });
  [0, 0.5, 1.0, 1.5].forEach(g => {
    s.addShape(pres.shapes.LINE, { x: x0, y: py(g), w: x1 - x0, h: 0,
      line: { color: g === 1 ? C.muted : C.rule, width: g === 1 ? 1 : 0.75,
              dashType: g === 1 ? "dash" : "solid" } });
    text(s, `×${g.toFixed(1)}`, M, py(g) - 0.11, 0.56, 0.22,
      { fontSize: 9.5, color: C.muted, align: "right" });
  });
  text(s, "break-even: stdout = payload", x1 - 2.05, py(1.0) - 0.22, 2.0, 0.2,
    { fontSize: 8.5, color: C.muted, align: "right", italic: true });

  [["random", C.radical, "incompressible"], ["structure", C.lake, "structure text"]].forEach(
    ([shape, col, lab]) => {
      const series = sizes.map(sz => pts.find(r => r.shape === shape && r.payload_bytes === sz));
      series.forEach((r, i) => {
        if (i) {
          const a = series[i - 1];
          s.addShape(pres.shapes.LINE, { x: px(i - 1), y: py(a.ratio),
            w: px(i) - px(i - 1), h: py(r.ratio) - py(a.ratio),
            line: { color: col, width: 2 } });
        }
        s.addShape(pres.shapes.OVAL, { x: px(i) - 0.065, y: py(r.ratio) - 0.065, w: 0.13, h: 0.13,
          fill: { color: col }, line: { color: col } });
      });
      const last = series[series.length - 1];
      text(s, `${lab}  ×${last.ratio.toFixed(2)}`, px(sizes.length - 1) - 2.2,
        py(last.ratio) + (shape === "random" ? -0.28 : 0.06), 2.15, 0.24,
        { fontSize: 10, bold: true, color: col, align: "right" });
    });

  s.addShape(pres.shapes.LINE, { x: x0, y: yBot, w: x1 - x0, h: 0,
    line: { color: C.text, width: 1 } });
  sizes.forEach((sz, i) => text(s, sz >= 1024 * 1024 ? "1 MiB" : `${sz / 1024} KiB`,
    px(i) - 0.45, yBot + 0.06, 0.9, 0.22,
    { fontSize: 9.5, color: C.muted, align: "center" }));
  text(s, "payload size  →", x0, yBot + 0.3, 2.2, 0.22, { fontSize: 9.5, color: C.muted });
  text(s, "stdout bytes per payload byte, gzip + base64, framed", x1 - 3.6, yBot + 0.3, 3.6, 0.22,
    { fontSize: 9, color: C.muted, align: "right", italic: true });

  const big = STG.rows.find(r => r.shape === "structure" && r.payload_bytes === 2 * 1024 * 1024);
  const bigR = STG.rows.find(r => r.shape === "random" && r.payload_bytes === 2 * 1024 * 1024);
  const mib = STG.rows.find(r => r.shape === "random" && r.payload_bytes === 1024 * 1024);
  card(s, M, 4.7, 5.9, 1.5, "Both ceilings are in the wrong units", [
    `Outbound: a 2 MiB PDB is refused as too_large — the cap is checked on the raw size — ` +
    `though it compresses to ${(big.argv_bytes / 1e6).toFixed(2)} MB. A 1 MiB random file is accepted, ` +
    `and costs ${(mib.stdout_bytes / 1e6).toFixed(2)} MB.`,
    `Inbound has no declared cap at all: the chunks must fit ARG_MAX, so exec fails with E2BIG ` +
    `at ~${(STG.argv_ceiling_bytes / 1048576).toFixed(1)} MiB of incompressible payload. Our backlog, not an ask.`,
  ], { fill: C.failTint, hc: C.fail, fs: 10, hfs: 12 });

  // --- right: the ledger ---------------------------------------------------
  const RX = 6.72, RW = 6.11;
  card(s, RX, 1.5, RW, 2.42, "Measured, and in this deck", [
    `mean ${CONC.mean.toFixed(2)} tasks in flight over ${CONC.span.toFixed(1)} s, peak ${CONC.peak} — derived from run.json at build time`,
    `the critical path is one ${CONC.longest.toFixed(1)} s fold; six folds share four pool slots, so two of each round wait`,
    "~320 KB of checkpoint per turn before coordinates moved to blobs — growing, and breaking nothing",
    "the staging channel, left: measured against the real wrap() and collect()",
    "417 offline tests in ~155 s with no network, no pool and no endpoint",
  ], { fill: C.panel, fs: 10.5, hfs: 13 });
  card(s, RX, 4.02, RW, 2.11, "Not measured — so not claimed", [
    "no scaling curve: pool width has never been varied, and 4 is a default, not a finding",
    "no GPU occupancy. ORBIT_JOB_GPUS=1 is a request; nothing here has read a GPU's utilisation",
    "no multi-node run, and never two endpoints at once",
    "no serial baseline, so nothing above divides into a speedup",
    "every walltime and memory figure in protocol/specs.py is still an estimate",
  ], { fill: C.failTint, hc: C.fail, fs: 10.5, hfs: 13 });

  s.addShape(pres.shapes.RECTANGLE, { x: M, y: 6.28, w: W - 2 * M, h: 0.72,
    fill: { color: C.ink }, line: { color: C.ink } });
  text(s, [
    { text: "Where scale and science stop being linear:  ", options: { bold: true, color: "F0C898" } },
    { text: "stage_score prints “AlphaFold3 is about an hour of GPU each, so that is N GPU-hours” " +
      "and stops for an answer (protocol.py:616). Cost is linear in the designs you fold; yield is not — " +
      "they are diversity-selected, so the tenth is less like the other nine than the second was. " +
      "That trade is the one checkpoint in the protocol a human cannot skip.",
      options: { color: "C8D4DD" } },
  ], M + 0.2, 6.37, 11.9, 0.6, { fontSize: 11.5 });

  footer(s, `F5: slides/bench_staging.py on ${STG.source.host}, ${STG.source.date} — no broker in the path, ` +
    `so a floor on what a real job pays. The structure series is a real ${REF.pdb_id} PDB, tiled above 462 KB.`);

  s.addNotes(
`[1:25] The slide I would want to see if I were you, so it is here rather than in an appendix.

The figure is the only curve in this deck, and I measured it for this talk. It is about the one data path this system has to a cluster: because the broker forwards neither outputs nor stdin_text — finding five, later — a job returns a file by printing it on stdout, gzipped, base64'd and framed. So I measured what that costs, driving the real wrap and collect functions locally.

Orange is incompressible data, about one and a third bytes of stdout per byte of payload, which is base64 doing what base64 does. Green is structure text, a third of a byte per byte, because a PDB gzips about four to one. Cost depends entirely on entropy.

The ceilings do not, and that is the finding. Outbound the cap is checked on the raw file size, so a two megabyte PDB is refused even though it would compress to two thirds of a megabyte, while a one megabyte random file sails through and costs one point four. Inbound there is no declared cap at all: exec fails with E2BIG at about one and a half megabytes of incompressible payload. Both caps are in the wrong units — that is going in our backlog, not on your list.

On the right, the ledger. Measured: in-flight depth, the critical path, checkpoint growth, this curve, the suite. Not measured and therefore not claimed: no scaling curve, no GPU occupancy, no multi-node run, no serial baseline.

At the bottom is where this stops being a systems question. When the protocol selects designs it says AlphaFold3 is about an hour of GPU each, and waits. Cost is linear in how many you fold; yield is not, because they are diversity-selected — the tenth is less like the other nine than the second was. That is the nonlinearity, and it is the piece of this story I would most like to measure and cannot.`);
}

// =================================================== 17. Degradation
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "Degradation is a feature, and it is tested", "Performance · effectiveness when a dependency fails");

  const rows = [
    ["No ANTHROPIC_API_KEY", "every node has a deterministic rule-based path", "the measured campaign ran this way"],
    ["No Orbit endpoint", "interface_for falls hpc → local and logs it", "tests/test_tasks.py"],
    ["ChemGraph not installed", "the task reports unavailable; nothing else changes", "chemgraph_available()"],
    ["ESM Atlas drops a request", "design survives sequence-only; round scores the rest", `really happened: ${CAMP}-r2-5`],
    ["Tier 1/2 write fails", "warn, rank in memory, tell the user in the reply", "test_lake_write_failure_does_not_lose_the_round"],
    ["No SQLite checkpointer", "InMemorySaver, with a note on /api/health", "runtime.py:466"],
  ];
  const cw = [2.9, 5.0, 4.43];
  const tbl = [["when", "what happens", "evidence"].map(h => ({ text: h,
    options: { bold: true, color: C.white, fill: { color: C.task }, fontSize: 11.5 } }))]
    .concat(rows.map((r, i) => r.map((c, j) => ({ text: c, options: {
      fontFace: j === 2 ? MF : BF, fontSize: j === 2 ? 9.5 : 11.5, bold: j === 0,
      color: j === 2 ? C.muted : C.text,
      fill: { color: i % 2 ? C.white : C.panel } } }))));
  s.addTable(tbl, { x: M, y: 1.55, w: cw.reduce((a, b) => a + b), colW: cw,
    border: { type: "solid", color: C.rule, pt: 0.75 }, fontFace: BF, valign: "middle",
    rowH: [0.32, 0.34, 0.34, 0.34, 0.36, 0.42, 0.34], margin: 0.07 });

  code(s, [
    '# A storage failure must not lose the user\'s round: the designs are',
    '# already in state, so we log, warn, and carry on.',
    'try:',
    '    ranked = deps.history.rank_round(campaign_id, round_no, ...)',
    'except Exception as exc:',
    '    storage_error = storage_error or str(exc)',
    '    log.warning("persisting the ranking failed: %s", exc)',
    '    ranked = _rank_in_memory(combined, metric_name, direction)',
    '...',
    'if storage_error:',
    '    update["warnings"] = [f"Design History write failed: {storage_error}"]',
  ], M, 4.15, 7.6, 1.8, { anchor: "graph/nodes/analyst.py:166–233  ·  TRIMMED", fs: 10 });

  card(s, M + 7.9, 4.15, 4.93, 0.95, "How the user finds out", [
    "The interpreter appends a \"Caveats from this run\" section to its reply, from the warnings channel.",
  ], { fill: C.failTint, hc: C.fail, fs: 10.5 });
  card(s, M + 7.9, 5.25, 4.93, 0.7, "Where this came from", [
    "A readonly-SQLite error during a live run. My own fault — but it exposed unguarded writes.",
  ], { fill: C.panel, fs: 10.5 });
  s.addNotes(
`[1:15] Six failure modes, what each does, and the evidence that it does it.

The fifth row is the one to dwell on. During a live run I hit "attempt to write a readonly database" — my own fault, I deleted a data directory under a running server — but it exposed something real: the analyst wrote to the lake unguarded, so a storage problem could lose a round the user had waited two minutes for.

The fix is at the bottom. Each tier write is guarded independently, a failure falls back to ranking in memory, and the error goes onto a warnings channel that survives the turn, so the interpreter appends a "Caveats from this run" section rather than silently handing back a thinner answer. A test kills the lake mid-round and asserts the designs still come back.

And the fourth row is the one I did not have to arrange. ESM Atlas dropped a request during the measured campaign, the design came through sequence-only, the round scored the other five, and the warning surfaced. That is the whole mechanism working on a failure I did not choose.`);
}

// =========================================== 18. USABILITY — divider
{
  const s = pres.addSlide();
  dimension(s, {
    n: 3, name: "Usability",
    definition: "Not the interface \u2014 the use cases: what this lends itself to as an application, as a platform, and as a component in someone else's workflow.",
    claims: [
      ["As an application: a chat with a structure viewer",
       "seven SSE frame kinds in one ordered stream, task chips that name their interface, and artifacts \u2014 Markdown, .docx, a sortable ensemble table, two Mol* specs \u2014 you can hand to a collaborator"],
      ["As a platform: other people's keys, endpoints and allocations",
       "logins, per-user credentials as Fernet ciphertext, one OrbitInterface per user under its own client name; 22 HTTP routes and a CLI, so no part of it needs the browser"],
      ["As a component: one contract, and a record that outlives the process",
       "the Task Interface is the extension point \u2014 four implementations over ~102 shared lines \u2014 and tiers 2 and 3 of the lake are a SQLite file and a Parquet file, readable with nothing running"],
    ],
    caveat: "one deployment, one lab, and no second consumer. Nothing has yet driven this as a component except its own tests \u2014 the component claim is the shape of the code, not evidence.",
  });
  s.addNotes(
`[0:20] Act three, usability — use cases, not user interface. Three modes: as an application, as a platform that holds other people's credentials and endpoints, and as a component something else drives. All three are built. The caveat is that nothing outside this repo has driven it as a component yet, so that third claim is the shape of the code rather than evidence.`);
}

// ====================================================== 19. Frontend
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "The web app: seven frame kinds and one viewer", "Usability · as an application");

  // frame kinds
  const frames = [
    ["token", "LLM text delta", C.ui], ["status", "node progress line", C.agent],
    ["task", "submitted · log · state", C.radical], ["state", "UI_STATE_KEYS slice", C.agent],
    ["message", "a complete reply", C.ui], ["error", "stream-fatal", C.fail],
    ["done", "end of turn", C.muted],
  ];
  const fw = 1.72;
  frames.forEach(([t, sub, col], i) => {
    const x = M + i * (fw + 0.08);
    box(s, x, 1.55, fw, 0.78, { title: t, sub: "\n" + sub, line: col, fill: C.white,
      fs: 12, sfs: 9, subMono: false });
  });
  text(s, "POST /api/chat returns text/event-stream. One asyncio.Queue merges the graph's own " +
    "astream frames with TaskManager events, so task progress and node status share one ordered stream.",
    M, 2.45, 12.3, 0.5, { fontSize: 11.5, color: C.text });

  code(s, [
    'let buffer = "";',
    'while (true) {',
    '  const { done, value } = await reader.read();',
    '  if (done) break;',
    '  buffer += decoder.decode(value, { stream: true });',
    '  let split;',
    '  while ((split = buffer.indexOf("\\n\\n")) !== -1) {',
    '    const raw = buffer.slice(0, split);',
    '    buffer = buffer.slice(split + 2);',
    '    ...  yield JSON.parse(line.slice(6)) as Frame;',
    '  }',
    '}',
  ], M, 3.05, 6.1, 2.1, { anchor: "frontend/src/lib/api.ts:42–61  ·  TRIMMED", fs: 10 });
  text(s, "A chunk boundary can land mid-frame, so partial frames are held in a buffer until a " +
    "blank line. A malformed frame is skipped, never fatal.",
    M, 5.25, 6.1, 0.5, { fontSize: 11, color: C.muted, italic: true });

  card(s, M + 6.4, 3.05, 6.43, 1.3, "The Mol* viewer", [
    "rcsb-molstar 2.14.7 UMD from jsDelivr, loader cached on window.__molstarLoader so one fetch serves every mount.",
    "One viewer per mount; the spec is re-applied on change; ResizeObserver → handleResize.",
  ], { fill: C.uiTint, hc: C.ui, fs: 10.5 });
  card(s, M + 6.4, 4.45, 6.43, 1.3, "Two things that bit", [
    "createComponent takes no colour, so colours go through pluginCall → updateRepresentationsTheme.",
    "Coordinates are inlined as a json artifact by the analyst, so the viewer needs no second authenticated fetch.",
  ], { fill: C.failTint, hc: C.fail, fs: 10.5 });

  text(s, [
    { text: "Confirmed rendering ", options: { bold: true, color: C.good } },
    { text: "in a browser on 2026-10-01. It was built without one — the dev server, the proxy, a " +
      "real artifact through it and the CDN assets were all that could be checked at the time." },
  ], M, 5.85, 12.3, 0.6, { fontSize: 12.5 });
  s.addNotes(
`[1:10] Briefly, because the backend is what you came for.

The chat endpoint is a POST that returns an event stream — not EventSource, because the prompt goes in the body. Seven frame kinds. The thing I would point at is that a single asyncio queue merges LangGraph's own stream with TaskManager events, so node status and task progress arrive in one ordered stream rather than two the client has to interleave.

The client code is there for a bug class people hit constantly: a network chunk boundary lands mid-frame, so partial frames are held in a buffer until a blank line arrives, and a malformed frame is skipped rather than killing the stream.

The viewer is rcsb-molstar from the CDN, the loader cached on window, one viewer per mount. The usability point rather than the implementation one: what the agent sends the browser is a sanitized JSON view spec, never generated JavaScript, because the brief asked for the latter and that would put a prompt's output into the page as code. There is a backup slide on it.

And this was built without a browser available, so for a while the canvas was the one thing nobody had looked at — everything around it checked out, which is exactly when you convince yourself it is fine. Confirmed rendering on the first of October.`);
}

// ====================================================== 20. Deployed
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "Deployed, and every user brings their own keys", "Usability · as a platform");

  const hops = [
    ["browser", "HttpOnly session cookie", C.ui],
    ["Caddy :443", "HTTPS, same host", C.muted],
    ["PEAT :8000", "loopback · per-user settings", C.agent],
    ["Orbit broker :8443", "systemd, on the VM", C.radical],
    ["amarel3 endpoint", "psij,sysinfo → Slurm", C.radical],
  ];
  const hw = (12.333 - 4 * 0.32) / 5;
  hops.forEach(([t, sub, col], i) => {
    const x = M + i * (hw + 0.32);
    box(s, x, 1.5, hw, 0.8, { title: t, sub: "\n" + sub, line: col,
      fill: col === C.radical ? C.radicalTint : C.white, fs: 11.5, sfs: 9,
      tc: col === C.muted ? C.ink : col });
    if (i < hops.length - 1) line(s, x + hw + 0.03, 1.9, x + hw + 0.29, 1.9, { both: i === 2 });
  });
  label(s, "one VM: Caddy, the agent and the broker", M, 2.38, 7.3, { align: "left" });
  label(s, "the endpoint is a cluster login node, across the internet",
    M + 7.3, 2.38, 5.03, { align: "right" });

  card(s, M, 2.78, 6.0, 1.45, "Logins are off by default, and off they change nothing", [
    "No runtime.auth → _user() returns None and every ownership check passes, which is why the other 399 tests needed no edit when logins arrived.",
    "On: every route but login, logout and a bare /api/health needs the cookie; a session must be the caller's (404 otherwise); settings need the admin role, not a shared token.",
  ], { fill: C.panel, fs: 10.5 });

  card(s, M + 6.33, 2.78, 6.0, 1.7, "A key that must not be written down anywhere", [
    "Not in configurable: LangGraph copies every string in it into checkpoint metadata, so the key would be saved every turn.",
    "Not in spec.params: those are written to the lake and returned by the task routes. A pool task is handed the key as a call argument instead.",
    "test_no_user_secret_is_persisted_by_a_turn scans the checkpointer, the task snapshots and the data dir for it.",
  ], { fill: C.failTint, hc: C.fail, fs: 10.5 });

  code(s, [
    'def settings(self) -> Settings:',
    '    turn = current_turn.get()',
    '    return turn.settings if turn is not None else self.base_settings',
  ], M, 4.6, 6.0, 0.8, { anchor: "graph/deps.py:49–51  ·  VERBATIM", fs: 10 });
  text(s, "A turn's user travels in a ContextVar, which asyncio copies into every task the turn " +
    "creates. So every node gets that user's key and endpoint without knowing users exist, and " +
    "each user's endpoint gets its own OrbitInterface under its own client name.",
    M, 5.55, 6.0, 0.7, { fontSize: 10.5, color: C.muted, italic: true });

  card(s, M + 6.33, 4.6, 6.0, 1.65, "What this does not give you — backlog A21", [
    "The broker is shared, and it is not a tenant boundary: one ingress token, and anyone holding it can submit to every endpoint on it. Per-user HPC isolation needs a broker per user.",
    "Sessions from before logins have no owner, so they are unreachable under logins.",
    "The lockout is in memory, and trusts X-Forwarded-For only from a loopback peer — right behind this proxy, wrong behind any other.",
  ], { fill: C.panel, fs: 10.5 });

  footer(s, "plans/LINODE_DEPLOY.md is the plan and the record; plans/AMAREL_ENDPOINT.md is the cluster side, with the acceptance ladder.");
  s.addNotes(
`[1:10] One slide on where this actually runs, because "it works on my laptop" is not an architecture claim.

Left to right: a browser, Caddy terminating TLS, the agent on loopback and the Orbit broker under systemd — all on one small VM — then the endpoint, a cluster login node registered to that broker across the internet. The two-way arrow is the only one, because push events come back over the same websocket.

Logins are off by default, and off they change nothing: with no auth object the user lookup returns None and every ownership check passes, which is why the rest of the suite needed no edit when they arrived.

The red card is the part I would want reviewed. A user's key must not end up written down, and there were two places it would have gone by default: LangGraph copies every string in configurable into checkpoint metadata, and task params are written to the lake. So the key travels in a ContextVar for the turn, a pool task is handed it as a call argument, and a test scans the checkpointer, the task snapshots and the data directory for it.

What this does not give you is a tenant boundary. The broker is shared — one ingress token, and anyone holding it can submit to every endpoint on it — so per-user isolation needs a broker per user, which is the next phase.`);
}

// ==================================================== 21. Running it
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "Running it, driving it, and what each tier proves", "Usability · as a component");

  code(s, [
    '$ ./scripts/setup.sh                       # builds .venv, then verifies it',
    '$ python -m designagent --reload           # :8000',
    '$ python -m designagent --check-config --probe   # config, each credential tried',
    '$ python -m designagent --add-user NAME --admin  # and --gen-secrets-key',
    '$ cd frontend && npm install && npm run dev      # :5173, proxies /api',
    '$ pytest -q                                # 417 tests, no network',
    '$ pytest -q -m live                        # 12 tests, starts a real broker',
    '$ pytest -q -m remote                      # 8 tests, a real endpoint',
  ], M, 1.55, 7.3, 1.52, { anchor: "VERBATIM", fs: 10 });

  card(s, M + 7.6, 1.55, 5.23, 1.72, "Driving it without the browser", [
    "22 HTTP routes: /api/chat is the SSE stream, the rest REST — sessions, tasks, artifacts, settings, catalog. Plus the CLI above.",
    "The record outlives the process: tiers 2 and 3 are a SQLite file and a Parquet file, read by run_model.py under the Kuzu lock.",
    "uv sync cannot work here — four editable installs from refcodes/, and flowgentic pins two of its deps to git URLs.",
  ], { fill: C.panel, fs: 9 });

  const rows = [
    ["tests/test_protocol_*.py", "203", "the validators between a chat message and a shell script, every job spec run under bash, the stage machine turn by turn"],
    ["tests/test_graph.py", "55", "18 classifier cases, a full loop end to end, artifacts, lake-write failure, no structures in state"],
    ["test_tasks · _jobspec · _proteinmpnn", "70", "the contract; the job spec, in-band staging and its ceiling, the FASTA adapter"],
    ["test_auth.py · test_settings.py", "46", "off, logins change nothing; a session belongs to its owner; everything writable is reported"],
    ["test_lake · _api · _artifacts", "43", "all three lake tiers against a tmp_path; SSE frames; cancel → 409 with a reason"],
    ["-m live · -m remote · -m llm", "12·8·4", "a broker and endpoint as subprocesses · a real scheduler · a real API key"],
  ];
  const cw = [3.4, 0.8, 8.13];
  const tbl = [["file", "n", "what it actually proves"].map(h => ({ text: h,
    options: { bold: true, color: C.white, fill: { color: C.task }, fontSize: 11.5 } }))]
    .concat(rows.map((r, i) => r.map((c, j) => ({ text: c, options: {
      fontFace: j === 0 ? MF : BF, fontSize: j === 0 ? 10.5 : 11.5,
      align: j === 1 ? "center" : "left", color: C.text,
      fill: { color: i % 2 ? C.white : C.panel } } }))));
  s.addTable(tbl, { x: M, y: 3.35, w: cw.reduce((a, b) => a + b), colW: cw,
    border: { type: "solid", color: C.rule, pt: 0.75 }, fontFace: BF, valign: "middle",
    rowH: [0.28, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4], margin: 0.06 });

  text(s, [
    { text: "The split that matters: ", options: { bold: true, color: C.ink } },
    { text: "417 tests need no network, no pool and no endpoint, because nodes only reach through " +
      "Deps. The 24 that do are marked " },
    { text: "live", options: { fontFace: MF } },
    { text: ", " },
    { text: "remote", options: { fontFace: MF } },
    { text: " and " },
    { text: "llm", options: { fontFace: MF } },
    { text: " — each deselected separately, so the cheap tier can never pull in one that needs an " +
      "allocation or spends money." },
  ], M, 6.5, 12.3, 0.6, { fontSize: 12.5 });
  s.addNotes(
`[1:00] How you actually use this, in all three senses. Four commands to run it, three to test it, and no configuration step that has to succeed first — with no API key every layer notes on the health endpoint what it could not do and keeps going.

The card on the right is the component claim made concrete. Nothing here needs the browser: twenty-two HTTP routes, of which the chat is the SSE stream and the rest are ordinary REST, plus a command line for config, credentials and accounts. And the record outlives the process — two of the three lake tiers are a SQLite file and a Parquet file, which is exactly how the script that mined the numbers for this deck read them while the server held the Kuzu lock.

The test split is the part I would defend. 417 of the 441 tests need no network, no process pool and no endpoint, which is a direct consequence of the Deps rule from the architecture slide: the suite hands a node an in-process task manager and a temp-directory lake, and the whole graph runs in seconds.

The twenty-four that need a substrate are split three ways and deselected separately: live brings up its own broker and endpoint as subprocesses, remote wants a real scheduler and an allocation, and llm spends money. Keeping them apart means the cheap tier can never drag in an expensive one. The live ones are not mocks of ORBIT; they are ORBIT, on localhost.

The top row is the protocol node, half the suite — deliberate, because it is the part with no endpoint to try it against, so the tests are the only thing holding it.`);
}

// ======================================================== 22. Status
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "What is real, and what is not — in all three", "Status · do not compress this slide");

  const cols = [
    ["FUNCTIONALITY", C.agent, [
      ["The campaign loop, all six nodes", "two redesign rounds, routed by the improvement test, on a real target"],
      ["All three lake tiers", `${T2.counts.scores} scores, ${T1.nodes.Task} task nodes, an ${T3.n_rows}-row Parquet set`],
      ["Artifacts and streaming", "Markdown, .docx, a sortable table, two Mol* specs — pane confirmed in a browser"],
    ], [
      ["No protocol stage has run on a cluster", "nine stages, 203 tests, zero real runs — specs.py is a guess"],
      ["A restart during a protocol run", "handles are in-memory: recovery is a re-fetch, nothing re-attaches"],
      ["ChemGraph", "wired behind the interface, exercised by tests, never by a campaign"],
    ]],
    ["PERFORMANCE", C.radical, [
      ["Work really leaves the process", "12 real ESMFold predictions on the rhapsody pool, reaped out of order"],
      ["Orbit against a real cluster", "2026-10-09: Slurm jobs through a public broker, logs, a cancel, native_id back"],
      ["ProteinMPNN, the real model", "v_48_020 on CPU through a real broker; designs carry its score and hash"],
    ], [
      ["No scaling study of any kind", "one host, a pool of four never varied, no multi-node, no occupancy"],
      ["No GPU of our own", "every AF3 walltime and memory figure is an estimate until a queue says otherwise"],
      ["wrap_nodes", "ships off, against the approved design. That is the ask on the next slide"],
    ]],
    ["USABILITY", C.ui, [
      ["As an application, deployed", "a VM behind Caddy, sign-in, the viewer driven in a real browser"],
      ["As a platform", "per-user logins and credentials encrypted at rest; each user's own endpoint"],
      ["As a component, in shape", "22 HTTP routes and a CLI; lake tiers 2 and 3 read with nothing running"],
    ], [
      ["A broker per user", "one shared ingress token, so HPC is not yet a tenant boundary"],
      ["Driven by anything but itself", "no second consumer — the component claim is the code's shape, not evidence"],
      ["The Globus adapter", "structurally complete, tested with an injected executor, never run live"],
    ]],
  ];

  cols.forEach(([name, col, real, notReal], i) => {
    const x = M + i * 4.28, w = 4.0;
    s.addShape(pres.shapes.RECTANGLE, { x, y: 1.44, w, h: 0.4,
      fill: { color: col }, line: { color: col } });
    text(s, name, x + 0.14, 1.44, w - 0.28, 0.4,
      { fontSize: 13, bold: true, color: C.white, valign: "middle", charSpacing: 1 });

    [[real, C.good, "Runs end to end against something real", 1.88],
     [notReal, C.fail, "Built, and has never run for real", 4.02]].forEach(
      ([rows, band, heading, y0]) => {
        s.addShape(pres.shapes.RECTANGLE, { x, y: y0, w, h: 0.28,
          fill: { color: band }, line: { color: band } });
        text(s, heading, x + 0.12, y0, w - 0.24, 0.28,
          { fontSize: 9.5, bold: true, color: C.white, valign: "middle" });
        rows.forEach(([h, b], j) => {
          const y = y0 + 0.28 + j * 0.6;
          s.addShape(pres.shapes.RECTANGLE, { x, y, w, h: 0.58,
            fill: { color: j % 2 ? C.white : (band === C.fail ? C.failTint : C.panel) },
            line: { color: C.rule, width: 0.5 } });
          text(s, h, x + 0.12, y + 0.01, w - 0.24, 0.21, { fontSize: 10, bold: true, color: C.ink });
          text(s, b, x + 0.12, y + 0.22, w - 0.24, 0.34, { fontSize: 8.5, color: C.muted });
        });
      });
  });

  text(s, [
    { text: "Three red columns as long as the three green ones, on purpose. ",
      options: { bold: true, color: C.fail } },
    { text: "The single most load-bearing caveat is still the first one: no stage of the protocol has " +
      "run on a cluster. The path under it works — real jobs, real logs, a real cancel — and the " +
      "performance column is the one I would attack if I were you." },
  ], M, 6.24, 12.33, 0.7, { fontSize: 12 });
  s.addNotes(
`[1:25] Same slide as before, cut three ways instead of two, so each dimension has to answer for itself. Green runs end to end against something real; red is built and has never run for real.

Functionality is the strongest column: the loop runs, all three tiers are written, the artifacts open. What has never run is the protocol on a cluster — two hundred and three tests, zero real runs — and nothing re-attaches to an in-flight job after a restart.

Performance: work genuinely leaves this process, ORBIT has run against a real cluster, ProteinMPNN is the real model. What is missing is every form of scaling evidence — one host, a pool of four I never varied, no multi-node run, no occupancy, no GPU of my own, which is why the AlphaFold3 figures are estimates.

Usability: deployed, holding other people's keys, drivable over HTTP and from a CLI. What is not there is a broker per user, so HPC is not yet a tenant boundary, and no second consumer has driven it as a component.

I made the red columns the same length as the green ones deliberately. If one of them were short you should be suspicious of it, and the one I would attack if I were in your seat is the middle one.`);
}

// ========================================================== 23. Asks
{
  const s = pres.addSlide(); s.background = { color: C.ink };
  text(s, "FINDINGS AND ASKS", M, 0.45, 9, 0.3,
    { fontSize: 12, bold: true, color: "8FB8C9", charSpacing: 2 });
  text(s, "Eight reproducibles, by what each one costs", M, 0.75, 12.3, 0.6,
    { fontFace: HF, fontSize: 28, bold: true, color: C.white });

  // Grouped by the dimension each one damages, which is also the order I would
  // fix them in. The numbers are the ones CODE_FOR_DECK.md reproduces, so they
  // keep their identity even though the column order is not 1..8.
  const groups = [
    ["A wrong answer, or real time lost", [
      ["3", "Orbit's terminal task event omits stdout", "functionality", "A completed task resolves with an empty result until get_task is called again. Worked around in _finish_task_enriched."],
      ["4", "A FAILED Orbit job carries no reason", "functionality", "Only a non-zero exit code reaches the client, so FAILED synthesises an explanation from code, then stderr, then the log tail."],
      ["5", "Neither outputs nor stdin_text is forwarded", "performance", "So stdout is a job's only channel for a file. Hence in-band staging — and the curve two slides back is what that costs."],
      ["2", "RetryConfig defaults cancel long tasks silently", "performance", "30 s per attempt, 3 attempts. This repo's own folds take 13–42 s, so half of round 2 would be cancelled and re-run."],
    ]],
    ["Paid by whoever adopts the stack next", [
      ["6", "asyncflow swallows SIGTERM", "usability", "It installs its own handler, reports a completed shutdown, and leaves the process running — so every stop has to escalate."],
      ["1", "flowgentic hard-imports aiohttp — and httpx", "usability", "fault_tolerance.py:71–72 and :83–84 are except Exception: raise where the comment says \"if present\"."],
      ["7", "The broker has no HTTP topology route", "usability", "/topology → 307 → 404, read as a plugin name. Readiness has to come from rt.topology(), which propagates asynchronously."],
      ["8", "--no-auth still requires cert and key", "usability", "It disables ingress auth only; the broker always serves TLS and refuses to start without a pair. One line in the docs."],
    ]],
  ];
  const TAG = { functionality: "7E96D8", performance: C.radical, usability: "B28ACD" };
  groups.forEach(([heading, items], col) => {
    const x = M + col * 6.5;
    s.addShape(pres.shapes.RECTANGLE, { x, y: 1.44, w: 6.14, h: 0.02,
      fill: { color: "4A5B68" }, line: { color: "4A5B68" } });
    text(s, heading.toUpperCase(), x, 1.5, 6.14, 0.26,
      { fontSize: 10, bold: true, color: "8FB8C9", charSpacing: 1.5 });
    items.forEach(([n, h, dim, b], i) => {
      const y = 1.84 + i * 1.0;
      s.addShape(pres.shapes.OVAL, { x, y, w: 0.32, h: 0.32,
        fill: { color: C.radical }, line: { color: C.radical } });
      text(s, n, x, y, 0.32, 0.32, { fontSize: 12, bold: true, color: C.white,
        align: "center", valign: "middle" });
      text(s, h, x + 0.44, y - 0.03, 4.6, 0.3, { fontSize: 12, bold: true, color: "F0C898" });
      text(s, dim, x + 5.1, y - 0.01, 1.04, 0.24,
        { fontSize: 8.5, bold: true, color: TAG[dim], align: "right", charSpacing: 0.5 });
      text(s, b, x + 0.44, y + 0.26, 5.7, 0.68, { fontSize: 9.5, color: "C8D4DD" });
    });
  });

  s.addShape(pres.shapes.RECTANGLE, { x: M, y: 5.86, w: 12.33, h: 1.16,
    fill: { color: "2A3A47" }, line: { color: C.radical, width: 2 } });
  text(s, [
    { text: "The question:  ", options: { bold: true, color: C.radical, fontSize: 15 } },
    { text: "is flowgentic's EXECUTION_BLOCK meant to preserve the caller's context?",
      options: { bold: true, color: C.white, fontSize: 15, breakLine: true } },
    { text: "A node wrapped as one runs on asyncflow's loop, outside LangGraph's runnable context. " +
      "get_stream_writer() raises \"Called get_config outside of a runnable context\" and every " +
      "custom event is dropped silently — the graph completes, the chat just goes quiet. " +
      "If yes, it is a bug and wrap_nodes=True becomes our default. If no, flowgentic's node " +
      "wrapping and LangGraph's streaming are mutually exclusive, and that belongs in the README.",
      options: { color: "C8D4DD", fontSize: 11 } },
  ], M + 0.2, 5.94, 11.9, 1.0, { fontSize: 12 });
  text(s, "Thanks. Repo: main @ 7e72b74 · slides/CODE_FOR_DECK.md carries every anchor and the reproduction for each finding.",
    M, H - 0.45, 12.3, 0.3, { fontSize: 10.5, color: "7E8F9C", italic: true });
  s.addNotes(
`[1:50] Eight things I had to work around in two weeks on your stack. Each names a file and a line and CODE_FOR_DECK.md has the reproduction, so none of it needs taking on my word. They are grouped by what they cost: the left column produces a wrong answer or burns real time, the right column is paid by whoever adopts the stack after me.

Left column first. Three, four and five are ORBIT, and they are the ones I would most like fixed, because all three produce a silent wrong answer rather than an error: an empty result, a failure with no reason, and declared outputs that never arrive. Five is the expensive one — stdout becomes a job's only channel for a file, so this repo carries a staging protocol whose cost you saw two slides ago, and I would delete all of it the day the field is forwarded. Two is the retry default, which is a judgement call rather than a bug, but the wrong one for a workload where some tasks are models.

The right column is cheaper. One is, I think, a one-character fix: raise wants to be pass. Six is asyncflow's SIGTERM handler, which reports a completed shutdown and leaves the process running, so every stop script escalates to SIGKILL on a process holding a database lock. Seven and eight are documentation — I lost an afternoon to --no-auth, because no auth meaning no TLS is a reasonable reading.

And then the question, which is the actual ask. The approved design for this project had every graph node wrapped as an EXECUTION_BLOCK. It ships disabled, because a wrapped node runs on asyncflow's loop, outside LangGraph's runnable context, so the stream writer raises and every status event is dropped — silently. The graph still completes. The chat just goes quiet.

So: is EXECUTION_BLOCK meant to preserve the caller's context? If it is, that is a bug worth fixing and I flip my default back. If it is not, then flowgentic's node wrapping and LangGraph's streaming are mutually exclusive, and I think that sentence belongs in the README, because I would have liked to read it.`);
}

// ============================================= B1. Backup: streaming
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "The streaming experiment, in full", "Backup");

  card(s, M, 1.55, 6.2, 2.72, "What was tried", [
    "The approved design: wrap every node as AsyncFlowType.EXECUTION_BLOCK with RetryConfig(timeout_sec=None, max_attempts=1), same as tasks.",
    "Symptom: the graph completed correctly and the chat showed no status lines at all. No error surfaced anywhere.",
    "Isolation: one plain node and one wrapped node, each calling get_stream_writer() and emitting one event.",
    "Plain node: writer obtained, event received by astream(stream_mode=[\"custom\"]).",
    "Wrapped node: RuntimeError \"Called get_config outside of a runnable context\"; zero events.",
  ], { fill: C.panel, fs: 10.5 });

  card(s, M + 6.5, 1.55, 6.33, 2.72, "Why", [
    "flowgentic's EXECUTION_BLOCK submits the body to the asyncflow WorkflowEngine, which runs it on its own loop.",
    "LangGraph's get_stream_writer() reads the runnable context, which is a contextvar set by the LangGraph executor around the node call.",
    "Crossing to another loop loses the contextvar. The writer cannot be found, and LangGraph's own helper degrades to a no-op rather than raising — which is why it is silent.",
    "This is not a flowgentic bug unless EXECUTION_BLOCK intends to propagate context. Hence the question, not the accusation.",
  ], { fill: C.agentTint, hc: C.agent, fs: 10.5 });

  code(s, [
    'def wrap_node(integration, fn):',
    '    """Wrap a node body as a flowgentic EXECUTION_BLOCK, if enabled."""',
    '    if integration is None:',
    '        return fn',
    '    return integration.execution_wrappers.asyncflow(',
    '        fn, flow_type=AsyncFlowType.EXECUTION_BLOCK, retry=node_retry_config())',
    '',
    '# build_graph(): integration is set to None unless settings.wrap_nodes',
  ], M, 4.45, 7.6, 1.5, { anchor: "graph/build.py:41–133  ·  TRIMMED", fs: 10 });

  card(s, M + 7.9, 4.45, 4.93, 1.5, "What ships", [
    "wrap_nodes=False, the code path kept and documented.",
    "Tasks are still wrapped — FUNCTION_TASK runs task bodies, which never touch the stream writer.",
    "So the pool is genuinely used; only node bodies stayed in-process.",
  ], { fill: C.failTint, hc: C.fail, fs: 10.5 });

  text(s, "Verified after the change: 11 status lines stream for one redesign turn.",
    M, 6.15, 12.3, 0.4, { fontSize: 12.5, italic: true, color: C.good });
  s.addNotes(
`Backup, for the EXECUTION_BLOCK discussion if it goes deep.

The approved plan said wrap every node. I did, and the graph worked perfectly while the chat went completely silent — no error, no warning, just no status.

Isolating it took one plain node and one wrapped node, each asking for a stream writer and emitting one event. The plain one works. The wrapped one raises "Called get_config outside of a runnable context" and emits nothing.

The mechanism: EXECUTION_BLOCK hands the body to asyncflow's engine, which runs it on its own loop. LangGraph's stream writer lives in a contextvar that its executor sets around the node call. Cross loops and you lose the contextvar. And LangGraph's helper degrades to a no-op rather than raising, which is exactly why the failure is invisible.

Important fairness point: this is only a flowgentic bug if EXECUTION_BLOCK intends to propagate caller context. I do not know that it does, which is why slide 18 asks rather than asserts.

What ships: wrap_nodes false, the path kept and documented. Tasks are still wrapped, so the process pool is genuinely in use — task bodies never touch the stream writer, so nothing is lost there.`);
}

// =========================================== B2. Backup: local Orbit
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "Standing up a local Orbit stack", "Backup");

  code(s, [
    '# broker: --no-auth disables INGRESS AUTH ONLY. TLS is always served,',
    '# and it refuses to start without a cert and key.',
    'openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem \\',
    '  -days 3 -subj /CN=127.0.0.1 \\',
    '  -addext subjectAltName=IP:127.0.0.1,DNS:localhost     # SAN must cover 127.0.0.1',
    'chmod 600 key.pem                                        # refuses a looser mode',
    '',
    'radical-orbit-broker.py --no-auth --host 127.0.0.1 --port N \\',
    '    --cert cert.pem --key key.pem',
    '',
    'RADICAL_ORBIT_RHAPSODY_BACKEND=concurrent \\',
    'radical-orbit-endpoint.py --name local -p rhapsody,psij \\',
    '    --url https://127.0.0.1:N --cert cert.pem',
  ], M, 1.55, 7.6, 2.5, { anchor: "tasks/hpc/local_orbit.py  ·  EDITED for the slide", fs: 10 });

  card(s, M + 7.9, 1.55, 4.93, 1.2, "Readiness", [
    "There is no HTTP topology route — /topology reads as a plugin name and 404s.",
    "The stack greps the endpoint's own log for registered as '<name>'.",
  ], { fill: C.failTint, hc: C.fail, fs: 10.5 });
  card(s, M + 7.9, 2.9, 4.93, 1.15, "Then the client decides", [
    "OrbitInterface.connect() polls rt.topology() until the endpoint appears, bounded by connect_timeout — topology propagates asynchronously.",
  ], { fill: C.panel, fs: 10.5 });

  card(s, M, 4.3, 6.2, 1.7, "Why subprocesses, not EmbeddedBroker", [
    "The embedded broker expects operator-placed credentials in ~/.radical/orbit, which a test must not create or touch.",
    "Subprocesses with a throwaway cert keep the whole thing inside tmp_path and leave no state behind.",
    "Cost: the stack has to detect readiness from logs, which is the fragile part of the fixture.",
  ], { fill: C.radicalTint, hc: C.radical, fs: 10.5 });
  card(s, M + 6.5, 4.3, 6.33, 1.7, "What the fixture then proves", [
    "An executable task runs and its stdout comes back (after the get_task re-fetch).",
    "A bash loop's output tails incrementally — log_offset advances, so it is not refetched whole.",
    "A job exiting 3 lands FAILED with a non-empty error.",
    "A sleeping job cancels while running, and reports CANCELED.",
  ], { fill: C.panel, fs: 10.5 });
  s.addNotes(
`Backup, for anyone who wants to reproduce the ORBIT work.

The thing that cost me most of an afternoon is the first comment. --no-auth disables the ingress token check only. The broker always serves TLS and refuses to start without a cert and key, so with --no-auth alone it exits at startup. The fixture generates a throwaway self-signed pair, with a SAN covering 127.0.0.1 because that is what the client connects to, and chmods the key to 600 because the broker rejects anything looser.

Readiness was the other trap. I went looking for an HTTP topology endpoint and got a 307 then a 404 saying endpoint 'topology' unknown — because the gateway reads it as a plugin name. There is no such route. So the fixture waits on the endpoint's own log line, and the authoritative check moved to where it belongs: the client polls rt.topology() until the endpoint appears, bounded by a timeout, because topology propagates asynchronously.

Subprocesses rather than the embedded broker, because the embedded one expects operator-placed credentials in the user's home directory and a test has no business creating those.

What it buys is the four assertions on the right, all against real processes.`);
}

// ================================================ B3. Backup: Globus
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "Designed for two, implemented for one", "Backup · the other remote backend");

  box(s, M + 3.2, 1.55, 6.7, 0.5, { title: "RemoteWorkflowInterface(TaskInterface)",
    sub: "   connect · connected · _track · get · _settle", line: C.task, fill: C.taskTint,
    fs: 12.5, sfs: 10, align: "left", valign: "middle", round: false });
  line(s, M + 5.0, 2.05, M + 4.2, 2.5, { color: C.task });
  line(s, M + 8.1, 2.05, M + 8.9, 2.5, { color: C.task, status: "planned" });

  box(s, M + 1.9, 2.5, 4.5, 1.0, { title: "OrbitInterface", sub:
    "\nrun against a localhost stack and a real cluster",
    line: C.radical, fill: C.radicalTint, fs: 13, sfs: 10.5, tc: C.radical });
  box(s, M + 6.9, 2.5, 4.5, 1.0, { title: "GlobusComputeInterface", sub:
    "\nstructurally complete, never met a live endpoint",
    line: C.muted, fill: C.white, fs: 13, sfs: 10.5, status: "planned" });

  card(s, M, 3.75, 6.3, 1.5, "What the abstraction cost", [
    "Almost nothing: connect/_track/_settle plus drain_logs is the whole shared surface, and it is 102 lines.",
    "The interesting work is backend-specific by nature — Orbit's push callbacks have no Globus analogue.",
    "So the ABC's job is to make the differences declarable rather than to hide them.",
  ], { fill: C.taskTint, hc: C.task, fs: 10.5 });

  code(s, [
    'capabilities = Capabilities(',
    '    supports_cancel=False,      # only before start; see cancel()',
    '    supports_log_stream=False,  # final stdout only',
    '    supports_push_events=False, # the future resolves; no',
    '                                # intermediate states',
    '    supports_staging=False,     # shared FS or Globus Transfer',
    ')',
  ], M + 6.6, 3.75, 6.23, 1.3, { anchor: "tasks/hpc/globus.py:31–36  ·  VERBATIM", fs: 10 });

  card(s, M + 6.6, 5.2, 6.23, 1.1, "Testable with no endpoint", [
    "GlobusRunner's executor_factory is injectable, so the suite passes a plain executor taking a callable.",
    "argv is a list run with no shell on that path; the real ShellFunction path uses shlex.join because its API takes a string. A test proves a metacharacter stays data.",
  ], { fill: C.panel, fs: 10.5 });

  text(s, [
    { text: "The claim I will defend: ", options: { bold: true, color: C.ink } },
    { text: "the capability flags are more valuable than the shared base class. They are what let " +
      "the manager and the UI behave correctly against a backend they were not written for." },
  ], M, 5.45, 6.3, 0.85, { fontSize: 12.5 });
  s.addNotes(
`[1:15] The brief said design for both Globus hpc-bridge and ORBIT, implement ORBIT first. So: one ABC, two implementations, and one of them still has never touched a live endpoint.

What the abstraction cost is worth saying, because "we abstracted over two backends" is usually a boast hiding a mess. It cost almost nothing, because the shared surface is tiny — connect, track, settle, and a log drain, 102 lines in total. The interesting work is irreducibly backend-specific: Orbit's push callbacks have no Globus analogue, and pretending otherwise would have meant inventing a polling shim nobody wanted.

So the base class's real job is not hiding differences. It is making them declarable, which is what the capability record on the right does. Globus Compute genuinely cannot tail a running task and genuinely cannot cancel after start; each flag carries the reason inline.

And it is testable without Globus at all, because hpc-bridge's runner takes an injectable executor factory. The suite hands it a plain executor. One detail there: argv is a list run with no shell on the test path, while the real ShellFunction API takes a string, so that path uses shlex.join — with a test proving a shell metacharacter stays data.

The claim I will defend is that the flags earned their keep and the base class merely did not get in the way.`);
}

// ===================================== B4. Backup: local task agents
{
  const s = pres.addSlide(); s.background = { color: C.white };
  title(s, "Local Task Agents: a spec, not generated code", "Backup · task agents");

  const flow = [
    ["design state\n+ prompt", C.agent], ["default_spec()", C.task],
    ["LLM proposes\n(optional)", C.ui], ["sanitize_spec()", C.fail],
    ["Mol* view spec\n(JSON)", C.lake],
  ];
  const fw = 2.3;
  flow.forEach(([t, col], i) => {
    const x = M + i * (fw + 0.25);
    box(s, x, 1.55, fw, 0.75, { title: t, line: col, fill: C.white, fs: 11.5 });
    if (i < flow.length - 1) line(s, x + fw + 0.02, 1.93, x + fw + 0.24, 1.93);
  });

  card(s, M, 2.6, 6.2, 2.0, "Why a spec and not generated JavaScript", [
    "The brief said the agent \"codes a browser-based visualization\". Shipping LLM-written JS into the viewer is an injection surface for a prompt.",
    "So the agent emits a constrained JSON spec — structures, highlights, representation, colours — and the React component is the only thing that touches Mol*.",
    "sanitize_spec repairs or drops every field. A bad generation degrades to a plain cartoon, never a broken pane.",
    "Same expressive range for what a reviewer actually asks for; no execution.",
  ], { fill: C.failTint, hc: C.fail, fs: 10.5 });

  code(s, [
    'def sanitize_spec(spec: Any, *, fallback: dict) -> dict:',
    '    """Validate an LLM-proposed spec, repairing or dropping',
    '    bad fields.',
    '',
    '    Anything unexpected falls back rather than reaching the',
    '    viewer, so a bad generation degrades to a plain cartoon',
    '    instead of a broken pane.',
    '    """',
    '    if not isinstance(spec, dict):',
    '        return fallback',
  ], M + 6.5, 2.6, 6.33, 1.85, { anchor: "tools/molviz_agent.py:143–150  ·  TRIMMED", fs: 10 });

  const rows = [
    ["Molecular Visualization Generator", "local", "Mol* view spec keyed from state + prompt", "runs; drew both leads in the measured campaign"],
    ["Cheminformatics Agent (ChemGraph)", "local", "ChemGraph single_agent workflow in a pool worker", "wired, optional extra, exercised by tests only"],
    ["ESMFold", "local → hpc", "ESM Atlas API under 400 aa, else a job", "12 real predictions on disk"],
    ["ProteinMPNN", "hpc", "PSI/J job spec + FASTA parser", "no endpoint → heuristic proposer instead"],
  ];
  const cw = [3.5, 1.15, 3.9, 3.78];
  const tbl = [["agent", "interface", "what it does", "status"].map(h => ({ text: h,
    options: { bold: true, color: C.white, fill: { color: C.task }, fontSize: 11 } }))]
    .concat(rows.map((r, i) => r.map((c, j) => ({ text: c, options: {
      fontFace: j === 1 ? MF : BF, fontSize: 11, bold: j === 0,
      color: j === 3 && i >= 2 ? (i === 3 ? C.fail : C.good) : C.text,
      fill: { color: i % 2 ? C.white : C.panel } } }))));
  s.addTable(tbl, { x: M, y: 4.72, w: cw.reduce((a, b) => a + b), colW: cw,
    border: { type: "solid", color: C.rule, pt: 0.75 }, fontFace: BF, valign: "middle",
    rowH: [0.3, 0.36, 0.36, 0.34, 0.36], margin: 0.07 });
  footer(s, "13 task bodies in the registry across the three interfaces; the catalog is served at /api/catalog so the UI can list what the agent can do.");
  s.addNotes(
`[1:10] The brief asked for Local Task Agents, and named two: a molecular visualization generator and ChemGraph.

The visualization one is where I deviated, and I want to be explicit about it. The brief says the agent "codes a browser-based visualization". Shipping LLM-written JavaScript into the viewer means a prompt can get code into the page, so I did not do that. The agent emits a constrained JSON view spec instead — structures, highlights, representation, colours — and a single React component is the only thing that ever touches Mol*. sanitize_spec repairs or drops every field, so a bad generation degrades to a plain cartoon rather than a broken pane.

I think that keeps the full expressive range of what a reviewer actually asks for — "colour the mutated residue, focus on it, show the rest as cartoon" — without executing anything.

The table is the honest status of all four. The visualizer runs and drew both leads in the measured campaign. ChemGraph is wired behind the same interface but has only ever been exercised by tests. ESMFold has twelve real predictions on disk. ProteinMPNN has a job spec and a FASTA parser and no endpoint, so what actually ran was the heuristic proposer — which is why the variants in this campaign are single-point substitutions.`);
}

pres.writeFile({ fileName: path.join(__dirname, "designagent-codewalk.pptx") })
  .then(f => console.log("wrote", f));
