import js from '@eslint/js';
import jsxA11y from 'eslint-plugin-jsx-a11y';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist', 'node_modules', 'playwright-report', 'test-results', 'coverage', '*.config.js'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommendedTypeChecked],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2023,
      globals: { ...globals.browser, ...globals.node },
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      'react-hooks': reactHooks,
      'jsx-a11y': jsxA11y,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      ...jsxA11y.flatConfigs.recommended.rules,
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      '@typescript-eslint/no-misused-promises': ['error', { checksVoidReturn: { attributes: false } }],
      '@typescript-eslint/only-throw-error': 'off',
      // Safe rendering (PLAN.md 13.19): agent/provider content is always text.
      'no-restricted-syntax': [
        'error',
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: 'Raw HTML is never rendered. Render agent and provider content as text.',
        },
        {
          selector: "AssignmentExpression[left.property.name='innerHTML']",
          message: 'Raw HTML is never rendered.',
        },
        {
          selector: "AssignmentExpression[left.property.name='outerHTML']",
          message: 'Raw HTML is never rendered.',
        },
      ],
      'no-restricted-globals': ['error', { name: 'eval', message: 'No eval (CSP forbids it).' }],
      // The browser never talks to providers, the model server or the runtime daemon.
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            { group: ['openai', 'openai/*'], message: 'No provider SDKs in the browser.' },
            { group: ['@anthropic-ai/*'], message: 'No provider SDKs in the browser.' },
            { group: ['twilio', 'twilio/*'], message: 'No provider SDKs in the browser.' },
            { group: ['googleapis', '@google-cloud/*', 'google-auth-library'], message: 'No provider SDKs in the browser.' },
          ],
        },
      ],
      // Labels in this app wrap their inputs or use htmlFor via the Field component.
      'jsx-a11y/label-has-associated-control': ['error', { assert: 'either', depth: 3 }],
      'jsx-a11y/no-autofocus': 'off',
      // Scrollable regions (logs, raw JSON, conversation) must be keyboard-focusable.
      'jsx-a11y/no-noninteractive-tabindex': ['error', { tags: ['pre', 'ol'], roles: ['log', 'region', 'tabpanel', 'alert'] }],
    },
  },
  {
    files: ['mock/**/*.ts', 'e2e/**/*.ts', 'scripts/**/*.ts', '*.config.ts'],
    rules: {
      'no-restricted-syntax': 'off',
      '@typescript-eslint/require-await': 'off',
    },
  },
  {
    files: ['**/*.test.{ts,tsx}', 'src/test/**'],
    rules: {
      '@typescript-eslint/unbound-method': 'off',
      '@typescript-eslint/no-non-null-assertion': 'off',
      '@typescript-eslint/require-await': 'off',
    },
  },
);
