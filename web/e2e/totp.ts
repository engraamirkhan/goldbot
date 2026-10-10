import { createHmac } from "node:crypto";

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

const lastStep = new Map<string, number>();

/** A code for a time step this secret has not used yet: the server accepts each code once (replay guard) and only
 * within +-1 step, so when the current and next steps are spent this waits for the clock to move on. */
export async function freshTotp(secretB32: string): Promise<string> {
  const used = lastStep.get(secretB32) ?? -Infinity;
  let now = Math.floor(Date.now() / 30_000);
  const step = Math.max(now, used + 1);
  while (step > now + 1) {
    await new Promise((r) => setTimeout(r, 1000));
    now = Math.floor(Date.now() / 30_000);
  }
  lastStep.set(secretB32, step);
  return totp(secretB32, step * 30_000);
}
