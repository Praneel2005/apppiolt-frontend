import { chromium } from "playwright";

async function main() {
  console.log("Launching Chromium...");
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  page.on("console", (msg) => console.log(`[BROWSER CONSOLE] ${msg.type()}: ${msg.text()}`));
  page.on("pageerror", (err) => console.error(`[BROWSER ERROR] ${err.message}`));

  console.log("Navigating to http://localhost:5173/ ...");
  await page.goto("http://localhost:5173/", { waitUntil: "networkidle" });

  let sessionId = null;
  for (let i = 0; i < 40; i++) {
    sessionId = await page.evaluate(() => {
      const store = window.__APP_STORE__;
      return store ? store.getState().sessionId : null;
    });
    if (sessionId) break;
    await new Promise((r) => setTimeout(r, 250));
  }

  console.log(`Active session_id in store: ${sessionId}`);
  if (!sessionId) {
    console.error("FAIL: Did not get session_id from store");
    await browser.close();
    process.exit(1);
  }

  // ── TEST 1: Normal state -> verify.ok = true ──
  console.log("\n[TEST 1] Testing normal render_ack verification...");
  const debugPayload = {
    session_id: sessionId,
    state: {
      route: "/sales/revenue-by-customer-state",
      page_id: "sales.revenue_by_customer_state",
      filters: {
        customer_region: { op: "in", value: ["Southeast"] },
      },
      date_range: {
        from: "2018-04-01",
        to: "2018-06-30",
        preset: "last_quarter",
      },
    },
  };

  const resp1 = await fetch("http://localhost:8000/api/debug/apply_state", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(debugPayload),
  });
  const res1 = await resp1.json();
  if (!res1.verify || res1.verify.ok !== true) {
    console.error("FAIL: Test 1 verify.ok was not true:", res1);
    await browser.close();
    process.exit(1);
  }
  console.log("✓ TEST 1 PASSED: verify.ok is true!");

  // ── TEST 2: Fault injection (drop_filter) -> verify.ok = false ──
  console.log("\n[TEST 2] Testing fault injection (drop_filter)...");
  await fetch("http://localhost:8000/api/debug/fault", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: "drop_filter" }),
  });

  const resp2 = await fetch("http://localhost:8000/api/debug/apply_state", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(debugPayload),
  });
  const res2 = await resp2.json();

  // Reset fault to none
  await fetch("http://localhost:8000/api/debug/fault", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: "none" }),
  });

  if (res2.verify && res2.verify.ok === false && res2.verify.mismatches.length > 0) {
    console.log(`✓ TEST 2 PASSED: Mismatch detected as expected (${res2.verify.mismatches.length} mismatches):`);
    console.log(JSON.stringify(res2.verify.mismatches, null, 2));
  } else {
    console.error("FAIL: Test 2 expected mismatch but got:", res2);
    await browser.close();
    process.exit(1);
  }

  console.log("\n=======================================================");
  console.log("ALL VERIFICATION CHECKS PASSED PERFECTLY!");
  console.log("=======================================================");
  await browser.close();
  process.exit(0);
}

main().catch((err) => {
  console.error("Error in test:", err);
  process.exit(1);
});
