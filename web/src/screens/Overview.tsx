// Compatibility shim: App.tsx (slice A) imports Overview until it switches to Home; delete then.
export { Home as Overview } from "./Home";
