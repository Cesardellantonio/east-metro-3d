// Full browser test for the East Metro Twin. Usage: node twin_fulltest.mjs [desktop|mobile]
import puppeteer from "puppeteer-core";
const S = process.cwd(), MODE = process.argv[2] || "desktop", MOBILE = MODE === "mobile";
const results = [];
const check = (name, ok, detail = "") => { results.push([ok ? "PASS" : "FAIL", name, detail]); };
const sleep = ms => new Promise(r => setTimeout(r, ms));

const browser = await puppeteer.launch({ executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: "new",
  userDataDir: S + "/pp-prof-" + MODE, args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const page = await browser.newPage();
await page.setViewport(MOBILE ? { width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 2 } : { width: 1440, height: 900 });
const errors = [];
page.on("pageerror", e => errors.push(e.message));
page.on("console", m => { if (m.type() === "error" && !/404/.test(m.text())) errors.push(m.text()); });
page.on("requestfailed", r => { if (!/favicon/.test(r.url())) errors.push("request failed " + r.url()); });

const t0 = Date.now();
await page.goto("http://127.0.0.1:8743/index.html", { waitUntil: "load", timeout: 60000 });
await page.waitForFunction(() => window.__twin && document.querySelector("#loading").hidden, { timeout: 120000 });
check("loads and hides the loading screen", true, `${((Date.now() - t0) / 1000).toFixed(1)}s`);
const scan = await page.$eval("#scan", e => e.textContent);
check("header shows listing, school and building counts", /541 listings/.test(scan) && /313 schools/.test(scan) && /15,190/.test(scan), scan);

async function pixelStats() {
  const b64 = await page.screenshot({ type: "png", encoding: "base64" });
  return page.evaluate(async b64 => {
    const img = new Image(); img.src = "data:image/png;base64," + b64; await img.decode();
    const c = document.createElement("canvas"); c.width = 200; c.height = 120; const x = c.getContext("2d");
    x.drawImage(img, 0, 0, 200, 120); const d = x.getImageData(0, 0, 200, 120).data; const set = new Set();
    for (let i = 0; i < d.length; i += 4) set.add((d[i] >> 4) + "," + (d[i + 1] >> 4) + "," + (d[i + 2] >> 4));
    return { colours: set.size, hash: Array.from(d.filter((_, i) => i % 97 === 0)).join(",").length + ":" + d.reduce((a, v, i) => (a + v * (i % 13)) % 1e9, 0) };
  }, b64);
}
await sleep(1500);
const base = await pixelStats();
check("scene renders a varied image (not blank)", base.colours > 60, `${base.colours} distinct colours`);
await page.screenshot({ path: `${S}/ft_${MODE}_overview.png` });

// Camera views
for (const v of ["North Shore", "Downtown", "Metrotown", "Brentwood", "New West", "Port Moody", "Coquitlam", "Lougheed", "Overview"]) {
  const before = await page.evaluate(() => __twin.controls.target.toArray());
  await page.evaluate(v => [...document.querySelectorAll("#views .chip")].find(b => b.textContent === v).click(), v);
  await sleep(1700);
  const after = await page.evaluate(() => __twin.controls.target.toArray());
  check(`camera view "${v}" flies the camera`, Math.hypot(after[0] - before[0], after[2] - before[2]) > 50 || v === "Overview", after.map(Math.round).join(","));
}
await page.evaluate(() => [...document.querySelectorAll("#views .chip")].find(b => b.textContent === "Metrotown").click());
await sleep(1700);

// Terrain
const terr = await page.evaluate(() => { const T = __twin, W = T.THREE; const xz = (lat, lon) => { const d = { lon0: -122.98, lat0: 49.255, mx: 72657.856, mz: 110540 }; return [(lon - d.lon0) * d.mx, -(lat - d.lat0) * d.mz]; };
  const at = (lat, lon) => Math.round(T.ground(...xz(lat, lon))); return { sfu: at(49.2781, -122.9199), grouse: at(49.3806, -123.0815), metro: at(49.2258, -123.0039), inlet: at(49.30, -123.05) }; });
check("terrain heights match landmarks (SFU, Grouse, Metrotown, Burrard Inlet)", terr.sfu > 250 && terr.grouse > 900 && terr.metro > 60 && terr.metro < 200 && terr.inlet === 0, JSON.stringify(terr));
await page.evaluate(() => [...document.querySelectorAll("#views .chip")].find(b => b.textContent === "North Shore").click()); await sleep(1800);
await page.screenshot({ path: `${S}/ft_${MODE}_mountains.png` });
// Everything sits on the ground: every visible pillar base equals the terrain height under it
const onGround = await page.evaluate(() => { const T = __twin, m = new T.THREE.Matrix4(); let bad = 0, n = 0;
  T.L.forEach((l, i) => { T.pillars.getMatrixAt(i, m); if (!m.elements[5]) return; n++; if (Math.abs(m.elements[13] - T.ground(l.x, l.z)) > .5) bad++; }); return { n, bad }; });
check("every pillar stands on the terrain", onGround.n > 0 && onGround.bad === 0, JSON.stringify(onGround));
if (MOBILE) { await page.click("#t-controls"); await sleep(200); }
const pillarY0 = await page.evaluate(() => { const m = new __twin.THREE.Matrix4(); const i = __twin.L.findIndex(l => l.area === "Port Moody"); __twin.pillars.getMatrixAt(i, m); return m.elements[13]; });
await page.select("#c-ex", "2.5"); await sleep(1500);
const ex = await page.evaluate(() => { const m = new __twin.THREE.Matrix4(); const i = __twin.L.findIndex(l => l.area === "Port Moody"); __twin.pillars.getMatrixAt(i, m); return { y: m.elements[13], cityOk: __twin.city.geometry.attributes.position.count > 0 }; });
check("terrain exaggeration lifts homes with the ground", ex.y > pillarY0 * 2 && ex.cityOk, `${Math.round(pillarY0)} → ${Math.round(ex.y)} m`);
await page.screenshot({ path: `${S}/ft_${MODE}_mountains_x25.png` });
await page.select("#c-ex", "1"); await sleep(1500);
await page.click("#l-ter"); await sleep(150); const tOff = await page.evaluate(() => __twin.terrain.visible);
await page.click("#l-ter"); await sleep(150); const tOn = await page.evaluate(() => __twin.terrain.visible);
check("terrain layer toggle hides and shows", tOff === false && tOn === true);
if (MOBILE) { await page.click("#t-controls"); await sleep(200); }

// Mobile: panels start closed and open with the header buttons
if (MOBILE) {
  const hidden0 = await page.evaluate(() => [document.querySelector("#controls").hidden, document.querySelector("#info").hidden]);
  check("mobile: panels start collapsed", hidden0[0] && hidden0[1]);
  await page.click("#t-controls"); await sleep(200);
  check("mobile: Layers button opens the controls", await page.evaluate(() => !document.querySelector("#controls").hidden));
  await page.click("#t-controls"); await sleep(200);
}

// Colour modes
if (MOBILE) { await page.click("#t-controls"); await sleep(200); }
for (const mode of ["gap", "school", "walk", "loc", "age", "ovr"]) {
  const c0 = await page.evaluate(() => Array.from(__twin.pillars.instanceColor.array.slice(0, 60)).join());
  await page.select("#c-color", mode); await sleep(250);
  const r = await page.evaluate(() => ({ c: Array.from(__twin.pillars.instanceColor.array.slice(0, 60)).join(), legend: document.querySelector("#legend").textContent, top: document.querySelector("#top-title").textContent, n: document.querySelectorAll("#top button").length }));
  check(`colour by "${mode}" recolours pillars, legend and Top 10`, (r.c !== c0 || mode === "ovr") && r.legend.length > 20 && r.n === 10, r.top);
}
// Height modes
for (const mode of ["ovr", "psf", "flat", "price"]) {
  const h0 = await page.evaluate(() => Array.from(__twin.pillars.instanceMatrix.array.slice(0, 64)).join());
  await page.select("#c-height", mode); await sleep(250);
  const h1 = await page.evaluate(() => Array.from(__twin.pillars.instanceMatrix.array.slice(0, 64)).join());
  check(`pillar height by "${mode}" changes heights`, h0 !== h1);
}
// Filters
await page.select("#f-type", "Townhouse"); await sleep(250);
const types = await page.evaluate(() => [...document.querySelectorAll("#top button")].map(b => b.textContent));
check("filter: type Townhouse leaves only townhouses in Top 10", types.length > 0 && types.every(t => /Townhouse/.test(t)), `${types.length} shown`);
await page.select("#f-type", "");
await page.$eval("#f-max", e => { e.value = "700000"; e.dispatchEvent(new Event("input")); }); await sleep(250);
const maxOk = await page.evaluate(() => __twin.L.filter((l, i) => { const m = new __twin.THREE.Matrix4(); __twin.pillars.getMatrixAt(i, m); return m.elements[5] > 0; }).every(l => l.price <= 700000));
const nVis = await page.evaluate(() => __twin.L.filter((l, i) => { const m = new __twin.THREE.Matrix4(); __twin.pillars.getMatrixAt(i, m); return m.elements[5] > 0; }).length);
check("filter: max price $700K hides pricier pillars", maxOk && nVis > 0, `${nVis} visible`);
await page.$eval("#f-max", e => { e.value = "1200000"; e.dispatchEvent(new Event("input")); });
const vis = () => page.evaluate(() => __twin.L.filter((l, i) => { const m = new __twin.THREE.Matrix4(); __twin.pillars.getMatrixAt(i, m); return m.elements[5] > 0; }).length);
const withClean = await vis();
await page.click("#f-clean"); await sleep(250);
const noClean = await vis();
check("filter: unticking 'hide leasehold/presale' shows more homes", noClean > withClean, `${withClean} → ${noClean}`);
await page.click("#f-clean"); await sleep(250);
// Layers
for (const [id, key] of [["#l-bld", "city"], ["#l-walk", "walkLayer"], ["#l-sch", "schoolMesh"], ["#l-sky", "sky"], ["#l-lbl", "labels"]]) {
  await page.click(id); await sleep(150);
  const off = await page.evaluate(k => __twin[k].visible, key);
  await page.click(id); await sleep(150);
  const on = await page.evaluate(k => __twin[k].visible, key);
  check(`layer toggle ${id} hides and shows`, off === false && on === true);
}
if (MOBILE) { await page.click("#t-controls"); await sleep(200); }

// Picking a pillar by tapping it on screen
await page.evaluate(() => [...document.querySelectorAll("#views .chip")].find(b => b.textContent === "Metrotown").click());
await sleep(1800);
const target = await page.evaluate(() => {
  const vw = innerWidth, vh = innerHeight, blocked = el => ["#controls", "#info", "header.bar", ".hint"].some(s => { const e = document.querySelector(s); if (!e || e.hidden) return false; const r = e.getBoundingClientRect(); return el[0] > r.left && el[0] < r.right && el[1] > r.top && el[1] < r.bottom; });
  const T = __twin; const cand = [];
  T.L.forEach((l, i) => { const m = new T.THREE.Matrix4(); T.pillars.getMatrixAt(i, m); const h = m.elements[5]; if (!h) return;
    const p = T.toScreen(l.x, T.ground(l.x, l.z) + h * .5, l.z); if (p[2] < 1 && p[0] > 20 && p[0] < vw - 20 && p[1] > 20 && p[1] < vh - 20 && !blocked(p)) cand.push({ i, p, d: Math.hypot(p[0] - vw / 2, p[1] - vh / 2), address: l.address }); });
  cand.sort((a, b) => a.d - b.d); return cand[0];
});
if (target) {
  await page.mouse.move(target.p[0], target.p[1]); await sleep(400);
  if (!MOBILE) { const tip = await page.evaluate(() => !document.querySelector("#tip").hidden && document.querySelector("#tip").textContent); check("hovering a pillar shows a tooltip", !!tip, tip || ""); }
  if (MOBILE) await page.touchscreen.tap(target.p[0], target.p[1]); else await page.mouse.click(target.p[0], target.p[1]);
  await sleep(500);
  const info = await page.$eval("#info", e => e.textContent);
  check("tapping a pillar opens its scorecard", info.includes(target.address.split(" - ").pop().split(" ")[0]) && /VAL/.test(info), target.address);
  const links = await page.$$eval("#info a", as => as.map(a => a.href));
  check("scorecard has listing and dashboard links", links.some(h => /rew\.ca\/properties/.test(h)) && links.some(h => /claude\.ai\/artifact/.test(h)));
} else check("found a pillar on screen to tap", false);

// Picking a school pin
const sch = await page.evaluate(() => {
  const T = __twin, vw = innerWidth, vh = innerHeight; let best = null;
  T.schools.forEach((s, i) => { const p = T.toScreen(s.x, T.ground(s.x, s.z) + 80, s.z); const d = Math.hypot(p[0] - vw / 2, p[1] - vh / 2); if (p[2] < 1 && p[0] > 300 && p[0] < vw - 320 && p[1] > 120 && p[1] < vh - 80 && (!best || d < best.d)) best = { s, p, d }; });
  if (!best && vw < 500) T.schools.forEach((s) => { const p = T.toScreen(s.x, T.ground(s.x, s.z) + 80, s.z); const d = Math.hypot(p[0] - vw / 2, p[1] - vh / 2); if (p[2] < 1 && p[0] > 20 && p[0] < vw - 20 && p[1] > 200 && p[1] < vh - 60 && (!best || d < best.d)) best = { s, p, d }; });
  return best;
});
if (sch) {
  if (MOBILE) { await page.evaluate(() => { document.querySelector("#info").hidden = true; }); await page.touchscreen.tap(sch.p[0], sch.p[1]); } else await page.mouse.click(sch.p[0], sch.p[1]);
  await sleep(500);
  const info = await page.$eval("#info", e => e.textContent);
  check("tapping a school pin shows the school", info.includes(sch.s.name), sch.s.name);
} else check("found a school pin on screen", false);

// Top 10 list flies to a home
if (MOBILE) { await page.click("#t-controls"); await sleep(200); }
const tgt0 = await page.evaluate(() => __twin.controls.target.toArray());
await page.evaluate(() => document.querySelectorAll("#top button")[2].click()); await sleep(1700);
const tgt1 = await page.evaluate(() => __twin.controls.target.toArray());
const topInfo = await page.$eval("#info", e => e.textContent);
check("Top 10 item flies to the home and opens it", Math.hypot(tgt1[0] - tgt0[0], tgt1[2] - tgt0[2]) > 1 && /VAL/.test(topInfo));
await page.screenshot({ path: `${S}/ft_${MODE}_selected.png` });
if (MOBILE) { await page.evaluate(() => { document.querySelector("#info").hidden = true; document.querySelector("#controls").hidden = true; }); }

// Mouse / touch navigation
const camState = () => page.evaluate(() => ({ t: __twin.controls.target.toArray(), p: __twin.camera.position.toArray(), d: __twin.camera.position.distanceTo(__twin.controls.target) }));
const cx = MOBILE ? 195 : 720, cy = MOBILE ? 500 : 450;
if (!MOBILE) {
  let a = await camState();
  await page.mouse.move(cx, cy); await page.mouse.wheel({ deltaY: -600 }); await sleep(900);
  let b = await camState(); check("scroll wheel zooms in", b.d < a.d * .95, `${Math.round(a.d)} → ${Math.round(b.d)} m`);
  a = b; await page.mouse.move(cx, cy); await page.mouse.down(); await page.mouse.move(cx + 200, cy + 60, { steps: 12 }); await page.mouse.up(); await sleep(700);
  b = await camState(); check("left-drag pans the map", Math.hypot(b.t[0] - a.t[0], b.t[2] - a.t[2]) > 20);
  a = b; await page.mouse.move(cx, cy); await page.mouse.down({ button: "right" }); await page.mouse.move(cx + 220, cy - 80, { steps: 12 }); await page.mouse.up({ button: "right" }); await sleep(700);
  b = await camState();
  const az = s => Math.atan2(s.p[0] - s.t[0], s.p[2] - s.t[2]);
  check("right-drag rotates / tilts the view", Math.abs(az(b) - az(a)) > .05 || Math.abs(b.p[1] - a.p[1]) > 20);
} else {
  const client = await page.target().createCDPSession();
  const touch = async (type, pts) => client.send("Input.dispatchTouchEvent", { type, touchPoints: pts.map(([x, y], id) => ({ x, y, id })) });
  let a = await camState();
  await touch("touchStart", [[cx, cy]]); for (let k = 1; k <= 10; k++) await touch("touchMove", [[cx + k * 15, cy + k * 5]]); await touch("touchEnd", []); await sleep(700);
  let b = await camState(); check("one-finger drag pans the map", Math.hypot(b.t[0] - a.t[0], b.t[2] - a.t[2]) > 10);
  a = b; await touch("touchStart", [[cx - 40, cy], [cx + 40, cy]]); for (let k = 1; k <= 10; k++) await touch("touchMove", [[cx - 40 - k * 12, cy], [cx + 40 + k * 12, cy]]); await touch("touchEnd", []); await sleep(700);
  b = await camState(); check("two-finger pinch zooms", Math.abs(b.d - a.d) > a.d * .05, `${Math.round(a.d)} → ${Math.round(b.d)} m`);
}

// Resize
if (!MOBILE) {
  await page.setViewport({ width: 1024, height: 700 }); await sleep(600);
  const size = await page.evaluate(() => [__twin.renderer.domElement.clientWidth, __twin.renderer.domElement.clientHeight, __twin.camera.aspect]);
  check("resizing the window resizes the 3D view", size[0] === 1024 && size[1] === 700 && Math.abs(size[2] - 1024 / 700) < .01, size.join(" × "));
}
check("no JavaScript errors during the whole run", errors.length === 0, errors.slice(0, 3).join(" | "));
await browser.close();
const fails = results.filter(r => r[0] === "FAIL").length;
console.log(`\n${MODE.toUpperCase()}: ${results.length - fails}/${results.length} passed`);
for (const [s, n, d] of results) console.log(`${s}  ${n}${d ? "  (" + d + ")" : ""}`);
process.exit(fails ? 1 : 0);
