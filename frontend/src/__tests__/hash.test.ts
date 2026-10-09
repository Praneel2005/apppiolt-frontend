/**
 * Vitest test for canonicalSeriesHash.
 * Must pass every case in contracts/hash_vectors.json.
 */

import { describe, expect, it } from "vitest";
import vectors from "../../../contracts/hash_vectors.json";
import { canonicalSeriesHash } from "../lib/hash";

describe("canonicalSeriesHash", () => {
  for (const tc of vectors.cases) {
    it(`matches vector: ${tc.name}`, async () => {
      const hash = await canonicalSeriesHash(
        tc.rows as Record<string, unknown>[],
        tc.columns
      );
      expect(hash).toBe(tc.hash);
    });
  }
});
