const IST = "Asia/Kolkata";

const dateTime = new Intl.DateTimeFormat("en-IN", {
  timeZone: IST,
  dateStyle: "medium",
  timeStyle: "short",
});

export function formatIst(value: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "-" : dateTime.format(date);
}

export function formatPaise(paise: number): string {
  const sign = paise < 0 ? "-" : "";
  const abs = Math.abs(Math.trunc(paise));
  const rupees = Math.floor(abs / 100);
  const rest = String(abs % 100).padStart(2, "0");
  return `${sign}₹${rupees.toLocaleString("en-IN")}.${rest}`;
}

export function rupeesToPaise(input: string): number | null {
  const match = /^\s*(\d+)(?:\.(\d{1,2}))?\s*$/.exec(input);
  if (!match) return null;
  const rupees = Number(match[1]);
  const fraction = Number((match[2] ?? "").padEnd(2, "0") || "0");
  return rupees * 100 + fraction;
}

export function minutesLeft(due: string | null, now: number): number | null {
  if (!due) return null;
  return Math.floor((new Date(due).getTime() - now) / 60000);
}
