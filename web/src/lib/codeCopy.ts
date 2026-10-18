// The sign-in code's words by channel (ADR 0028 as amended 2026-10-17; auth.code_channel). Telegram is the default:
// the code goes to the owner's goldbot chat. The mail words stay for the day mail is chosen. A reply never says
// whether the password was right, so the lines read "if the email and password are right".
export type CodeChannel = "telegram" | "mail";

export interface CodeCopy {
  choose: string;                                   // the radio and the button that ask for a code
  title: string;                                    // the code screen's heading
  sentLine: (maskedEmail: string) => string;        // where the code went, ahead of "It works once, for ..."
  wrong: (left: number) => string;
  partial: string;
  noMessage: string;
  accountOn: string;
  accountOff: string;
  setupHint: string;                                // after the owner account is created
}

export const CODE_COPY: Record<CodeChannel, CodeCopy> = {
  telegram: {
    choose: "Send me a code",
    title: "Enter the code we sent",
    sentLine: () => "If the email and password are right, a 6-digit code is on its way to your goldbot Telegram chat.",
    wrong: (left) => `That code did not work. Check the newest message from goldbot in Telegram. ${left} ${left === 1 ? "try" : "tries"} left for this code.`,
    partial: "Type all 6 digits from the message.",
    noMessage: "No message? Open your goldbot chat in Telegram, check the password you typed (we do not say whether it was right), or use the authenticator app.",
    accountOn: "Sign-in codes: on. You can sign in with your password and a code sent to your goldbot Telegram chat. Re-arm, auto mode, password change, recovery codes and managing users still need your authenticator.",
    accountOff: 'Sign-in codes: off. Your Telegram chat is not set up for codes on the server (RUNBOOK 2.10, "Sign-in code"). Sign-in uses your authenticator app.',
    setupHint: "Day-to-day sign-in can use a code sent to your goldbot Telegram chat once it is set up.",
  },
  mail: {
    choose: "Email me a code",
    title: "Enter the code we emailed",
    sentLine: (masked) => `If the email and password are right, a 6-digit code is on its way to ${masked}.`,
    wrong: (left) => `That code did not work. Check the newest email from goldbot. ${left} ${left === 1 ? "try" : "tries"} left for this code.`,
    partial: "Type all 6 digits from the email.",
    noMessage: "No email? Check the spam folder, check the password you typed (we do not say whether it was right), or use the authenticator app.",
    accountOn: "Email codes: on. You can sign in with your password and a code sent to your email. Re-arm, auto mode, password change, recovery codes and managing users still need your authenticator.",
    accountOff: 'Email codes: off. Mail is not set up on the server (RUNBOOK 2.10, "Email sign-in"). Sign-in uses your authenticator app.',
    setupHint: "Day-to-day sign-in can use a code sent to your email once mail is set up.",
  },
};
