import type { Page } from "@playwright/test";

/** The screens by their pre-v2 names, mapped to the v2 destination and sub-tab (docs/ux/dashboard-v2.md section 2). */
const WHERE: Record<string, [string, string?]> = {
  // trades redesign 2026-10-27: Open -> Now, Activity -> List, Summary -> Today (docs/design/trades-ux-2026-10-27.md)
  Overview: ["Home"], Approvals: ["Trades"], "Trades open": ["Trades", "Now"], "Trades activity": ["Trades", "List"],
  "Trades summary": ["Trades", "Today"], "Trades now": ["Trades", "Now"], "Trades list": ["Trades", "List"],
  "Trades strategies": ["Trades", "Strategies"], "Trades today": ["Trades", "Today"], Limits: ["Limits"],
  Research: ["Research", "Today and trials"], Progress: ["Research", "Road to live"], Agents: ["Research", "Strategies"],
  Feeds: ["Feeds", "Data feeds"], News: ["Feeds", "News"], Health: ["Feeds", "System health"],
  "Entry mode": ["Settings", "Entry mode and halt"], Account: ["Settings", "Your sign-in"], Users: ["Settings", "Users"],
};

/** Open a screen the way a person would: the rail on a desktop, the bottom bar (and More) on a phone. */
export async function go(page: Page, name: string) {
  const [section, tab] = WHERE[name] ?? [name];
  const item = page.locator(`[data-nav-item="${section}"]:visible`);
  if (!(await item.count())) await page.getByRole("button", { name: "More", exact: true }).click();
  await page.locator(`[data-nav-item="${section}"]:visible`).first().click();
  if (tab) await page.getByRole("tab", { name: tab, exact: true }).click();
}
