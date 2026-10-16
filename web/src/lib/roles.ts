import type { Role } from "./api";

// One spelling per role, used by the header, the Account tab and the Users tab (docs/ux/header-role.md):
// "the header says who is signed in by role, never by address".
const LABELS: Record<Role, string> = { owner: "Owner", approver: "Approver", viewer: "Viewer" };

export const roleLabel = (role: Role): string => LABELS[role];
