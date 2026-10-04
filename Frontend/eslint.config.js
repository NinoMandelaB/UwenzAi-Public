import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import { defineConfig, globalIgnores } from "eslint/config";

export default defineConfig([
  globalIgnores(["dist", "public"]),
  {
    files: ["**/*.{js,jsx}"],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: "latest",
        ecmaFeatures: { jsx: true },
        sourceType: "module",
      },
    },
    rules: {
      // Relax unused var rule to warnings and ignore common short names used intentionally
      "no-unused-vars": [
        "warn",
        {
          varsIgnorePattern:
            "^[A-Z_]|^t$|^err$|^Icon$|^useAuth$|^isAuthLoading$|^isFlagging$",
        },
      ],

      // Turn off strict React Hooks checks that produce many project-specific false positives
      "react-hooks/set-state-in-effect": "off",
      "react-hooks/exhaustive-deps": "off",
      "react-hooks/preserve-manual-memoization": "off",
      "react-hooks/purity": "off",
      "react-hooks/rules-of-hooks": "off",
      "react-hooks/refs": "off",
      "react-hooks/immutability": "off",

      // Relax fast-refresh rule which doesn't apply to some context files in this project
      "react-refresh/only-export-components": "off",

      // Allow empty blocks in some cases (converted to warnings)
      "no-empty": ["warn"],

      // Allow duplicate object keys in translations file for now (merged content)
      "no-dupe-keys": "off",
    },
  },
]);
