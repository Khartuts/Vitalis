/** Set by android/android-bridge.js inside the phone app's WebView; absent in the Electron desktop app. */
export const IS_ANDROID = (globalThis as { __VITALIS_PLATFORM__?: string }).__VITALIS_PLATFORM__ === "android";

/** Screens on the phone (the Correlation Lab stays on the desktop for now). */
export const PHONE_ROUTES = new Set(["overview", "sleep", "recovery", "stress", "cardio", "temperature", "activity", "trends", "journal", "ai", "reports", "settings"]);
