import type { Page } from "@playwright/test";

/** The screens by their pre-v2 names, mapped to the v2 destination and sub-tab (docs/ux/dashboard-v2.md section 2). */
const WHERE: Record<string, [string, string?]> = {
  // trades redesign 2026-10-27: Open -> Now, Activity -> List, Summary -> Today (docs/design/trades-ux-2026-10-27.md)
  Overview: ["Home"], Approvals: ["Trades"], "Trades open": ["Trades", "Now"], "Trades activity": ["Trades", "List"],
  "Trades summary": ["Trades", "Today"], "Trades now": ["Trades", "Now"], "Trades list": ["Trades", "List"],
  "Trades strategies": ["Trades", "Strategies"], "Trades today": ["Trades", "Today"], Limits: ["Limits"],
  Health: ["Health"],   // Research, Progress, Agents, Feeds and News were retired on 2026-11-02 (owner)
  "Entry mode": ["Settings", "Entry mode and halt"], Account: ["Settings", "Your sign-in"], Users: ["Settings", "Users"],
};

/** Open a screen the way a person would: the rail on a desktop, the bottom bar on a phone (all five sections fit). */
export async function go(page: Page, name: string) {
  const [section, tab] = WHERE[name] ?? [name];
  await page.locator(`[data-nav-item="${section}"]:visible`).first().click();
  if (tab) await page.getByRole("tab", { name: tab, exact: true }).click();
}
