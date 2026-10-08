import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { canonicalSeriesHash, cell } from "./hash.mjs";

const vec = JSON.parse(readFileSync(new URL("../hash_vectors.json", import.meta.url), "utf8"));

for (const c of vec.cases) {
  test(`vector: ${c.name}`, async () => {
    assert.equal(await canonicalSeriesHash(c.rows, c.columns), c.hash);
  });
}

test("tie rounding is half-up in micro-units (not toFixed)", () => {
  assert.equal(cell(0.0078125), "7813");
  assert.equal(cell(-0.0078125), "-7813");
  assert.equal(cell(1.5), "1500000");
  assert.equal(cell(-0.0), "0");
  assert.equal(cell(-0.0000001), "0");
});
