/** Set by android/android-bridge.js inside the phone app's WebView; absent in the Electron desktop app. */
export const IS_ANDROID = (globalThis as { __VITALIS_PLATFORM__?: string }).__VITALIS_PLATFORM__ === "android";

/** Screens in the first phone version (the rest follow in later steps). */
export const PHONE_ROUTES = new Set(["overview", "sleep", "recovery", "stress", "cardio", "temperature", "activity", "trends", "journal", "settings"]);
