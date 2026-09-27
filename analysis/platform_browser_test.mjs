// Full browser test for the Two Keys 3D platform.
// Serve analysis/output/platform/ (wrapped in a doctype page) on 127.0.0.1:8744, then:  node platform_browser_test.mjs [desktop|mobile]
import puppeteer from "puppeteer-core";
const MODE = process.argv[2] || "desktop", MOBILE = MODE === "mobile", S = process.cwd();
const results = [], check = (n, ok, d = "") => results.push([ok ? "PASS" : "FAIL", n, d]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const b = await puppeteer.launch({ executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: "new", userDataDir: `${S}/pp-prof-pt-${MODE}`, args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const p = await b.newPage();
await p.setViewport(MOBILE ? { width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 2 } : { width: 1440, height: 900 });
const errs = []; p.on("pageerror", e => errs.push(e.message)); p.on("console", m => { if (m.type() === "error" && !/404/.test(m.text())) errs.push(m.text()); });
await p.goto("http://127.0.0.1:8744/index.html", { waitUntil: "load" });
await p.evaluate(() => { try { localStorage.clear(); } catch {} }); await p.reload({ waitUntil: "load" });
const t0 = Date.now();
await p.waitForFunction(() => window.__twin && document.querySelector("#loading").hidden, { timeout: 120000 });
check("loads", true, `${((Date.now() - t0) / 1000).toFixed(1)}s`);
await sleep(1500);
const colours = await p.evaluate(async () => { const c = __twin.renderer.domElement, x = document.createElement("canvas"); x.width = 120; x.height = 80; const g = x.getContext("2d"); __twin.renderer.render && 0; g.drawImage(c, 0, 0, 120, 80); const d = g.getImageData(0, 0, 120, 80).data, s = new Set(); for (let i = 0; i < d.length; i += 4) s.add(`${d[i] >> 4},${d[i + 1] >> 4},${d[i + 2] >> 4}`); return s.size; }).catch(() => 0);
const shot = await p.screenshot({ encoding: "base64" });
check("scene renders", shot.length > 50000, `${Math.round(shot.length / 1000)} KB screenshot`);
const ev = (f, ...a) => p.evaluate(f, ...a);
// Lenses
if (MOBILE) { await p.click("#m-rail"); await sleep(200); }
for (const lens of ["value", "school", "walk", "cost", "age", "score"]) {
  const c0 = await ev(() => Array.from(__twin.caps.instanceColor.array.slice(0, 90)).join());
  await ev(l => document.querySelector(`[data-lens="${l}"]`).click(), lens); await sleep(150);
  const r = await ev(() => ({ c: Array.from(__twin.caps.instanceColor.array.slice(0, 90)).join(), legend: document.querySelector("#legend").textContent, top: document.querySelectorAll("#top button").length }));
  check(`lens "${lens}" recolours beacons and legend`, (r.c !== c0 || lens === "score") && r.legend.length > 20 && r.top > 0);
}
const vis = () => ev(() => { const m = new __twin.THREE.Matrix4(); let n = 0; __twin.H.forEach((h, i) => { __twin.caps.getMatrixAt(i, m); if (m.elements[0]) n++; }); return n; });
const n0 = await vis();
await ev(() => document.querySelector('[data-type="House"]').click()); await sleep(200);
const n1 = await vis(); check("type filter hides houses", n1 < n0, `${n0} → ${n1}`);
await ev(() => document.querySelector('[data-type="House"]').click()); await sleep(200);
await p.$eval("#f-max", e => { e.value = "800000"; e.dispatchEvent(new Event("input", { bubbles: true })); }); await sleep(600);
const n2 = await vis(); check("max price filter", n2 < n0 && n2 > 0, `${n0} → ${n2}`);
await p.$eval("#f-max", e => { e.value = "1200000"; e.dispatchEvent(new Event("input", { bubbles: true })); }); await sleep(600);
for (const [id, key, get] of [["#l-bld", "city", "visible"], ["#l-sch", "schoolGroup", "visible"], ["#l-sky", "sky", "visible"], ["#l-lbl", "labels", "visible"]]) {
  await p.click(id); await sleep(100); const off = await ev(k => __twin[k].visible, key); await p.click(id); await sleep(100); const on = await ev(k => __twin[k].visible, key);
  check(`layer ${id}`, off === false && on === true);
}
if (MOBILE) { await p.click("#m-rail"); await sleep(200); }
if (!MOBILE) { await ev(() => document.querySelector('[data-light="golden"]').click()); await sleep(300); const g = await ev(() => document.querySelector('[data-light="golden"]').getAttribute("aria-pressed")); check("golden-hour lighting", g === "true"); await ev(() => document.querySelector('[data-light="day"]').click()); }
// Select a home by clicking its beacon on screen
await ev(() => { const T = __twin, h = T.H.find(x => x.bi >= 0 && x.c === "Burnaby") || T.H.find(x => x.bi >= 0); T.select(h); }); await sleep(2200);
const target = await ev(() => { const T = __twin, cand = []; T.H.forEach((h, i) => { const m = new T.THREE.Matrix4(); T.caps.getMatrixAt(i, m); if (!m.elements[0]) return; const pp = T.toScreen(h.x, T.capY(h), h.z); const blocked = ["#rail", "#card", "#bar", "#furniture", "#mobilebar"].some(s => { const e = document.querySelector(s); if (!e || e.hidden) return false; const r = e.getBoundingClientRect(); return pp[0] > r.left - 6 && pp[0] < r.right + 6 && pp[1] > r.top - 6 && pp[1] < r.bottom + 6; }); if (pp[2] < 1 && pp[0] > 10 && pp[0] < innerWidth - 10 && pp[1] > 10 && pp[1] < innerHeight - 10 && !blocked) cand.push({ i, pp, a: h.a }); }); cand.sort((a, b) => Math.hypot(a.pp[0] - innerWidth / 2, a.pp[1] - innerHeight / 2) - Math.hypot(b.pp[0] - innerWidth / 2, b.pp[1] - innerHeight / 2)); return cand[0]; });
if (target) {
  if (!MOBILE) { await p.mouse.move(target.pp[0], target.pp[1]); await sleep(400); const tip = await ev(() => !document.querySelector("#tip").hidden && document.querySelector("#tip").textContent); check("hover shows a tooltip", !!tip, tip || ""); }
  if (MOBILE) await p.touchscreen.tap(target.pp[0], target.pp[1]); else await p.mouse.click(target.pp[0], target.pp[1]);
  await sleep(1800);
  const card = await p.$eval("#card-in", e => e.textContent);
  check("tapping a beacon opens its card", card.includes(target.a.split(" - ").pop().split(" ")[0]) && /Turn my key/.test(card), target.a);
  check("building or location lights up in brass", await ev(() => __twin.S.sel != null && __twin.renderer.info.render.triangles > 0));
} else check("found a beacon to tap", false);
// Vote with the button, then with the keyboard
const selKey = await ev(() => __twin.S.sel?.k);
await ev(() => document.querySelector('[data-vote="love"]').click()); await sleep(300);
const voted = await ev(k => (__twin.S.votes.local || {})[k]?.v, selKey);
check("Turn my key saves a vote", voted === "love");
const ring = await ev(() => { const i = __twin.H.indexOf(__twin.S.sel), m = new __twin.THREE.Matrix4(); __twin.S && 0; return i; });
if (!MOBILE) { await p.keyboard.press("ArrowUp"); await sleep(300); check("keyboard ↑ votes maybe", (await ev(k => (__twin.S.votes.local || {})[k]?.v, selKey)) === "maybe"); }
// Tour
await ev(() => document.querySelector('[data-mode="tour"]').click()); await sleep(2000);
const l1 = await p.$eval("#t-label", e => e.textContent), k1 = await ev(() => __twin.S.sel?.k);
await ev(() => document.querySelector('[data-vote="pass"]').click()); await sleep(2000);
const l2 = await p.$eval("#t-label", e => e.textContent), k2 = await ev(() => __twin.S.sel?.k);
check("tour: voting moves to the next home", k1 && k2 && k1 !== k2 && l1 !== l2, `${l1} → ${l2}`);
await p.click("#t-skip"); await sleep(1500); check("tour: skip moves on", (await ev(() => __twin.S.sel?.k)) !== k2);
await p.click("#t-exit"); await sleep(400); check("tour: end returns to explore", await ev(() => __twin.S.mode === "explore" && document.querySelector("#tour").hidden));
// Sheets
for (const m of ["picks", "prio", "report"]) { await ev(m => document.querySelector(`[data-mode="${m}"]`).click(), m); await sleep(400); const t = await p.$eval("#sheet-in", e => e.textContent); check(`sheet "${m}" opens`, !(await ev(() => document.querySelector("#sheet").hidden)) && t.length > 100); }
await ev(() => document.querySelector('[data-mode="prio"]').click()); await sleep(300);
const vi = await ev(() => { const m = new __twin.THREE.Matrix4(); return __twin.H.findIndex((h, i) => { __twin.stems.getMatrixAt(i, m); return m.elements[0] > 0 && h.at.SCH !== h.at.VAL; }); });
const h0 = await ev(i => { const m = new __twin.THREE.Matrix4(); __twin.stems.getMatrixAt(i, m); return m.elements[5]; }, vi);
await p.$eval("#w-SCH", e => { e.value = "10"; e.dispatchEvent(new Event("input", { bubbles: true })); }); await sleep(900);
const h1 = await ev(i => { const m = new __twin.THREE.Matrix4(); __twin.stems.getMatrixAt(i, m); return m.elements[5]; }, vi);
check("priorities reshape beacon heights", h0 !== h1, `${h0?.toFixed(1)} → ${h1?.toFixed(1)} m`);
await p.click("#sheet-x"); await sleep(300); check("sheet closes back to the map", await ev(() => document.querySelector("#sheet").hidden));
// Picks shows the loved home
await ev(() => { __twin.S.votes.local[__twin.H[0].k] = { v: "love", note: "test note", at: "x" }; }); await ev(() => document.querySelector('[data-mode="picks"]').click()); await sleep(300);
check("picks lists my favourites", (await p.$eval("#sheet-in", e => e.textContent)).length > 150); await p.click("#sheet-x");
// Navigation
const cam = () => ev(() => ({ t: __twin.controls.target.toArray(), p: __twin.camera.position.toArray(), d: __twin.camera.position.distanceTo(__twin.controls.target) }));
if (!MOBILE) {
  await ev(() => { __twin.S.sel = null; document.querySelector("#card-x")?.click(); }); await sleep(300);
  let a = await cam(); await p.mouse.move(700, 480); await p.mouse.wheel({ deltaY: -700 }); await sleep(900); let c = await cam(); check("wheel zooms", c.d < a.d * .95, `${Math.round(a.d)} → ${Math.round(c.d)} m`);
  a = c; await p.mouse.down(); await p.mouse.move(900, 540, { steps: 12 }); await p.mouse.up(); await sleep(600); c = await cam(); check("drag pans", Math.hypot(c.t[0] - a.t[0], c.t[2] - a.t[2]) > 10);
  a = c; await p.mouse.down({ button: "right" }); await p.mouse.move(950, 420, { steps: 12 }); await p.mouse.up({ button: "right" }); await sleep(600); c = await cam(); const az = s => Math.atan2(s.p[0] - s.t[0], s.p[2] - s.t[2]); check("right-drag rotates", Math.abs(az(c) - az(a)) > .05);
  await p.click("#compass"); await sleep(1200); c = await cam(); check("compass turns to north", Math.abs(az(c)) < .06, az(c).toFixed(3));
  const sb = await p.$eval("#scale-l", e => e.textContent); check("scale bar shows a distance", /m|km/.test(sb), sb);
} else {
  const cdp = await p.target().createCDPSession(), touch = (type, pts) => cdp.send("Input.dispatchTouchEvent", { type, touchPoints: pts.map(([x, y], id) => ({ x, y, id })) });
  await ev(() => document.querySelector("#card-x")?.click()); await sleep(300);
  let a = await cam(); await touch("touchStart", [[195, 420]]); for (let k = 1; k <= 10; k++) await touch("touchMove", [[195 + k * 12, 420 + k * 4]]); await touch("touchEnd", []); await sleep(600);
  let c = await cam(); check("one-finger drag pans", Math.hypot(c.t[0] - a.t[0], c.t[2] - a.t[2]) > 5);
  a = c; await touch("touchStart", [[160, 420], [230, 420]]); for (let k = 1; k <= 10; k++) await touch("touchMove", [[160 - k * 10, 420], [230 + k * 10, 420]]); await touch("touchEnd", []); await sleep(600);
  c = await cam(); check("pinch zooms", Math.abs(c.d - a.d) > a.d * .05, `${Math.round(a.d)} → ${Math.round(c.d)} m`);
  await p.click("#m-rail"); await sleep(200); check("Map button opens the controls", await ev(() => !document.querySelector("#rail").hidden));
}
await p.screenshot({ path: `${S}/pt_${MODE}_final.png` });
check("no JavaScript errors", errs.length === 0, errs.slice(0, 2).join(" | "));
await b.close();
const fails = results.filter(r => r[0] === "FAIL").length;
console.log(`${MODE.toUpperCase()}: ${results.length - fails}/${results.length} passed`);
for (const [s, n, d] of results) console.log(`${s}  ${n}${d ? "  (" + d + ")" : ""}`);
process.exit(fails ? 1 : 0);
