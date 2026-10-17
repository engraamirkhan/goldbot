import { createHmac } from "node:crypto";
import { existsSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { join } from "node:path";

/** RFC 6238 TOTP (SHA-1, 6 digits, 30 s), the same parameters goldbot/api/auth.py uses. */
export function totp(secretB32: string, t = Date.now()): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const ch of secretB32.replace(/=+$/, "").toUpperCase()) bits += alphabet.indexOf(ch).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g)!.map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(t / 1000 / 30)));
  const mac = createHmac("sha1", key).update(counter).digest();
  const off = mac[mac.length - 1] & 0x0f;
  const code = (mac.readUInt32BE(off) & 0x7fffffff) % 1_000_000;
  return code.toString().padStart(6, "0");
}

/** The server's TOTP clock for one e2e server. scripts/e2e_server.py (install_totp_clock) adds 30 s times the whole
 * number in `<state_dir>/totp_clock_steps` to the real time, so a test that needs another unused code moves that clock
 * forward one step instead of sleeping for real time to pass (TD-4: the sleeps were 30-60 s of a 90 s test budget).
 * The server still verifies every code, accepts each once and only within +-1 step of its clock. The file only grows. */
export function totpClock(stateDir: () => string) {
  const file = () => join(stateDir(), "totp_clock_steps");
  const lastStep = new Map<string, number>();
  const offset = () => (existsSync(file()) ? Number(readFileSync(file(), "utf8")) || 0 : 0);
  const now = () => Date.now() + offset() * 30_000;
  return {
    /** The server's current time in ms. */
    now,
    /** A code for a time step this secret has not used yet; steps the server clock forward when the current and next
     * are spent. One test at a time per server (the stories are serial), so nothing else holds a code meanwhile. */
    fresh(secretB32: string): string {
      const current = Math.floor(now() / 30_000);
      const step = Math.max(current, (lastStep.get(secretB32) ?? -Infinity) + 1);
      if (step > current + 1) {
        const tmp = `${file()}.${process.pid}.tmp`;
        writeFileSync(tmp, String(offset() + (step - current - 1)));
        renameSync(tmp, file());                                    // atomic: the server never reads half a number
      }
      lastStep.set(secretB32, step);
      return totp(secretB32, step * 30_000);
    },
    /** A six-digit code that is none of the codes the server would accept now (it allows one step either side). */
    wrong(secretB32: string): string {
      const valid = new Set([-1, 0, 1].map((k) => totp(secretB32, now() + k * 30_000)));
      for (let i = 0; ; i++) {
        const c = String(i).padStart(6, "0");
        if (!valid.has(c)) return c;
      }
    },
  };
}
