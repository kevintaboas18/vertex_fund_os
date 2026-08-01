
export function marketDateStr(now) {
  const f = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit" });
  return f.format(now);
}
