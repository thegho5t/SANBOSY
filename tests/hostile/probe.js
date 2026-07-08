const fs = require("fs");
// fs escape
for (const p of ["/etc/passwd", "/newfile", "/usr/evil"]) {
  try { fs.writeFileSync(p, "x"); console.log("WROTE", p, "-- FAIL"); }
  catch (e) { console.log("write blocked", p, ":", e.code); }
}
try { fs.writeFileSync("/box/ok", "x"); console.log("box writable: OK"); }
catch (e) { console.log("box FAILED:", e.code); }
// network
const net = require("net");
const s = net.connect(80, "1.1.1.1");
s.setTimeout(2000);
s.on("connect", () => console.log("NETWORK OPEN -- FAIL"));
s.on("error", (e) => console.log("network blocked:", e.code));
s.on("timeout", () => { console.log("network blocked: TIMEOUT"); s.destroy(); });
