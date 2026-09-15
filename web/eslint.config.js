// Lint rules: ESLint's recommended set, TypeScript's, and React's rules of hooks - nothing
// stylistic. Formatting is left to the editor; these catch bugs.
import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig(
  {
    // schema.d.ts is generated from the backend's OpenAPI document and never edited by hand.
    ignores: ["dist", "node_modules", "src/api/schema.d.ts", "playwright-report", "test-results"],
  },
  js.configs.recommended,
  tseslint.configs.recommended,
  reactHooks.configs.flat.recommended,
  {
    languageOptions: { globals: globals.browser },
  },
  {
    files: ["*.config.{js,ts}", "e2e/**/*.ts"],
    languageOptions: { globals: globals.node },
  },
);
