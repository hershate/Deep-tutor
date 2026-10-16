// A running call with no output yet: its row opens, says it is still running and for how long, and names its own timeout.
import { chromium } from "playwright";
const fails = [];
const check = (name, ok, detail = "") => { console.log(`${ok ? "  ok" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`); if (!ok) fails.push(name); };
const [,, out, w] = process.argv;
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: +w || 1100, height: 700 } });
await p.goto((process.env.PERF_URL ?? "http://localhost:4521/perf.html") + "?pref=zh", { waitUntil: "networkidle" }); await p.waitForSelector(".app"); await p.waitForTimeout(400);
await p.evaluate(async () => {
  window.__feed({ kind: "turn_started", authoredTurn: 1, msgIndex: 0, text: "list them" });
  window.__feed({ kind: "text", text: "我来看一下目录。" });
  const cmd = 'cd /Users/me/Documents/AI项目/音频搜索 && for f in *.md; do echo "=== $f ($(wc -l < "$f") 行)"; head -20 "$f"; done; ls -la /Users/me/Documents/ ; echo ---; find /Users/me/Documents -maxdepth 3 -name "*.md" | head -50';
  window.__feed({ kind: "tool_dispatch", tool: { id: "b1", name: "bash", args: JSON.stringify({ command: cmd, timeout_seconds: 900 }) } });
    await new Promise((r) => setTimeout(r, 300));
});
await p.waitForTimeout(600);
if (out) await p.screenshot({ path: out + "-1.png" });
const row = p.locator(".activity-group .call").first();
check("the running row is a disclosure", (await row.locator("details.tool-disclosure").count()) === 1);
await row.locator("summary").click().catch(() => {});
await p.waitForTimeout(1500);
const g = await p.evaluate(() => ({ note: document.querySelector(".activity-call-wait")?.textContent ?? "", input: !!document.querySelector(".activity-call-input"), title: document.querySelector(".activity-group .arg")?.getAttribute("title") ?? "" }));
check("opened, it shows the whole command", g.input && g.title.includes("maxdepth 3"));
check("and says it is still running with no output, and for how long", /命令仍在运行/.test(g.note) && /已运行/.test(g.note), g.note);
check("and names the command's own timeout", /超时上限 900 秒/.test(g.note), g.note);
if (out) await p.screenshot({ path: out + "-2.png" });
await b.close();
console.log(fails.length ? `\n${fails.length} failed` : "\nall ok");
process.exit(fails.length ? 1 : 0);
