import { describe, expect, it } from "vitest";
import { formatIst, formatPaise, minutesLeft, rupeesToPaise } from "../format";
import { parseList } from "../pages/Policy";

describe("format", () => {
  it("formats paise without floats", () => {
    expect(formatPaise(149950)).toBe("₹1,499.50");
    expect(formatPaise(5)).toBe("₹0.05");
    expect(formatPaise(-250)).toBe("-₹2.50");
  });

  it("parses rupee input to integer paise", () => {
    expect(rupeesToPaise("12")).toBe(1200);
    expect(rupeesToPaise("12.5")).toBe(1250);
    expect(rupeesToPaise("0.07")).toBe(7);
    expect(rupeesToPaise("abc")).toBeNull();
    expect(rupeesToPaise("1.234")).toBeNull();
  });

  it("shows time in IST", () => {
    expect(formatIst("2026-01-01T00:00:00Z")).toContain("5:30");
    expect(formatIst(null)).toBe("-");
    expect(formatIst("garbage")).toBe("-");
  });

  it("computes minutes left", () => {
    const now = Date.parse("2026-01-01T10:00:00Z");
    expect(minutesLeft("2026-01-01T10:30:00Z", now)).toBe(30);
    expect(minutesLeft("2026-01-01T09:00:00Z", now)).toBe(-60);
    expect(minutesLeft(null, now)).toBeNull();
  });

  it("splits comma lists", () => {
    expect(parseList(" a, b ,,c ")).toEqual(["a", "b", "c"]);
  });
});
