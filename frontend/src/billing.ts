import type { BillingFlag, PaymentStatus } from "./api/types";
import { t, type Key } from "./i18n";

const IST = "Asia/Kolkata";

export const GST_STATES: [string, string][] = [
  ["01", "Jammu & Kashmir"],
  ["02", "Himachal Pradesh"],
  ["03", "Punjab"],
  ["04", "Chandigarh"],
  ["05", "Uttarakhand"],
  ["06", "Haryana"],
  ["07", "Delhi"],
  ["08", "Rajasthan"],
  ["09", "Uttar Pradesh"],
  ["10", "Bihar"],
  ["11", "Sikkim"],
  ["12", "Arunachal Pradesh"],
  ["13", "Nagaland"],
  ["14", "Manipur"],
  ["15", "Mizoram"],
  ["16", "Tripura"],
  ["17", "Meghalaya"],
  ["18", "Assam"],
  ["19", "West Bengal"],
  ["20", "Jharkhand"],
  ["21", "Odisha"],
  ["22", "Chhattisgarh"],
  ["23", "Madhya Pradesh"],
  ["24", "Gujarat"],
  ["26", "Dadra & Nagar Haveli and Daman & Diu"],
  ["27", "Maharashtra"],
  ["29", "Karnataka"],
  ["30", "Goa"],
  ["31", "Lakshadweep"],
  ["32", "Kerala"],
  ["33", "Tamil Nadu"],
  ["34", "Puducherry"],
  ["35", "Andaman & Nicobar Islands"],
  ["36", "Telangana"],
  ["37", "Andhra Pradesh"],
  ["38", "Ladakh"],
  ["97", "Other Territory"],
  ["99", "Centre Jurisdiction"],
];

export function stateName(code: string): string {
  const found = GST_STATES.find(([value]) => value === code);
  return found ? `${found[1]} (${code})` : code || "-";
}

export function previousMonthIst(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: IST, year: "numeric", month: "2-digit" }).formatToParts(now);
  const year = Number(parts.find((p) => p.type === "year")?.value);
  const month = Number(parts.find((p) => p.type === "month")?.value);
  const total = year * 12 + (month - 1) - 1;
  return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, "0")}`;
}

export function paiseToInput(paise: number): string {
  const abs = Math.abs(Math.trunc(paise));
  return `${paise < 0 ? "-" : ""}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
}

export function isPositiveDecimal(value: string, maxDecimals = 4): boolean {
  const match = /^\s*(\d{1,8})(?:\.(\d+))?\s*$/.exec(value);
  if (!match) return false;
  if ((match[2] ?? "").length > maxDecimals) return false;
  return Number(value) > 0;
}

export function flagText(flag: BillingFlag): string {
  const text = t[`billFlag_${flag.code}` as Key] ?? flag.code;
  return flag.detail ? `${text} (${flag.detail})` : text;
}

export function tokens(value: number): string {
  return value.toLocaleString("en-IN");
}

export function paymentStatusTone(status: PaymentStatus): "ok" | "warn" | "urgent" {
  if (status === "paid") return "ok";
  if (status === "overdue" || status === "cancelled") return "urgent";
  return "warn";
}
